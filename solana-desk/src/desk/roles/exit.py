"""Exit: every few minutes, re-check open positions and get out on a trigger.

Triggers: liquidity drop, impact spike, authority change, time stop. Paper and dry-run positions
close as book entries at the executable exit quote (zero if there is none). A live position holds
real tokens, so a trigger places a sell order that the signer executes; it closes when that fills.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .. import execution
from ..clock import parse_iso
from ..constants import LAMPORTS_PER_SOL, SOL_MINT
from ..db import Lead, Position, Status
from ..guards import MintMismatch, check_mint
from ..tools.base import MintInfo, PairSnapshot, Quote, ToolError
from .base import Role

TRIGGERS = ("liquidity_drop", "impact_spike", "authority_change", "time_stop")


@dataclass
class Recheck:
    lead: Lead
    position: Position
    held_minutes: float
    pair_now: PairSnapshot | None = None
    exit_quote: Quote | None = None
    mint_now: MintInfo | None = None
    triggers: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)  # (trigger it stands for, detail)

    @property
    def liquidity_usd(self) -> float | None:
        return self.pair_now.liquidity_usd if self.pair_now else None

    @property
    def proceeds_sol(self) -> float | None:
        return self.exit_quote.out_amount / LAMPORTS_PER_SOL if self.exit_quote else None

    def evidence(self, checked_at: str) -> str:
        """The tool results behind this recheck, stored as the lead row's recheck_json."""
        quote = self.exit_quote
        return json.dumps({
            "checked_at": checked_at,
            "held_minutes": round(self.held_minutes, 2),
            "pair": self.pair_now.evidence() if self.pair_now else None,
            "exit_quote": None if quote is None else {
                "in_amount": str(quote.in_amount), "out_amount": str(quote.out_amount),
                "price_impact_pct": quote.price_impact_pct, "route": list(quote.route), "source": quote.source,
            },
            "mint": self.mint_now.evidence() if self.mint_now else None,
            "data_failures": [detail for _, detail in self.failures],
            "triggers": self.triggers,
        })


