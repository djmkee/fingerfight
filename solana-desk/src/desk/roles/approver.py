"""Approver: with auto_approve on, decide which staged leads to buy, within the coded limits.

decider "rules" buys every staged lead, deepest liquidity first. decider "ai" asks the LLM to
choose, showing it numbers only, never token names or descriptions: those are written by
strangers and can be crafted to manipulate a model. Either way the buys go through the same
approval gate a human uses, so the halt, expiry, slot, daily-buy and signer checks all apply,
and size always comes from code.
"""

from typing import Any

from .. import approval, execution
from ..db import Lead, Status
from .base import Role

_MARKET_FIELDS = ("volume_h1_usd", "volume_h24_usd", "price_change_h1_pct", "price_change_h24_pct",
                  "buys_h1", "sells_h1", "fdv_usd")


class Approver(Role):
    name = "approver"
    llm_results = frozenset({"buy", "skip"})
    conservative = "skip"

    def run(self) -> None:
        if not self.policy.auto_approve:
            return
        candidates = self.db.leads(Status.AWAITING)
        if not candidates:
            return
        if self.db.is_halted():
            self.log("skipped", reason="desk is halted")
            return
        budget = min(execution.free_slots(self.ctx),
                     self.policy.max_buys_per_day - execution.buys_today(self.ctx))
        if budget <= 0:
            self.log("skipped", reason="no free position slot or daily buy left", candidates=len(candidates))
            return
        if self.ctx.mode != "paper" and not execution.signer_status(self.db, self.ctx.now()).online:
            self.log("skipped", reason="the signer is offline", candidates=len(candidates))
            self.ctx.echo("approver: the signer is offline, so nothing was approved")
            return

        picks = self.rules_pick(candidates) if self.policy.decider == "rules" else self.ai_pick(candidates, budget)
        for lead in picks[:budget]:
            try:
                approval.approve(self.ctx, lead.ref, by=f"auto:{self.policy.decider}")
            except approval.ApprovalError as exc:
                self.log("auto_approve_refused", lead.lead_id, reason=str(exc))
                self.ctx.echo(f"approver: {lead.ref} not approved: {exc}")

    @staticmethod
    def rules_pick(candidates: list[Lead]) -> list[Lead]:
        return sorted(candidates, key=lambda lead: lead.liquidity_usd or 0.0, reverse=True)

    def ai_pick(self, candidates: list[Lead], budget: int) -> list[Lead]:
        if self.ctx.llm is None:
            self.log("skipped", reason="decider is ai but no LLM is configured", candidates=len(candidates))
            self.ctx.echo("approver: decider is ai but no LLM is configured (LLM_* in .env), so nothing "
                          "was approved; set decider: rules in config/policy.yaml to buy without one")
            return []
        open_positions = self.db.count_open_positions(self.ctx.mode)
        opinions = self.consult([self.view(lead, budget, open_positions) for lead in candidates])
        if opinions is None or opinions.failed:
            return []  # no answer means no trade
        by_id = {lead.lead_id: lead for lead in candidates}
        for lead_id, mismatch in opinions.mismatches.items():
            self.ctx.kill_lead(by_id[lead_id], self.name, mismatch)
        picks = []
        for lead_id, verdict in opinions.verdicts.items():
            lead = by_id[lead_id]
            if lead_id in opinions.mismatches:
                continue
            self.handoff(lead, verdict.result, verdict.evidence)
            if verdict.result == "buy":
                picks.append(lead)
        return picks

    @staticmethod
    def view(lead: Lead, free_slots: int, open_positions: int) -> dict[str, Any]:
        """Numbers only: no symbol, name, description, or URL ever reaches the decider."""
        plan, market = lead.quote(), lead.market()
        return {
            "lead_id": lead.lead_id, "mint": lead.mint,
            "liquidity_usd": lead.liquidity_usd, "age_minutes": lead.age_minutes,
            "top10_holder_pct": lead.top10_holder_pct,
            **{name: market.get(name) for name in _MARKET_FIELDS},
            "size_sol": plan.get("size_sol"), "price_impact_pct": plan.get("price_impact_pct"),
            "sell_price_impact_pct": (plan.get("sell_check") or {}).get("price_impact_pct"),
            "round_trip_pct": (plan.get("sell_check") or {}).get("round_trip_pct"),
            "free_slots": free_slots, "open_positions": open_positions,
        }
