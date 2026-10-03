"""The cycle: halt check -> Search -> Risk -> Sniper -> approvals list -> Exit -> events."""

import time
from collections.abc import Callable

from .context import DeskContext
from .db import Status
from .reporting import collect_stats, format_approvals, format_run_summary
from .roles import Exit, Head, Risk, Search, Sniper


class Orchestrator:
    def __init__(self, ctx: DeskContext) -> None:
        self.ctx = ctx
        self.head = Head(ctx)
        self.search = Search(ctx)
        self.risk = Risk(ctx)
        self.sniper = Sniper(ctx)
        self.exit = Exit(ctx)

    def run_cycle(self) -> None:
        ctx = self.ctx
        cycle = int(ctx.db.get_state("cycles", "0") or 0) + 1
        ctx.db.set_state("cycles", cycle)
        ctx.log("orchestrator", "cycle_start", cycle=cycle)

        # 1. Head applies the halt flag; while halted, Search is skipped and no lead is admitted.
        if self.head.start_cycle():
            reason = ctx.db.halt_reason()
            ctx.log("orchestrator", "search_skipped", cycle=cycle, reason=reason)
            ctx.echo(f"cycle {cycle}: HALTED ({reason}); Search skipped, no new leads")
            new_leads: list[int] = []
        else:
            # 2. Search writes new leads; Head assigns their IDs.
            new_leads = self.head.admit(self.search.scan())
            self.search.review(new_leads)
        self.risk.run()                 # 3. Risk scores unscored leads
        self.sniper.run()               # 4. Sniper quotes Risk=pass leads only
        ctx.echo(format_approvals(ctx))  # 5. awaiting_approval list for the human
        self.exit.run()                 # 6. Exit re-checks open paper positions
        ctx.log("orchestrator", "cycle_end", cycle=cycle, new_leads=len(new_leads),  # 7. events
                awaiting=ctx.db.count_leads(Status.AWAITING), open_positions=ctx.db.count_open_positions(),
                halted=ctx.db.is_halted())

    def loop(self, cycles: int | None, interval_s: float,
             sleep: Callable[[float], None] = time.sleep) -> str:
        """Run cycles until `cycles` is reached or Ctrl-C, then return the run summary."""
        started = self.ctx.ts()
        done = 0
        try:
            while cycles is None or done < cycles:
                self.run_cycle()
                done += 1
                if cycles is None or done < cycles:
                    sleep(interval_s)
        except KeyboardInterrupt:
            self.ctx.echo("\ninterrupted; stopping the paper loop")
        ended = self.ctx.ts()
        stats = collect_stats(self.ctx.db, started)
        self.ctx.log("orchestrator", "run_summary", cycles=done, started=started, ended=ended,
                     scanned=stats.scanned, filtered=stats.filtered, leads=len(stats.leads))
        return format_run_summary(self.ctx, stats, cycles=done, started=started, ended=ended)
