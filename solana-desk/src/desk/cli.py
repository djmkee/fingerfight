"""Command line: run the paper loop, decide on staged leads, and inspect the desk."""

import argparse
import getpass
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from .approval import ApprovalError, approve, reject
from .clock import utcnow
from .context import DeskContext
from .db import DeskDB
from .guards import KeyMaterialError
from .handoff import HandoffError, parse_lead_ref
from .llm import LLMClient
from .orchestrator import Orchestrator
from .policy import PolicyError, load_policy
from .prompts import PromptBook
from .reporting import format_status
from .roles import Head
from .settings import load_settings
from .tools import live_toolbox
from .tools.fixture import fixture_toolbox, load_specs

DEMO_FIXTURE = "fixtures/demo_market.json"
DEMO_DB = "data/demo.sqlite3"


def build_context(args: argparse.Namespace, *, with_tools: bool) -> DeskContext:
    root: Path = args.root
    settings = load_settings(root)
    policy = load_policy(settings.policy_path)
    db_path = args.db or (root / DEMO_DB if args.demo else settings.db_path)
    tools = None
    if with_tools:
        tools = fixture_toolbox(load_specs(root / DEMO_FIXTURE), utcnow()) if args.demo else live_toolbox(settings)
    return DeskContext(
        policy=policy,
        db=DeskDB(db_path),
        prompts=PromptBook(root / "prompts"),
        tools=tools,
        llm=LLMClient.from_settings(settings),
        cli="python main.py --demo" if args.demo else "python main.py",
    )


@contextmanager
def open_desk(args: argparse.Namespace, *, with_tools: bool) -> Iterator[DeskContext]:
    ctx = build_context(args, with_tools=with_tools)
    try:
        yield ctx
    finally:
        ctx.db.close()


def _operator() -> str:
    try:
        return getpass.getuser()
    except Exception:  # no login name in some containers
        return "human"


def cmd_run(args: argparse.Namespace) -> int:
    with open_desk(args, with_tools=True) as ctx:
        data = ("SYNTHETIC fixtures (fixtures/demo_market.json), not market data" if args.demo
                else "live DexScreener, Jupiter quotes, Solana RPC")
        llm = f"on ({ctx.llm.model})" if isinstance(ctx.llm, LLMClient) else "off (coded rules only)"
        print(f"paper desk | mode PAPER | data: {data} | LLM: {llm} | db: {ctx.db.path}")
        summary = Orchestrator(ctx).loop(args.cycles, args.interval)
    print()
    print(summary)
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    with open_desk(args, with_tools=False) as ctx:
        approve(ctx, args.lead, by=_operator())
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    with open_desk(args, with_tools=False) as ctx:
        reject(ctx, args.lead, by=_operator())
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    with open_desk(args, with_tools=False) as ctx:
        print(format_status(ctx))
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    day = date.fromisoformat(args.date) if args.date else utcnow().date()
    with open_desk(args, with_tools=False) as ctx:
        print(Head(ctx).daily_summary(day))
    return 0


def cmd_clear_halt(args: argparse.Namespace) -> int:
    with open_desk(args, with_tools=False) as ctx:
        if not ctx.db.is_halted():
            print("the desk is not halted")
            return 0
        reason = ctx.db.halt_reason()
        Head(ctx).clear_halt(by=_operator(), note=args.reason)
    print(f"halt cleared (was: {reason}); the daily loss limit now counts from current paper equity")
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    lead_id = parse_lead_ref(args.lead) if args.lead else None
    with open_desk(args, with_tools=False) as ctx:
        for row in ctx.db.events(lead_id=lead_id, limit=args.limit):
            lead = f"LEAD-{row['lead_id']}" if row["lead_id"] is not None else "-"
            print(f"{row['timestamp']}  {lead:<9} {row['role']:<12} {row['action']:<18} {row['payload']}")
    return 0


def build_parser(default_root: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main.py", description="Paper-only Solana memecoin desk.")
    parser.add_argument("--root", type=Path, default=default_root, help=argparse.SUPPRESS)
    parser.add_argument("--db", type=Path, help="SQLite file (default: DESK_DB_PATH, else data/desk.sqlite3)")
    parser.add_argument("--demo", action="store_true",
                        help=f"use synthetic offline fixtures and {DEMO_DB} instead of live data")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run the paper loop, then print the run summary")
    run.add_argument("--cycles", type=int, help="stop after N cycles (default: until Ctrl-C)")
    run.add_argument("--interval", type=float, default=60.0, help="seconds between cycles (default 60)")
    run.set_defaults(handler=cmd_run)

    for name, handler, text in (("approve", cmd_approve, "approve a staged lead: paper fill at its quoted price"),
                                ("reject", cmd_reject, "reject a staged lead")):
        command = commands.add_parser(name, help=text)
        command.add_argument("lead", metavar="LEAD-ID", help="for example LEAD-12")
        command.set_defaults(handler=handler)

    commands.add_parser("status", help="halt flag, approvals, positions, latest leads").set_defaults(handler=cmd_status)

    summary = commands.add_parser("summary", help="Head's daily summary from the tables")
    summary.add_argument("--date", help="UTC day as YYYY-MM-DD (default: today)")
    summary.set_defaults(handler=cmd_summary)

    clear = commands.add_parser("clear-halt", help="human-only: clear a daily-loss or failed-send halt")
    clear.add_argument("--reason", required=True, help="why it is safe to resume (logged)")
    clear.set_defaults(handler=cmd_clear_halt)

    events = commands.add_parser("events", help="print the audit log")
    events.add_argument("--lead", help="only events for this lead, e.g. LEAD-12")
    events.add_argument("--limit", type=int, default=50)
    events.set_defaults(handler=cmd_events)
    return parser


def main(argv: list[str] | None = None, default_root: Path | None = None) -> int:
    root = default_root or Path(__file__).resolve().parents[2]
    args = build_parser(root).parse_args(argv)
    try:
        return args.handler(args)
    except (PolicyError, KeyMaterialError) as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        return 2
    except (ApprovalError, HandoffError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
