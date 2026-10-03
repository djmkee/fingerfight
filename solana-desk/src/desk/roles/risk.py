"""Risk: pass or fail from numbers stored on the lead row. A fail closes the lead.

Risk first fetches mint and holder data and writes it to the row, then decides by reading the
row back, so every claim it makes is a stored tool result rather than something it made up.
"""

import json

from ..db import Lead, Status
from ..guards import MintMismatch, check_mint
from ..policy import Policy
from ..tools.base import ToolError
from .base import Role

Failure = tuple[str, str]  # (code, detail)

_MINT_FIELDS = ("mint_authority", "freeze_authority", "is_initialized", "extensions")


def evaluate(lead: Lead, policy: Policy) -> tuple[list[Failure], list[str]]:
    """Coded Risk checks over the stored row. Returns (failures, checks passed)."""
    failures: list[Failure] = []
    passed: list[str] = []

    def check(ok: bool, code: str, failed: str, ok_text: str) -> None:
        if ok:
            passed.append(ok_text)
        else:
            failures.append((code, failed))

    liquidity, age = lead.liquidity_usd, lead.age_minutes
    if liquidity is None:
        failures.append(("liquidity", "liquidity missing"))
    else:
        floor = policy.min_liquidity_usd
        check(liquidity >= floor, "liquidity", f"liquidity ${liquidity:,.0f} < ${floor:,.0f}",
              f"liquidity ${liquidity:,.0f} >= ${floor:,.0f}")
    if age is None:
        failures.append(("age", "age missing"))
    else:
        floor = policy.min_token_age_minutes
        check(age >= floor, "age", f"age {age:.1f}m < {floor:g}m", f"age {age:.1f}m >= {floor:g}m")

    # The policy loader guarantees both authority rejections are on in v1.
    mint = lead.authorities()
    if not all(name in mint for name in _MINT_FIELDS) or not isinstance(mint["extensions"], list):
        failures.append(("mint_data", "mint account data missing from the lead row"))
    else:
        check(mint["is_initialized"] is True, "not_initialized", "mint is not initialized", "mint initialized")
        check(mint["mint_authority"] is None, "mint_authority",
              f"mint authority active ({mint['mint_authority']})", "mint authority: none")
        check(mint["freeze_authority"] is None, "freeze_authority",
              f"freeze authority set ({mint['freeze_authority']})", "freeze authority: none")
        blocked = sorted(set(mint["extensions"]) & set(policy.blocked_token2022_extensions))
        check(not blocked, "token2022_extension", f"blocked Token-2022 extensions: {', '.join(blocked)}",
              "no blocked Token-2022 extensions")

    top10 = lead.top10_holder_pct
    if top10 is None:
        passed.append("top-10 holders: no data, check skipped per policy")
    else:
        cap = policy.max_top10_holder_pct
        check(top10 <= cap, "top10", f"top-10 holders {top10:.1f}% > {cap:g}%",
              f"top-10 holders {top10:.1f}% <= {cap:g}%")
    return failures, passed


class Risk(Role):
    name = "risk"
    llm_results = frozenset({"pass", "fail"})
    conservative = "fail"

    def run(self) -> None:
        scored: list[tuple[Lead, list[str]]] = []
        for lead in self.db.leads(Status.NEW):
            try:
                self.gather(lead)
            except MintMismatch as exc:
                self.ctx.kill_lead(lead, self.name, exc)
                continue
            except ToolError as exc:
                self.fail(lead, [("data_unavailable", f"on-chain data unavailable: {exc}")], [])
                continue
            stored = self.db.get_lead(lead.lead_id)  # decide only from what the row now holds
            assert stored is not None
            failures, passed = evaluate(stored, self.policy)
            if failures:
                self.fail(stored, failures, passed)
            else:
                scored.append((stored, passed))

        # Only leads that code passed reach the LLM; it can veto them, never revive a fail.
        opinions = self.consult([self.view(lead) for lead, _ in scored])
        for lead, passed in scored:
            if opinions is not None and lead.lead_id in opinions.mismatches:
                self.ctx.kill_lead(lead, self.name, opinions.mismatches[lead.lead_id])
                continue
            veto = self.required_verdict(opinions, lead)
            if veto:
                self.fail(lead, [("llm", veto)], passed)
            else:
                self.pass_lead(lead, passed)

    def gather(self, lead: Lead) -> None:
        """Fetch mint and holder data for the lead's own mint and store it on the row."""
        info = self.tools.chain.mint_info(lead.mint)
        check_mint(lead.mint, info.mint, "rpc getAccountInfo")
        exclude = frozenset(self.policy.pool_authorities) | {lead.pair}
        try:
            holders = self.tools.chain.holders(lead.mint, info.supply, exclude)
        except ToolError as exc:
            holders = None
            self.log("holder_data_unavailable", lead.lead_id, error=str(exc))
        record = info.evidence() | {"holders": holders.evidence() if holders else None}
        self.db.update_lead(lead.lead_id, self.ctx.ts(), authorities_json=json.dumps(record),
                            top10_holder_pct=round(holders.top10_pct, 4) if holders else None)

    def fail(self, lead: Lead, failures: list[Failure], passed: list[str]) -> None:
        detail = "; ".join(text for _, text in failures)
        notes = f"FAIL: {detail}" + (f" | passed: {'; '.join(passed)}" if passed else "")
        if self.db.transition(lead.lead_id, Status.NEW, Status.REJECTED, self.ctx.ts(),
                              risk_status="fail", risk_notes=notes,
                              reject_reason=f"risk/{failures[0][0]}: {detail}"):
            self.handoff(lead, "fail", detail)

    def pass_lead(self, lead: Lead, passed: list[str]) -> None:
        if self.db.transition(lead.lead_id, Status.NEW, Status.RISK_PASSED, self.ctx.ts(),
                              risk_status="pass", risk_notes="PASS: " + "; ".join(passed)):
            top10 = "null" if lead.top10_holder_pct is None else f"{lead.top10_holder_pct:.1f}"
            self.handoff(lead, "pass", f"liquidity_usd={lead.liquidity_usd:.0f} age_minutes={lead.age_minutes:.1f} "
                                       f"mint_authority=null freeze_authority=null top10_holder_pct={top10}")

    @staticmethod
    def view(lead: Lead) -> dict:
        mint = lead.authorities()
        return {
            "lead_id": lead.lead_id, "mint": lead.mint,
            "liquidity_usd": lead.liquidity_usd, "age_minutes": lead.age_minutes,
            "top10_holder_pct": lead.top10_holder_pct,
            **{name: mint.get(name) for name in (*_MINT_FIELDS, "token_program", "source")},
        }
