"""Required: a policy halt blocks new leads until a human clears it."""

import pytest

from desk.approval import ApprovalError, approve
from desk.db import HaltedError, Status
from desk.orchestrator import Orchestrator
from desk.roles import Head
from desk.tools.fixture import TokenSpec

from conftest import lead_for


def test_failed_send_limit_halts_and_blocks_new_leads(make_desk):
    ctx = make_desk([TokenSpec("AAA")])
    head = Head(ctx)
    for attempt in range(ctx.policy.max_failed_sends):
        assert not ctx.db.is_halted(), f"halted too early, after {attempt} failed sends"
        head.record_failed_send(lead_id=None, detail="simulated broadcast failure")
    assert ctx.db.is_halted()
    assert "failed sends" in ctx.db.halt_reason()

    Orchestrator(ctx).run_cycle()

    assert ctx.db.leads() == []
    assert ctx.tools.market.discover_calls == 0, "Search must be skipped while halted"
    assert any(row["action"] == "search_skipped" for row in ctx.db.events())
    # The refusal also holds below the orchestrator, at the storage layer.
    with pytest.raises(HaltedError):
        ctx.db.insert_lead(ts=ctx.ts(), mint=TokenSpec("AAA").mint, pair="p", liquidity_usd=1.0,
                           age_minutes=1.0, source="test", market_json="{}")


def test_daily_loss_halts_search_and_approvals(make_desk, clock):
    crashing, newcomer = TokenSpec("CRASH"), TokenSpec("LATE")
    ctx = make_desk([crashing, TokenSpec("SPARE")], daily_loss_halt_pct=2.0, max_trade_sol=1.0)
    desk = Orchestrator(ctx)
    desk.run_cycle()
    approve(ctx, lead_for(ctx, crashing).ref)          # 0.3 SOL paper position

    crashing.tokens_per_sol *= 10                       # price falls 90%; liquidity and impact unchanged
    clock.advance(minutes=1)
    desk.run_cycle()                                    # Exit marks roughly -0.27 SOL unrealized
    assert ctx.db.get_position(lead_for(ctx, crashing).lead_id).unrealized < -0.25
    assert not ctx.db.is_halted(), "the loss is only seen at the next cycle's halt check"

    ctx.tools.market.book.add(newcomer)
    clock.advance(minutes=1)
    desk.run_cycle()

    assert ctx.db.is_halted()
    assert "daily paper loss" in ctx.db.halt_reason()
    assert all(lead.mint != newcomer.mint for lead in ctx.db.leads())
    spare = ctx.db.leads(Status.AWAITING)
    assert spare, "the other staged lead is still waiting"
    with pytest.raises(ApprovalError, match="halted"):
        approve(ctx, spare[0].ref)


def test_human_clear_halt_resumes_search(make_desk):
    ctx = make_desk([TokenSpec("AAA")])
    head = Head(ctx)
    for _ in range(ctx.policy.max_failed_sends):
        head.record_failed_send(lead_id=None, detail="simulated")
    Orchestrator(ctx).run_cycle()
    assert ctx.db.leads() == []

    head.clear_halt(by="tester", note="investigated the failed sends")
    assert not ctx.db.is_halted()
    Orchestrator(ctx).run_cycle()

    assert len(ctx.db.leads()) == 1
    cleared = [row for row in ctx.db.events() if row["action"] == "halt_cleared"]
    assert cleared and "tester" in cleared[0]["payload"]


def test_halt_stays_until_a_human_clears_it(make_desk, clock):
    ctx = make_desk([TokenSpec("AAA")])
    head = Head(ctx)
    for _ in range(ctx.policy.max_failed_sends):
        head.record_failed_send(lead_id=None, detail="simulated")
    clock.advance(days=1)  # a new UTC day resets the counters, not the halt
    Orchestrator(ctx).run_cycle()
    assert ctx.db.is_halted()
    assert ctx.db.leads() == []
