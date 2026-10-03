"""Read-only reports built from the tables: approvals list, status, run and daily summaries."""

from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from .db import DeskDB, Lead, Position, Status

if TYPE_CHECKING:
    from .context import DeskContext

DISCLAIMERS = {
    "paper": "Paper mode: nothing was signed or sent. Paper fills use quoted prices and ignore fees, "
             "latency, and failed sends; none of this shows the strategy is profitable.",
    "dry_run": "Dry run: any trades were built, signed and simulated on Solana but never sent. Simulated "
               "fills are not evidence that the strategy is profitable.",
    "live": "Live mode: trades use real SOL from the trading wallet. Past fills are not evidence "
            "that the strategy is profitable.",
}


def mode_label(mode: str) -> str:
    return {"paper": "PAPER", "dry_run": "DRY RUN", "live": "LIVE"}.get(mode, mode.upper())


def equity_text(ctx: "DeskContext") -> str:
    equity = ctx.equity_sol()
    return "unknown until the signer reports the wallet balance" if equity is None else f"{equity:.4f} SOL"


@dataclass
class Stats:
    scanned: int
    filtered: int
    filter_reasons: Counter[str]
    duplicates: int  # scanned while a lead from before the window was inside its cooldown
    leads: list[Lead]
    approvals: int
    human_rejections: int
    opened: list[Position]
    closed: list[Position]


def collect_stats(db: DeskDB, since: str, until: str = "9999-12-31") -> Stats:
    window = (since, until)
    candidates = db.conn.execute(
        "SELECT last_result, last_reason FROM candidates WHERE last_seen >= ? AND last_seen < ?", window
    ).fetchall()
    filtered = [row["last_reason"] or "" for row in candidates if row["last_result"] == "filtered"]
    decisions = Counter(row["action"] for row in db.conn.execute(
        "SELECT action FROM events WHERE role = 'approval' AND timestamp >= ? AND timestamp < ?", window))
    positions = db.positions()
    return Stats(
        scanned=len(candidates),
        filtered=len(filtered),
        filter_reasons=Counter(reason.split(":", 1)[0] for reason in filtered),
        duplicates=int(db.scalar(
            """SELECT COUNT(*) FROM candidates c
               WHERE c.last_seen >= ? AND c.last_seen < ? AND c.last_result = 'duplicate'
                 AND NOT EXISTS (SELECT 1 FROM leads l WHERE l.mint = c.mint AND l.discovered_at >= ?)""",
            (since, until, since))),
        leads=db.leads_between(since, until),
        approvals=decisions["approve"],
        human_rejections=decisions["reject"],
        opened=[p for p in positions if since <= p.opened_at < until],
        closed=[p for p in positions if p.closed_at and since <= p.closed_at < until],
    )


def rejection_groups(leads: list[Lead]) -> dict[str, list[Lead]]:
    """Closed-without-trading leads keyed by the stage that closed them."""
    groups: dict[str, list[Lead]] = {}
    for lead in leads:
        if lead.status == Status.EXPIRED:
            key = "expired"
        elif lead.status == Status.DEAD:
            key = "dead"
        elif lead.status == Status.REJECTED:
            key = (lead.reject_reason or "unknown").split("/", 1)[0].split(":", 1)[0]
        else:
            continue
        groups.setdefault(key, []).append(lead)
    return groups


def _clip(text: str | None, width: int = 110) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[:width - 3] + "..."


def _tally(counter: Counter[str]) -> str:
    return ", ".join(f"{key} {count}" for key, count in counter.most_common())


def approval_line(lead: Lead) -> str:
    plan = lead.quote()
    return (f"{lead.ref:<9} {lead.mint}  size {plan.get('size_sol', 0):.4f} SOL  "
            f"impact {plan.get('price_impact_pct', 0):.2f}%  expires {str(plan.get('expires_at', '?'))[11:19]}Z")


def format_approvals(ctx: "DeskContext") -> str:
    leads = ctx.db.leads(Status.AWAITING)
    if not leads:
        return "awaiting approval: none"
    lines = ["awaiting approval (decide yes/no by lead id):"]
    lines += [f"  {approval_line(lead)}" for lead in leads]
    lines.append(f"  -> {ctx.cli} approve LEAD-<id>   |   {ctx.cli} reject LEAD-<id>")
    return "\n".join(lines)


def format_status(ctx: "DeskContext") -> str:
    db = ctx.db
    halted = f"YES ({db.halt_reason()})" if db.is_halted() else "no"
    mode = ctx.mode
    lines = [
        f"mode: {mode_label(mode)} | halted: {halted}",
        f"equity: {equity_text(ctx)} (realized {db.realized_total(mode):+.4f}, "
        f"unrealized {db.unrealized_total(mode):+.4f})",
        format_approvals(ctx),
        "open positions:",
    ]
    positions = db.positions(open_only=True)
    lines += [f"  LEAD-{p.lead_id:<4} {p.mode:<7} {p.mint}  size {p.size_sol:.4f} SOL  entry {p.paper_entry:.3e} "
              f"SOL/token  unrealized {p.unrealized:+.4f} SOL  opened {p.opened_at}" for p in positions] or ["  none"]
    lines.append("latest leads:")
    lines += [f"  {lead.ref:<9} {lead.status:<18} {_clip(lead.reject_reason or lead.risk_notes, 90)}"
              for lead in db.recent_leads(12)] or ["  none"]
    return "\n".join(lines)


