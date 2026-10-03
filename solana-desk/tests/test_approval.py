"""Required: a paper approval needs no key. Plus the rest of the human gate."""

import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import replace

import pytest

from desk.approval import ApprovalError, approve, reject
from desk.constants import LAMPORTS_PER_SOL
from desk.db import Status
from desk.orchestrator import Orchestrator
from desk.tools.fixture import TokenSpec

from conftest import ROOT, lead_for


class NoTools:
    """Any tool access during approval is a bug: approval must work from the stored quote alone."""

    def __getattr__(self, name):
        raise AssertionError(f"approval touched tools.{name}")


def staged(make_desk, *specs, **overrides):
    ctx = make_desk(list(specs), **overrides)
    Orchestrator(ctx).run_cycle()
    return ctx


def test_paper_approve_needs_no_key_and_fills_at_the_quoted_price(make_desk):
    spec = TokenSpec("FILL")
    ctx = staged(make_desk, spec)
    lead = lead_for(ctx, spec)
    plan = lead.quote()
    ctx.tools = NoTools()

    position = approve(ctx, lead.ref, by="tester")

    lead = ctx.db.get_lead(lead.lead_id)
    assert (lead.status, lead.human_decision, lead.tx_sig) == (Status.PAPER_FILLED, "approve", None)
    assert position.is_open and position.mint == spec.mint
    assert position.paper_entry == pytest.approx(plan["entry_price_sol"])
    assert position.size_sol == pytest.approx(ctx.policy.paper_equity_sol * ctx.policy.max_position_pct / 100)
    assert position.token_amount == plan["out_amount"]
    expected_price = (int(plan["in_amount"]) / LAMPORTS_PER_SOL) / (int(plan["out_amount"]) / 10**plan["token_decimals"])
    assert position.paper_entry == pytest.approx(expected_price)


def query_one(db, sql, *params):
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute(sql, params).fetchone()[0]


def test_cli_paper_approve_runs_in_a_clean_environment_without_the_signer(tmp_path):
    """End to end through main.py, with no wallet, LLM, or RPC settings anywhere."""
    # A private project root (config, prompts, fixtures) so no local .env is ever read.
    root = tmp_path / "root"
    for name in ("config", "prompts", "fixtures"):
        shutil.copytree(ROOT / name, root / name)
    # Only what Python needs to start (Windows needs SYSTEMROOT); nothing the desk reads.
    env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if name in os.environ}
    db = tmp_path / "demo.sqlite3"
    run = subprocess.run([sys.executable, str(ROOT / "main.py"), "--root", str(root), "--demo", "--db", str(db),
                          "run", "--cycles", "1", "--interval", "0"],
                         env=env, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    lead_id = query_one(db, "SELECT lead_id FROM leads WHERE status = 'awaiting_approval' ORDER BY lead_id LIMIT 1")

    probe = (
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
        "from pathlib import Path\n"
        "from desk.cli import main\n"
        f"code = main(['--demo', '--db', {str(db)!r}, 'approve', 'LEAD-{lead_id}'], default_root=Path({str(root)!r}))\n"
        "assert code == 0, code\n"
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] in ('signer', 'solders'))\n"
        "assert not loaded, loaded\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert query_one(db, "SELECT status FROM leads WHERE lead_id = ?", lead_id) == Status.PAPER_FILLED


def test_reject_closes_the_lead(make_desk):
    spec = TokenSpec("NOPE")
    ctx = staged(make_desk, spec)
    reject(ctx, lead_for(ctx, spec).ref, by="tester")
    lead = lead_for(ctx, spec)
    assert (lead.status, lead.human_decision) == (Status.REJECTED, "reject")
    assert lead.reject_reason == "human/rejected: by tester"
    assert ctx.db.positions() == []


@pytest.mark.parametrize("ref", ["LEAD-999", "12", "lead-x"])
def test_unknown_or_malformed_lead_ids_are_refused(make_desk, ref):
    ctx = staged(make_desk, TokenSpec("AAA"))
    with pytest.raises(ApprovalError):
        approve(ctx, ref)


def test_only_awaiting_leads_can_be_decided(make_desk):
    bad = TokenSpec("BAD", freeze_authority=True)
    ctx = staged(make_desk, bad)
    lead = lead_for(ctx, bad)
    with pytest.raises(ApprovalError, match="not awaiting_approval"):
        approve(ctx, lead.ref)
    with pytest.raises(ApprovalError, match="not awaiting_approval"):
        reject(ctx, lead.ref)


def test_stale_quote_expires_instead_of_filling(make_desk, clock):
    spec = TokenSpec("SLOW")
    ctx = staged(make_desk, spec)
    clock.advance(minutes=ctx.policy.approval_ttl_minutes + 1)
    with pytest.raises(ApprovalError, match="expired"):
        approve(ctx, lead_for(ctx, spec).ref)
    assert lead_for(ctx, spec).status == Status.EXPIRED
    assert ctx.db.positions() == []


def test_the_loop_expires_undecided_quotes(make_desk, clock):
    spec = TokenSpec("IGNORED")
    ctx = staged(make_desk, spec)
    clock.advance(minutes=ctx.policy.approval_ttl_minutes + 1)
    Orchestrator(ctx).run_cycle()
    assert lead_for(ctx, spec).status == Status.EXPIRED


def test_open_position_cap_is_enforced_at_approval(make_desk):
    first, second = TokenSpec("ONE"), TokenSpec("TWO")
    ctx = staged(make_desk, first, second, max_open_positions=2)
    approve(ctx, lead_for(ctx, first).ref)
    ctx.policy = replace(ctx.policy, max_open_positions=1)
    with pytest.raises(ApprovalError, match="max_open_positions"):
        approve(ctx, lead_for(ctx, second).ref)
    assert lead_for(ctx, second).status == Status.AWAITING
