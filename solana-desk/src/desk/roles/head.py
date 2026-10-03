"""Head: assigns lead IDs, applies the halt flag, expires stale quotes, writes the daily summary.

Head never trades: it calls no quote, sizing, or approval code.
"""

import json
from datetime import date, timedelta

from ..clock import parse_iso
from ..db import HaltedError, Status
from ..guards import mentions_key_material
from ..llm import LLMError
from ..reporting import collect_stats, format_daily_summary
from .base import Role
from .search import Candidate


class Head(Role):
    name = "head"

    def start_cycle(self) -> bool:
        """Apply halt rules and expire stale approvals. Returns whether the desk is halted."""
        halted = self.apply_halt_rules()
        self.expire_stale_approvals()
        return halted

    def _roll_day(self) -> str:
        """On the first look of a UTC day, record the equity the daily loss limit is measured from."""
        today = self.ctx.now().date().isoformat()
        if self.db.get_state("day") != today:
            equity = self.ctx.equity_sol()
            with self.db.tx():
                self.db.set_state("day", today)
                self.db.set_state("day_start_equity_sol", equity)
            self.log("day_start", day=today, equity_sol=equity)
        return today

    def apply_halt_rules(self) -> bool:
        today = self._roll_day()
        if self.db.is_halted():
            return True
        start = float(self.db.get_state("day_start_equity_sol") or self.policy.paper_equity_sol)
        equity = self.ctx.equity_sol()
        loss_pct = (start - equity) / start * 100 if start > 0 else 0.0
        if loss_pct >= self.policy.daily_loss_halt_pct:
            self.halt(f"daily paper loss {loss_pct:.2f}% >= {self.policy.daily_loss_halt_pct:g}%",
                      day_start_equity_sol=start, equity_sol=equity)
        failed = int(self.db.get_state(f"failed_sends:{today}", "0") or 0)
        if failed >= self.policy.max_failed_sends:
            self.halt(f"{failed} failed sends today >= {self.policy.max_failed_sends}", failed_sends=failed)
        return self.db.is_halted()

    def halt(self, reason: str, **evidence: object) -> None:
        if self.db.is_halted():
            return
        self.db.set_halted(reason, self.ctx.ts())
        self.log("halt", reason=reason, **evidence)
        self.ctx.echo(f"HALT: {reason}. No new leads or fills until a human runs clear-halt.")

    def clear_halt(self, by: str, note: str) -> None:
        """Human-only. Re-arms the daily loss limit from current equity and resets failed sends."""
        if not self.db.is_halted():
            raise RuntimeError("the desk is not halted")
        today = self._roll_day()
        previous = self.db.halt_reason()
        equity = self.ctx.equity_sol()
        with self.db.tx():
            self.db.clear_halt()
            self.db.set_state("day_start_equity_sol", equity)
            self.db.set_state(f"failed_sends:{today}", 0)
            self.log("halt_cleared", by=by, note=note, previous_reason=previous, equity_sol=equity)

    def record_failed_send(self, lead_id: int | None, detail: str) -> None:
        """Count a failed broadcast. v1 never sends; the future live path reports failures here."""
        key = f"failed_sends:{self._roll_day()}"
        count = int(self.db.get_state(key, "0") or 0) + 1
        self.db.set_state(key, count)
        self.log("failed_send", lead_id, count=count, detail=detail)
        self.apply_halt_rules()

    def admit(self, candidates: list[Candidate]) -> list[int]:
        """Assign lead IDs to Search's candidates, refusing all of them while halted."""
        admitted: list[int] = []
        for candidate in candidates:
            if len(admitted) >= self.policy.max_new_leads_per_cycle:
                self.log("admit_deferred", count=len(candidates) - len(admitted),
                         reason="max_new_leads_per_cycle reached")
                break
            snap = candidate.snapshot
            try:
                lead_id = self.db.insert_lead(
                    ts=self.ctx.ts(), mint=snap.base_mint, pair=snap.pair_address,
                    liquidity_usd=snap.liquidity_usd, age_minutes=round(candidate.age_minutes, 2),
                    source=snap.source, market_json=json.dumps(snap.evidence()),
                )
            except HaltedError as exc:
                self.log("admit_refused", reason=str(exc))
                break
            lead = self.db.get_lead(lead_id)
            assert lead is not None
            self.handoff(lead, "assign", f"pair={lead.pair} source={lead.source}")
            admitted.append(lead_id)
        return admitted

    def expire_stale_approvals(self) -> None:
        """A staged quote nobody decided on in time is closed, never filled late."""
        now = self.ctx.now()
        for lead in self.db.leads(Status.AWAITING):
            expires = lead.quote().get("expires_at")
            if expires is not None and now < parse_iso(expires):
                continue
            if self.db.transition(lead.lead_id, Status.AWAITING, Status.EXPIRED, self.ctx.ts(),
                                  reject_reason=f"head/expired: no human decision before {expires}"):
                self.handoff(lead, "expired", f"expires_at={expires}")

    def daily_summary(self, day: date) -> str:
        since = f"{day.isoformat()}T00:00:00+00:00"
        until = f"{(day + timedelta(days=1)).isoformat()}T00:00:00+00:00"
        stats = collect_stats(self.db, since, until)
        text = format_daily_summary(self.ctx, day, stats)
        notes = self._narrate(text)
        if notes:
            text += f"\n\nHead notes (LLM, written from the figures above):\n{notes}"
        path = self.db.path.parent / "summaries" / f"{day.isoformat()}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n")
        self.log("daily_summary", day=day.isoformat(), path=str(path), scanned=stats.scanned,
                 leads=len(stats.leads))
        return text

    def _narrate(self, figures: str) -> str | None:
        if self.ctx.llm is None:
            return None
        try:
            reply = self.ctx.llm.complete(self.ctx.prompts.system(self.name), json.dumps({"figures": figures}))
        except LLMError as exc:
            self.log("llm_error", error=str(exc))
            return None
        if mentions_key_material(reply):
            self.log("llm_key_guard", note="summary mentioned key material; discarded unread")
            return None
        return reply.strip()
