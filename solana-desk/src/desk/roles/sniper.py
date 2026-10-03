"""Sniper: for Risk=pass leads only, store an executable Jupiter quote and a policy-sized plan.

The lead then waits for a human yes/no as awaiting_approval. Sniper builds no transaction,
holds no key, and signs nothing.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .. import execution
from ..clock import iso
from ..constants import LAMPORTS_PER_SOL, SOL_MINT
from ..db import Lead, Status
from ..guards import MintMismatch, check_mint
from ..tools.base import Quote, ToolError
from .base import Role


@dataclass
class Plan:
    size_sol: float
    size_lamports: int
    decimals: int | None = None
    buy: Quote | None = None
    sell: Quote | None = None
    problems: list[tuple[str, str]] = field(default_factory=list)  # (code, detail)

    def record(self, quoted_at: datetime, expires_at: datetime) -> dict[str, Any]:
        """What goes in quote_json. A future signer would rebuild the swap from `jupiter_quote`."""
        buy, sell = self.buy, self.sell
        assert buy is not None
        entry_price = None
        if buy.out_amount > 0 and self.decimals is not None:
            entry_price = (buy.in_amount / LAMPORTS_PER_SOL) / (buy.out_amount / 10**self.decimals)
        return {
            "quoted_at": iso(quoted_at),
            "expires_at": iso(expires_at),
            "size_sol": self.size_sol,
            "size_lamports": self.size_lamports,
            "input_mint": buy.input_mint,
            "output_mint": buy.output_mint,
            "in_amount": str(buy.in_amount),
            "out_amount": str(buy.out_amount),
            "min_out_amount": str(buy.min_out_amount),
            "price_impact_pct": buy.price_impact_pct,
            "slippage_bps": buy.slippage_bps,
            "route": list(buy.route),
            "token_decimals": self.decimals,
            "entry_price_sol": entry_price,
            "sell_check": None if sell is None else {
                "out_lamports": str(sell.out_amount),
                "price_impact_pct": sell.price_impact_pct,
                "round_trip_pct": (sell.out_amount / buy.in_amount - 1) * 100,
            },
            "problems": [detail for _, detail in self.problems],
            "source": buy.source,
            "context_slot": buy.context_slot,
            "jupiter_quote": buy.raw,
        }


class Sniper(Role):
    name = "sniper"
    llm_results = frozenset({"awaiting_approval", "reject"})
    conservative = "reject"

    def run(self) -> None:
        if self.db.is_halted():
            self.log("skipped", reason="desk is halted")
            return
        staged: list[tuple[Lead, Plan]] = []
        for lead in self.db.leads(Status.RISK_PASSED):
            # Defense in depth: the status says risk_passed, but only an explicit Risk=pass counts.
            if lead.risk_status != "pass":
                self.reject(lead, "integrity", f"risk_status is {lead.risk_status!r}, not 'pass'")
                continue
            free = execution.free_slots(self.ctx) - self.db.count_leads(Status.AWAITING) - len(staged)
            if free <= 0:
                self.reject(lead, "no_slot", f"no free position slot (max_open_positions={self.policy.max_open_positions})")
                continue
            try:
                plan = self.plan(lead)
            except MintMismatch as exc:
                self.ctx.kill_lead(lead, self.name, exc)
                continue
            except ToolError as exc:
                self.reject(lead, "no_quote", f"no executable quote: {exc}")
                continue
            if plan.problems:
                code = plan.problems[0][0]
                self.reject(lead, code, "; ".join(detail for _, detail in plan.problems), plan)
                continue
            staged.append((lead, plan))

        opinions = self.consult([self.view(lead, plan) for lead, plan in staged])
        for lead, plan in staged:
            if opinions is not None and lead.lead_id in opinions.mismatches:
                self.ctx.kill_lead(lead, self.name, opinions.mismatches[lead.lead_id])
                continue
            veto = self.required_verdict(opinions, lead)
            if veto:
                self.reject(lead, "llm", veto, plan)
            else:
                self.stage(lead, plan)

    def plan(self, lead: Lead) -> Plan:
        """Size from policy, then quote the buy and a sell of the same tokens. Raises on bad data."""
        size_sol = execution.position_size_sol(self.ctx)
        plan = Plan(size_sol=size_sol or 0.0, size_lamports=int(round((size_sol or 0.0) * LAMPORTS_PER_SOL)))
        if size_sol is None:
            plan.problems.append(("sizing", "wallet balance unknown; the signer reports it while it runs"))
            return plan
        if size_sol < execution.MIN_TRADE_SOL:
            plan.problems.append(("sizing", f"position size {size_sol:g} SOL is below the "
                                            f"{execution.MIN_TRADE_SOL:g} SOL minimum; fund the wallet"))
            return plan
        decimals = lead.authorities().get("decimals")
        if not isinstance(decimals, int):
            plan.problems.append(("sizing", "token decimals unavailable"))
            return plan
        plan.decimals = decimals

        max_impact = self.policy.max_price_impact_pct
        buy = self.tools.quoter.quote(SOL_MINT, lead.mint, plan.size_lamports, self.policy.slippage_bps)
        check_mint(lead.mint, buy.output_mint, "jupiter buy quote outputMint")
        plan.buy = buy
        if buy.input_mint != SOL_MINT:
            plan.problems.append(("quote_mismatch", f"quote input is {buy.input_mint}, not SOL"))
        if buy.in_amount != plan.size_lamports:
            plan.problems.append(("quote_mismatch", f"quote inAmount {buy.in_amount} != size {plan.size_lamports}"))
        if buy.out_amount <= 0:
            plan.problems.append(("quote_mismatch", "quote outAmount is zero"))
        if buy.price_impact_pct > max_impact:
            plan.problems.append(("impact", f"price impact {buy.price_impact_pct:.2f}% > {max_impact:g}%"))
        if plan.problems:
            return plan

        try:
            sell = self.tools.quoter.quote(lead.mint, SOL_MINT, buy.out_amount, self.policy.slippage_bps)
        except ToolError as exc:
            plan.problems.append(("sell_route", f"no sell route for the quoted tokens: {exc}"))
            return plan
        check_mint(lead.mint, sell.input_mint, "jupiter sell quote inputMint")
        plan.sell = sell
        if sell.output_mint != SOL_MINT:
            plan.problems.append(("sell_route", f"sell quote output is {sell.output_mint}, not SOL"))
        elif sell.price_impact_pct > max_impact:
            plan.problems.append(("sell_impact", f"sell-side price impact {sell.price_impact_pct:.2f}% > {max_impact:g}%"))
        return plan

    def _record(self, plan: Plan) -> str:
        now = self.ctx.now()
        return json.dumps(plan.record(now, now + timedelta(minutes=self.policy.approval_ttl_minutes)))

    def stage(self, lead: Lead, plan: Plan) -> None:
        assert plan.buy is not None and plan.sell is not None
        if self.db.transition(lead.lead_id, Status.RISK_PASSED, Status.AWAITING, self.ctx.ts(),
                              quote_json=self._record(plan)):
            self.handoff(lead, "awaiting_approval",
                         f"size_sol={plan.size_sol:g} price_impact_pct={plan.buy.price_impact_pct:.2f} "
                         f"sell_price_impact_pct={plan.sell.price_impact_pct:.2f} "
                         f"route={'>'.join(plan.buy.route) or '?'}")

    def reject(self, lead: Lead, code: str, detail: str, plan: Plan | None = None) -> None:
        fields: dict[str, Any] = {"reject_reason": f"sniper/{code}: {detail}"}
        if plan is not None and plan.buy is not None:
            fields["quote_json"] = self._record(plan)  # kept for the audit trail
        if self.db.transition(lead.lead_id, Status.RISK_PASSED, Status.REJECTED, self.ctx.ts(), **fields):
            self.handoff(lead, "reject", detail)

    @staticmethod
    def view(lead: Lead, plan: Plan) -> dict[str, Any]:
        assert plan.buy is not None and plan.sell is not None
        return {
            "lead_id": lead.lead_id, "mint": lead.mint, "size_sol": plan.size_sol,
            "price_impact_pct": plan.buy.price_impact_pct, "sell_price_impact_pct": plan.sell.price_impact_pct,
            "out_amount": str(plan.buy.out_amount), "route": list(plan.buy.route),
            "round_trip_pct": (plan.sell.out_amount / plan.buy.in_amount - 1) * 100,
        }