class Exit(Role):
    name = "exit"
    llm_results = frozenset({"hold", "exit"})
    conservative = "exit"

    def run(self) -> None:
        now = self.ctx.now()
        checks: list[Recheck] = []
        for position in self.db.positions(open_only=True):
            if position.sell_order_id is not None:
                continue  # a sell order is already with the signer
            lead = self.db.get_lead(position.lead_id)
            assert lead is not None
            if lead.status == Status.DEAD:  # its data cannot be trusted; just get out
                if position.mode == "live":
                    execution.place_sell(self.ctx, position, "lead_dead: a stage saw a different mint")
                continue
            if not self._due(position, now):
                continue
            try:
                checks.append(self.recheck(lead, position, now))
            except MintMismatch as exc:
                self.ctx.kill_lead(lead, self.name, exc)

        # The LLM sees only positions code would hold; it may add an exit, never cancel one.
        opinions = self.consult([self.view(check) for check in checks if not check.triggers])
        for check in checks:
            lead = check.lead
            if opinions is not None and lead.lead_id in opinions.mismatches:
                self.ctx.kill_lead(lead, self.name, opinions.mismatches[lead.lead_id])
                continue
            extra = self.optional_verdict(opinions, lead)
            if extra is not None:
                if extra.evidence.lower().startswith(TRIGGERS):
                    check.triggers.append(f"{extra.evidence} (LLM)")
                else:
                    self.log("llm_exit_ignored", lead.lead_id, evidence=extra.evidence,
                             reason="evidence does not start with an exit trigger name")
            if check.triggers:
                self.close(check)
            else:
                self.hold(check)

    def _due(self, position: Position, now: datetime) -> bool:
        if position.last_checked_at is None:
            return True
        interval = timedelta(minutes=self.policy.exit.check_interval_minutes)
        return now - parse_iso(position.last_checked_at) >= interval

    def recheck(self, lead: Lead, position: Position, now: datetime) -> Recheck:
        check_mint(lead.mint, position.mint, "positions table")
        rules = self.policy.exit
        check = Recheck(lead, position, (now - parse_iso(position.opened_at)).total_seconds() / 60)
        if check.held_minutes >= rules.time_stop_minutes:
            check.triggers.append(f"time_stop: held {check.held_minutes:.0f}m >= {rules.time_stop_minutes:g}m")

        try:
            snap = self.tools.market.pair(lead.pair)
        except ToolError as exc:
            check.failures.append(("liquidity_drop", f"pair data unavailable ({exc})"))
        else:
            check_mint(lead.mint, snap.base_mint, "dexscreener pair baseToken")
            check.pair_now = snap
            entry = position.entry_liquidity_usd
            if snap.liquidity_usd is None:
                check.failures.append(("liquidity_drop", "pair reports no liquidity"))
            elif entry:
                drop = (entry - snap.liquidity_usd) / entry * 100
                if drop >= rules.liquidity_drop_pct:
                    check.triggers.append(f"liquidity_drop: ${entry:,.0f} -> ${snap.liquidity_usd:,.0f} (-{drop:.1f}%)")

        try:
            quote = self.tools.quoter.quote(lead.mint, SOL_MINT, int(position.token_amount), self.policy.slippage_bps)
        except ToolError as exc:
            check.failures.append(("impact_spike", f"no exit quote ({exc})"))
        else:
            check_mint(lead.mint, quote.input_mint, "jupiter exit quote inputMint")
            if quote.output_mint != SOL_MINT:
                check.failures.append(("impact_spike", f"exit quote pays {quote.output_mint}, not SOL"))
            else:
                check.exit_quote = quote
                if quote.price_impact_pct > rules.max_exit_impact_pct:
                    check.triggers.append(f"impact_spike: exit impact {quote.price_impact_pct:.2f}% > "
                                          f"{rules.max_exit_impact_pct:g}%")

        try:
            mint_now = self.tools.chain.mint_info(lead.mint)
        except ToolError as exc:
            check.failures.append(("authority_change", f"mint data unavailable ({exc})"))
        else:
            check_mint(lead.mint, mint_now.mint, "rpc getAccountInfo")
            check.mint_now = mint_now
            entry_mint = lead.authorities()
            changes = [f"{name} {entry_mint.get(name)} -> {getattr(mint_now, name)}"
                       for name in ("mint_authority", "freeze_authority")
                       if getattr(mint_now, name) != entry_mint.get(name)]
            added = sorted(set(mint_now.extensions) - set(entry_mint.get("extensions") or []))
            if added:
                changes.append(f"new extensions {', '.join(added)}")
            if changes:
                check.triggers.append("authority_change: " + "; ".join(changes))

        # Data that stays unavailable is treated as the trigger it would have checked.
        failures_in_a_row = position.recheck_failures + 1
        if check.failures and failures_in_a_row >= rules.max_recheck_failures:
            check.triggers.extend(f"{trigger}: {detail}, {failures_in_a_row} rechecks in a row"
                                  for trigger, detail in check.failures)
        return check

    def close(self, check: Recheck) -> None:
        position, lead = check.position, check.lead
        reason = "; ".join(check.triggers)
        if position.mode == "live":
            self._sell(check, reason)
            return
        proceeds = check.proceeds_sol or 0.0  # no executable exit quote: value the tokens at zero
        tokens = int(position.token_amount) / 10**position.token_decimals
        realized = proceeds - position.size_sol
        ts = self.ctx.ts()
        with self.db.tx():
            if not self.db.close_position(lead.lead_id, ts=ts, paper_exit=proceeds / tokens if tokens else 0.0,
                                          realized_sol=realized, exit_reason=reason):
                return
            self.db.transition(lead.lead_id, Status.FILLED, Status.CLOSED, ts,
                               recheck_json=check.evidence(ts))
            self.log("paper_close", lead.lead_id, exit_reason=reason, proceeds_sol=proceeds,
                     cost_sol=position.size_sol, realized_sol=realized,
                     exit_quote=check.exit_quote.raw if check.exit_quote else None)
            self.handoff(lead, "exit", f"{reason}; proceeds_sol={proceeds:.6f} cost_sol={position.size_sol:.6f} "
                                       f"realized_sol={realized:+.6f} ({position.mode})")

    def _sell(self, check: Recheck, reason: str) -> None:
        """Live: record the recheck, then hand the signer a sell order for the whole position."""
        position, ts = check.position, self.ctx.ts()
        with self.db.tx():
            self.db.update_position(position.lead_id, last_checked_at=ts, unrealized=self._mark(check),
                                    recheck_failures=position.recheck_failures + 1 if check.failures else 0)
            self.db.update_lead(position.lead_id, ts, recheck_json=check.evidence(ts))
            execution.place_sell(self.ctx, position, reason)
        self.handoff(check.lead, "exit", f"{reason}; sell order placed for the signer (live)")

    @staticmethod
    def _mark(check: Recheck) -> float:
        """Unrealized SOL at the exit quote. A live position nobody can quote is marked at zero."""
        position = check.position
        if check.proceeds_sol is not None:
            return check.proceeds_sol - position.size_sol
        return -position.size_sol if position.mode == "live" else position.unrealized

    def hold(self, check: Recheck) -> None:
        position = check.position
        ts = self.ctx.ts()
        fields: dict[str, Any] = {
            "last_checked_at": ts,
            "recheck_failures": position.recheck_failures + 1 if check.failures else 0,
        }
        fields["unrealized"] = self._mark(check)
        with self.db.tx():
            self.db.update_position(position.lead_id, **fields)
            self.db.update_lead(position.lead_id, ts, recheck_json=check.evidence(ts))
        impact = f"{check.exit_quote.price_impact_pct:.2f}" if check.exit_quote else "null"
        liquidity = f"{check.liquidity_usd:.0f}" if check.liquidity_usd is not None else "null"
        evidence = (f"held_minutes={check.held_minutes:.0f} liquidity_usd={liquidity} exit_price_impact_pct={impact} "
                    f"unrealized_sol={fields.get('unrealized', position.unrealized):+.6f}")
        if check.failures:
            evidence += f" recheck_failures={fields['recheck_failures']}"
        self.handoff(check.lead, "hold", evidence)

    def view(self, check: Recheck) -> dict[str, Any]:
        entry = check.lead.authorities()
        now = check.mint_now
        return {
            "lead_id": check.lead.lead_id, "mint": check.lead.mint,
            "held_minutes": round(check.held_minutes, 1),
            "entry_liquidity_usd": check.position.entry_liquidity_usd, "liquidity_usd_now": check.liquidity_usd,
            "exit_price_impact_pct": check.exit_quote.price_impact_pct if check.exit_quote else None,
            "has_exit_quote": check.exit_quote is not None,
            "size_sol": check.position.size_sol, "exit_value_sol": check.proceeds_sol,
            "entry_mint_authority": entry.get("mint_authority"), "entry_freeze_authority": entry.get("freeze_authority"),
            "entry_extensions": entry.get("extensions"),
            "mint_authority_now": now.mint_authority if now else None,
            "freeze_authority_now": now.freeze_authority if now else None,
            "extensions_now": list(now.extensions) if now else None,
            "data_failures": [detail for _, detail in check.failures],
        }