def _row(label: str, value: object, detail: str = "") -> str:
    return f"{label:<26}{value!s:>5}" + (f"   {detail}" if detail else "")


def format_run_summary(ctx: "DeskContext", stats: Stats, *, cycles: int, started: str, ended: str) -> str:
    groups = rejection_groups(stats.leads)
    awaiting = [lead for lead in stats.leads if lead.status == Status.AWAITING]
    filled = [lead for lead in stats.leads if lead.status in (Status.FILLED, Status.CLOSED)]
    rejected = stats.filtered + sum(len(group) for group in groups.values())
    lines = [
        f"=== {mode_label(ctx.mode).lower()} loop summary: {cycles} cycle(s), {started} -> {ended} ===",
        _row("scanned candidates", stats.scanned),
        _row("  filtered by Search", stats.filtered, _tally(stats.filter_reasons)),
    ]
    if stats.duplicates:
        lines.append(_row("  already a lead", stats.duplicates, "lead from before this run, inside lead_cooldown_hours"))
    lines.append(_row("leads created", len(stats.leads)))
    labels = {"search": "  dropped by Search LLM", "risk": "  rejected by Risk", "sniper": "  rejected by Sniper",
              "human": "  rejected by human", "expired": "  expired undecided", "dead": "  dead (mint mismatch)"}
    for key in [*labels, *sorted(set(groups) - set(labels))]:
        group = groups.get(key, [])
        if key in ("search", "human", "expired", "dead") and not group:
            continue
        lines.append(_row(labels.get(key, f"  closed by {key}"), len(group)))
        lines += [f"      {lead.ref}: {_clip(lead.reject_reason)}" for lead in group[:12]]
        if len(group) > 12:
            lines.append(f"      ... and {len(group) - 12} more (python main.py status / events)")
    lines.append(_row("awaiting approval", len(awaiting)))
    lines += [f"      {approval_line(lead)}" for lead in awaiting]
    if filled:
        lines.append(_row("approved and filled", len(filled)))
    ordered = [lead for lead in stats.leads if lead.status == Status.ORDERED]
    if ordered:
        lines.append(_row("approved, order pending", len(ordered)))
    lines.append(_row("open positions", ctx.db.count_open_positions(ctx.mode)))
    lines.append(_row("halted", "yes" if ctx.db.is_halted() else "no", ctx.db.halt_reason() or ""))
    lines.append(f"TOTAL: scanned {stats.scanned} | rejected {rejected} | awaiting approval {len(awaiting)}")
    lines.append(DISCLAIMERS[ctx.mode])
    return "\n".join(lines)


def format_daily_summary(ctx: "DeskContext", day: date, stats: Stats) -> str:
    groups = rejection_groups(stats.leads)
    awaiting = [lead.ref for lead in ctx.db.leads(Status.AWAITING)]
    reasons = Counter((lead.reject_reason or "unknown").split(":", 1)[0]
                      for group in groups.values() for lead in group)
    realized = sum(p.realized_sol or 0.0 for p in stats.closed)
    halted = f"yes ({ctx.db.halt_reason()})" if ctx.db.is_halted() else "no"
    lines = [
        f"# Desk daily summary, {day.isoformat()} (UTC)",
        f"- mode: {mode_label(ctx.mode)} | halted: {halted} | equity: {equity_text(ctx)}",
        f"- scanned candidates: {stats.scanned} (filtered by Search: {stats.filtered}"
        + (f"; {_tally(stats.filter_reasons)}" if stats.filtered else "") + ")",
        f"- leads created: {len(stats.leads)}; closed without trading: "
        + (", ".join(f"{key} {len(group)}" for key, group in groups.items()) or "none"),
        f"- awaiting approval now: {', '.join(awaiting) or 'none'}",
        f"- human decisions: {stats.approvals} approve, {stats.human_rejections} reject",
        f"- positions: {len(stats.opened)} opened, {len(stats.closed)} closed, "
        f"{ctx.db.count_open_positions(ctx.mode)} open (unrealized {ctx.db.unrealized_total(ctx.mode):+.4f} SOL)",
        f"- realized P&L from positions closed today: {realized:+.4f} SOL",
    ]
    if stats.closed:
        lines.append("- exit reasons: " + "; ".join(f"LEAD-{p.lead_id} {_clip(p.exit_reason, 80)}" for p in stats.closed))
    if reasons:
        lines.append("- rejection reasons: " + _tally(reasons))
    lines.append(DISCLAIMERS[ctx.mode])
    return "\n".join(lines)
