"""Search: turn discovery-feed pairs into candidates that already passed liquidity and age filters.

Search proposes; Head assigns the lead IDs. Each emitted lead carries lead_id, mint, pair,
liquidity, age, and source, all copied from the stored DexScreener snapshot.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import timedelta

from ..clock import iso
from ..db import Lead, Status
from ..guards import is_valid_mint
from ..tools.base import PairSnapshot, ToolError
from .base import Role


@dataclass(frozen=True)
class Candidate:
    snapshot: PairSnapshot
    age_minutes: float


class Search(Role):
    name = "search"
    llm_results = frozenset({"emit", "drop"})
    conservative = "drop"

    def scan(self) -> list[Candidate]:
        try:
            snapshots = self.tools.market.discover()
        except ToolError as exc:
            self.log("tool_error", tool="market.discover", error=str(exc))
            self.ctx.echo(f"search: discovery failed, nothing scanned this cycle ({exc})")
            return []

        now = self.ctx.now()
        cooldown_start = iso(now - timedelta(hours=self.policy.lead_cooldown_hours))
        candidates: list[Candidate] = []
        tally: Counter[str] = Counter()
        for snap in snapshots:
            age = snap.age_minutes(now)
            reason = self.filter_reason(snap, age)
            result = "filtered" if reason else "pass"
            if reason is None:
                recent = self.db.latest_lead_for_mint(snap.base_mint)
                if recent is not None and recent.discovered_at >= cooldown_start:
                    result, reason = "duplicate", f"duplicate: {recent.ref} is {recent.status}"
            changed = self.db.record_candidate(mint=snap.base_mint, pair=snap.pair_address,
                                               source=snap.source, ts=self.ctx.ts(),
                                               result=result, reason=reason)
            if result == "filtered" and changed:
                self.log("filtered", mint=snap.base_mint, pair=snap.pair_address,
                         source=snap.source, reason=reason)
            if result == "pass" and age is not None:
                candidates.append(Candidate(snap, age))
            tally[(reason or "").split(":", 1)[0] if result == "filtered" else result] += 1
        filtered = {code: count for code, count in tally.items() if code not in ("pass", "duplicate")}
        self.log("scan", seen=len(snapshots), passed=len(candidates), tally=dict(tally))
        self.ctx.echo(f"search: scanned {len(snapshots)} | passed filters {tally['pass']} | "
                      f"already a lead {tally['duplicate']} | filtered {sum(filtered.values())}"
                      + (f" ({', '.join(f'{code} {count}' for code, count in filtered.items())})" if filtered else ""))
        candidates.sort(key=lambda c: c.snapshot.liquidity_usd or 0.0, reverse=True)
        return candidates

    def filter_reason(self, snap: PairSnapshot, age: float | None) -> str | None:
        """Coded filters. A reason is "<code>: <detail>"; None means the candidate passes."""
        if not is_valid_mint(snap.base_mint):
            return f"invalid_mint: {snap.base_mint!r} is not a Solana address"
        if not snap.pair_address:
            return "no_pair: not the base token of any listed Solana pair"
        if snap.liquidity_usd is None:
            return "no_liquidity: pair reports no USD liquidity"
        if snap.liquidity_usd < self.policy.min_liquidity_usd:
            return f"liquidity: ${snap.liquidity_usd:,.0f} < ${self.policy.min_liquidity_usd:,.0f}"
        if age is None:
            return "no_age: pair has no creation time"
        if age < self.policy.min_token_age_minutes:
            return f"age: {age:.1f}m < {self.policy.min_token_age_minutes:g}m"
        return None

    def review(self, lead_ids: list[int]) -> None:
        """Emit a handoff for each new lead. An LLM, if configured, may only drop leads."""
        leads = [lead for lead_id in lead_ids if (lead := self.db.get_lead(lead_id)) is not None]
        opinions = self.consult([self.view(lead) for lead in leads])
        for lead in leads:
            if opinions is not None and lead.lead_id in opinions.mismatches:
                self.ctx.kill_lead(lead, self.name, opinions.mismatches[lead.lead_id])
                continue
            drop = self.optional_verdict(opinions, lead)
            if drop is not None:
                if self.db.transition(lead.lead_id, Status.NEW, Status.REJECTED, self.ctx.ts(),
                                      reject_reason=f"search/llm_drop: {drop.evidence or 'dropped'}"):
                    self.handoff(lead, "drop", drop.evidence)
                continue
            self.handoff(lead, "emit", self.evidence(lead))

    @staticmethod
    def evidence(lead: Lead) -> str:
        return (f"pair={lead.pair} liquidity_usd={lead.liquidity_usd:.0f} "
                f"age_minutes={lead.age_minutes:.1f} source={lead.source}")

    @staticmethod
    def view(lead: Lead) -> dict:
        market = lead.market()
        return {
            "lead_id": lead.lead_id, "mint": lead.mint, "pair": lead.pair,
            "liquidity_usd": lead.liquidity_usd, "age_minutes": lead.age_minutes, "source": lead.source,
            "market": {key: market.get(key) for key in ("base_mint", "base_symbol", "quote_symbol",
                                                         "dex_id", "price_usd", "volume_h24_usd")},
        }
