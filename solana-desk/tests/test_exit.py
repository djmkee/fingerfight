"""Exit: paper positions close on liquidity drop, impact spike, authority change, or time stop."""

import json

import pytest

from desk.approval import approve
from desk.db import Status
from desk.orchestrator import Orchestrator
from desk.roles import Exit
from desk.tools.fixture import TokenSpec

from conftest import ScriptedLLM, agree, lead_for


def holding(make_desk, spec, **overrides):
    ctx = make_desk([spec], **overrides)
    Orchestrator(ctx).run_cycle()
    lead = lead_for(ctx, spec)
    approve(ctx, lead.ref)
    return ctx, lead.lead_id


@pytest.mark.parametrize(("change", "trigger"), [
    (lambda spec: setattr(spec, "liquidity_usd", 20_000.0), "liquidity_drop"),
    (lambda spec: setattr(spec, "sell_impact_pct", 12.0), "impact_spike"),
    (lambda spec: setattr(spec, "freeze_authority", True), "authority_change"),
])
def test_trigger_closes_the_paper_position(make_desk, clock, change, trigger):
    spec = TokenSpec("HELD")
    ctx, lead_id = holding(make_desk, spec)
    change(spec)
    clock.advance(minutes=1)

    Exit(ctx).run()

    position = ctx.db.get_position(lead_id)
    assert not position.is_open
    assert position.exit_reason.startswith(trigger)
    lead = ctx.db.get_lead(lead_id)
    assert lead.status == Status.CLOSED
    assert json.loads(lead.recheck_json)["triggers"] == [position.exit_reason]  # the claim's evidence is on the row
    proceeds = position.paper_exit * int(position.token_amount) / 10**position.token_decimals
    assert proceeds > 0, "an executable exit quote existed, so the paper exit uses it"
    assert position.realized_sol == pytest.approx(proceeds - position.size_sol)


def test_time_stop(make_desk, clock):
    spec = TokenSpec("OLD")
    ctx, lead_id = holding(make_desk, spec)
    clock.advance(minutes=ctx.policy.exit.time_stop_minutes)
    Exit(ctx).run()
    assert ctx.db.get_position(lead_id).exit_reason.startswith("time_stop")


def test_hold_marks_to_the_exit_quote_and_waits_for_the_interval(make_desk, clock):
    spec = TokenSpec("CALM")
    ctx, lead_id = holding(make_desk, spec)
    clock.advance(minutes=1)
    Exit(ctx).run()

    position = ctx.db.get_position(lead_id)
    assert position.is_open
    exit_out = int(position.token_amount) / 10**spec.decimals / spec.tokens_per_sol * (1 - spec.sell_impact_pct / 100)
    assert position.unrealized == pytest.approx(exit_out - position.size_sol, rel=1e-6)
    assert position.unrealized < 0, "a round trip at quoted prices loses the price impact"
    recheck = json.loads(ctx.db.get_lead(lead_id).recheck_json)
    assert recheck["pair"]["base_mint"] == spec.mint
    assert recheck["exit_quote"]["price_impact_pct"] == pytest.approx(spec.sell_impact_pct)
    assert recheck["mint"]["freeze_authority"] is None and recheck["triggers"] == []

    calls = len(ctx.tools.quoter.calls)
    clock.advance(minutes=1)
    Exit(ctx).run()
    assert len(ctx.tools.quoter.calls) == calls, "not due again before check_interval_minutes"


def test_no_exit_quote_closes_at_zero_after_repeated_failures(make_desk, clock):
    spec = TokenSpec("STUCK")
    ctx, lead_id = holding(make_desk, spec)
    spec.sell_route = False
    for _ in range(ctx.policy.exit.max_recheck_failures):
        clock.advance(minutes=ctx.policy.exit.check_interval_minutes)
        Exit(ctx).run()

    position = ctx.db.get_position(lead_id)
    assert not position.is_open
    assert position.exit_reason.startswith("impact_spike: no exit quote")
    assert (position.paper_exit, position.realized_sol) == (0.0, pytest.approx(-position.size_sol))


def test_llm_may_add_an_exit_only_for_a_listed_trigger(make_desk, clock):
    calm, other = TokenSpec("CALM"), TokenSpec("OTHER")
    reasons = {calm.symbol: "time_stop held long enough per supplied fields",
               other.symbol: "price looks toppy"}
    by_mint = {calm.mint: calm.symbol, other.mint: other.symbol}
    honest = agree()

    def respond(stage, leads):
        if stage != "exit":
            return honest(stage, leads)
        return "\n".join(f"LEAD-{lead['lead_id']} | {lead['mint']} | exit | exit | {reasons[by_mint[lead['mint']]]}"
                         for lead in leads)

    ctx = make_desk([calm, other], llm=ScriptedLLM(respond))
    Orchestrator(ctx).run_cycle()
    for spec in (calm, other):
        approve(ctx, lead_for(ctx, spec).ref)
    clock.advance(minutes=1)
    Exit(ctx).run()

    assert ctx.db.get_position(lead_for(ctx, calm).lead_id).exit_reason.endswith("(LLM)")
    assert ctx.db.get_position(lead_for(ctx, other).lead_id).is_open
    assert any(row["action"] == "llm_exit_ignored" for row in ctx.db.events())


def test_realized_loss_counts_toward_equity(make_desk, clock):
    spec = TokenSpec("LOSS")
    ctx, lead_id = holding(make_desk, spec)
    spec.liquidity_usd = 1_000.0
    clock.advance(minutes=1)
    Exit(ctx).run()
    position = ctx.db.get_position(lead_id)
    assert ctx.equity_sol() == pytest.approx(ctx.policy.paper_equity_sol + position.realized_sol)
    assert position.realized_sol < 0
