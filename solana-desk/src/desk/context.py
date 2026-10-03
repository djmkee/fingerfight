"""Everything a role needs, passed explicitly: policy, storage, tools, prompts, LLM, and clock."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .clock import iso, utcnow
from .db import DeskDB, Lead, Status
from .guards import MintMismatch
from .handoff import Handoff
from .policy import Policy
from .prompts import PromptBook
from .tools.base import Toolbox


class Completer(Protocol):
    def complete(self, system: str, user: str) -> str: ...


@dataclass
class DeskContext:
    policy: Policy
    db: DeskDB
    prompts: PromptBook
    tools: Toolbox | None = None
    llm: Completer | None = None
    clock: Callable[[], datetime] = utcnow
    echo: Callable[[str], None] = print
    cli: str = "python main.py"  # how printed hints spell the CLI (adds --demo in demo mode)

    def now(self) -> datetime:
        return self.clock()

    def ts(self) -> str:
        return iso(self.clock())

    def log(self, role: str, action: str, lead_id: int | None = None, **payload: object) -> None:
        self.db.log_event(self.ts(), role, action, lead_id, payload)

    def equity_sol(self) -> float:
        """Paper equity: bankroll + realized P&L + open positions marked at their last exit quote."""
        return self.policy.paper_equity_sol + self.db.realized_total() + self.db.unrealized_total()

    def kill_lead(self, lead: Lead, role: str, mismatch: MintMismatch) -> None:
        """A stage saw a different mint than the lead's: mark it dead and zero any paper position."""
        line = Handoff(lead.lead_id, lead.mint, role, "dead", str(mismatch)).render()
        ts = self.ts()
        with self.db.tx():
            if not self.db.transition(lead.lead_id, Status.ACTIVE, Status.DEAD, ts,
                                      reject_reason=f"{role}/mint_mismatch: {mismatch}"):
                return
            position = self.db.get_position(lead.lead_id)
            if position is not None and position.is_open:
                self.db.close_position(lead.lead_id, ts=ts, paper_exit=0.0,
                                       realized_sol=-position.size_sol, exit_reason="mint_mismatch")
            self.log(role, "dead", lead.lead_id, line=line, where=mismatch.where,
                     observed_mint=str(mismatch.observed))
        self.echo(line)
