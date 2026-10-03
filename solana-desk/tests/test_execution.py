"""Desk side of real trading: orders, fills, failures, expiry, live exits, and equity per mode."""

from dataclasses import replace
from datetime import timedelta

import pytest

from desk import execution
from desk.approval import ApprovalError, approve
from desk.clock import iso
from desk.db import OrderStatus, Status
from desk.orchestrator import Orchestrator
from desk.roles import Exit, Head
from desk.tools.fixture import TokenSpec

from conftest import lead_for, signer_online


def staged(make_desk, *specs, mode="dry_run", **overrides):
    ctx = make_desk(list(specs), trading_mode=mode, **overrides)
    signer_online(ctx)
    Orchestrator(ctx).run_cycle()
    return ctx


def test_dry_run_size_is_capped_at_max_trade_sol(make_desk):
    spec = TokenSpec("SIZE")
    ctx = staged(make_desk, spec)                       # 2 SOL wallet: 3% is 0.06, capped at 0.05
    assert lead_for(ctx, spec).quote()["size_sol"] == pytest.approx(0.05)


def test_dry_run_size_follows_the_wallet_below_the_cap(make_desk):
    spec = TokenSpec("SMALL")
    ctx = make_desk([spec], trading_mode="dry_run")
    signer_online(ctx, lamports=1_000_000_000)          # 1 SOL wallet: 3% is 0.03
    Orchestrator(ctx).run_cycle()
    assert lead_for(ctx, spec).quote()["size_sol"] == pytest.approx(0.03)


def test_unknown_wallet_balance_means_no_trade(make_desk):
    spec = TokenSpec("BLIND")
    ctx = make_desk([spec], trading_mode="dry_run")     # no signer has reported in
    Orchestrator(ctx).run_cycle()
    lead = lead_for(ctx, spec)
    assert lead.status == Status.REJECTED and "wallet balance unknown" in lead.reject_reason


def test_approval_places_a_buy_order_instead_of_a_position(make_desk):
    spec = TokenSpec("ORDER")
    ctx = staged(make_desk, spec)
    order = approve(ctx, lead_for(ctx, spec).ref, by="tester")
    assert (order.side, order.mode, order.status, int(order.amount)) == ("buy", "dry_run", "pending", 50_000_000)
    assert lead_for(ctx, spec).status == Status.ORDERED
    assert ctx.db.positions() == [], "nothing is booked until the signer reports a fill"


@pytest.mark.parametrize(("setup", "message"), [
    (lambda ctx: ctx.db.set_state("signer_heartbeat_at", iso(ctx.now() - timedelta(minutes=5))), "not running"),
    (lambda ctx: ctx.db.set_state("signer_mode", "live"), "signer runs in live mode"),
])
def test_approval_needs_a_matching_running_signer(make_desk, setup, message):
    spec = TokenSpec("NOSIGNER")
    ctx = staged(make_desk, spec)
    setup(ctx)
    with pytest.raises(ApprovalError, match=message):
        approve(ctx, lead_for(ctx, spec).ref)
    assert lead_for(ctx, spec).status == Status.AWAITING


def test_simulated_fill_opens_a_dry_run_position(make_desk):
    spec = TokenSpec("SIM")
    ctx = staged(make_desk, spec)
    order = approve(ctx, lead_for(ctx, spec).ref)
    ctx.db.update_order(order.order_id, OrderStatus.PENDING, OrderStatus.WORKING, ctx.ts())
    execution.apply_buy_fill(ctx.db, order.order_id, lamports_spent=50_010_000, tokens_received=49_000_000_000,
                             decimals=6, tx_sig=None, simulated=True, fee_lamports=None, ts=ctx.ts())
    position = ctx.db.get_position(lead_for(ctx, spec).lead_id)
    assert (position.mode, position.size_sol, position.token_amount) == ("dry_run", 0.05001, "49000000000")
    assert ctx.db.get_order(order.order_id).status == OrderStatus.SIMULATED
    assert lead_for(ctx, spec).status == Status.FILLED


def test_failed_sends_count_and_halt_at_the_limit(make_desk):
    specs = [TokenSpec(f"F{i}") for i in range(3)]
    ctx = staged(make_desk, *specs)
    for spec in specs:
        order = approve(ctx, lead_for(ctx, spec).ref)
        ctx.db.update_order(order.order_id, OrderStatus.PENDING, OrderStatus.SENDING, ctx.ts())
        execution.apply_failure(ctx.db, ctx.policy, order.order_id, status=OrderStatus.FAILED,
                                detail="blockhash expired", ts=ctx.ts(), sent=True)
        assert lead_for(ctx, spec).status == Status.REJECTED
    assert ctx.db.is_halted() and "3 failed sends" in ctx.db.halt_reason()


def test_orders_nobody_picks_up_expire(make_desk, clock):
    spec = TokenSpec("SLOWPOKE")
    ctx = staged(make_desk, spec)
    order = approve(ctx, lead_for(ctx, spec).ref)
    clock.advance(seconds=ctx.policy.order_timeout_seconds + 1)
    signer_online(ctx)
    Head(ctx).start_cycle()
    assert ctx.db.get_order(order.order_id).status == OrderStatus.EXPIRED
    lead = lead_for(ctx, spec)
    assert lead.status == Status.REJECTED and "did not pick it up" in lead.reject_reason


def test_slots_count_buys_still_in_flight(make_desk):
    specs = [TokenSpec(f"S{i}") for i in range(3)]
    ctx = staged(make_desk, *specs, max_open_positions=3)
    ctx.policy = replace(ctx.policy, max_open_positions=2)
    approve(ctx, lead_for(ctx, specs[0]).ref)
    approve(ctx, lead_for(ctx, specs[1]).ref)
    with pytest.raises(ApprovalError, match="max_open_positions"):
        approve(ctx, lead_for(ctx, specs[2]).ref)


def test_daily_buy_cap(make_desk):
    specs = [TokenSpec(f"B{i}") for i in range(3)]
    ctx = staged(make_desk, *specs, max_buys_per_day=2)
    approve(ctx, lead_for(ctx, specs[0]).ref)
    approve(ctx, lead_for(ctx, specs[1]).ref)
    with pytest.raises(ApprovalError, match="max_buys_per_day"):
        approve(ctx, lead_for(ctx, specs[2]).ref)


def live_position(make_desk, clock, spec):
    ctx = staged(make_desk, spec, mode="live")
    order = approve(ctx, lead_for(ctx, spec).ref)
    ctx.db.update_order(order.order_id, OrderStatus.PENDING, OrderStatus.SENDING, ctx.ts(), tx_sig="BuySig")
    execution.apply_buy_fill(ctx.db, order.order_id, lamports_spent=50_000_000, tokens_received=49_000_000_000,
                             decimals=6, tx_sig="BuySig", simulated=False, fee_lamports=5000, ts=ctx.ts())
    clock.advance(minutes=1)
    signer_online(ctx, lamports=1_950_000_000)
    return ctx, lead_for(ctx, spec).lead_id


def test_live_exit_places_a_sell_order_and_the_fill_closes_the_position(make_desk, clock):
    spec = TokenSpec("LIVEEXIT")
    ctx, lead_id = live_position(make_desk, clock, spec)
    assert ctx.db.get_lead(lead_id).tx_sig == "BuySig"
    spec.liquidity_usd = 10_000.0                       # liquidity collapses: exit trigger
    Exit(ctx).run()

    position = ctx.db.get_position(lead_id)
    assert position.is_open and position.sell_order_id is not None, "live: a sell order, not a book entry"
    sell = ctx.db.get_order(position.sell_order_id)
    assert (sell.side, sell.mode, sell.amount, sell.status) == ("sell", "live", "49000000000", "pending")
    assert sell.reason.startswith("liquidity_drop")
    Exit(ctx).run()
    assert len([o for o in ctx.db.recent_orders(10) if o.side == "sell"]) == 1, "no second sell while one is open"

    ctx.db.update_order(sell.order_id, OrderStatus.PENDING, OrderStatus.SENDING, ctx.ts(), tx_sig="SellSig")
    execution.apply_sell_fill(ctx.db, sell.order_id, lamports_received=30_000_000, tokens_sold=49_000_000_000,
                              tx_sig="SellSig", fee_lamports=5000, ts=ctx.ts())
    position = ctx.db.get_position(lead_id)
    assert not position.is_open and position.realized_sol == pytest.approx(-0.02)
    assert ctx.db.get_lead(lead_id).status == Status.CLOSED


def test_failed_sell_is_retried_on_the_next_check(make_desk, clock):
    spec = TokenSpec("RETRY")
    ctx, lead_id = live_position(make_desk, clock, spec)
    spec.liquidity_usd = 10_000.0
    Exit(ctx).run()
    first = ctx.db.get_position(lead_id).sell_order_id
    ctx.db.update_order(first, OrderStatus.PENDING, OrderStatus.WORKING, ctx.ts())
    execution.apply_failure(ctx.db, ctx.policy, first, status=OrderStatus.FAILED, detail="no route", ts=ctx.ts())
    assert ctx.db.get_position(lead_id).sell_order_id is None
    clock.advance(minutes=ctx.policy.exit.check_interval_minutes)
    Exit(ctx).run()
    assert ctx.db.get_position(lead_id).sell_order_id not in (None, first)


def test_dead_lead_with_live_position_is_sold_not_written_off(make_desk, clock):
    spec = TokenSpec("DRIFTLIVE")
    ctx, lead_id = live_position(make_desk, clock, spec)
    ctx.tools.market.base_mint_override[spec.pair] = TokenSpec("SOMETHINGELSE").mint
    Exit(ctx).run()
    assert ctx.db.get_lead(lead_id).status == Status.DEAD
    position = ctx.db.get_position(lead_id)
    assert position.is_open and position.sell_order_id is not None, "real tokens are sold, not booked at zero"


def test_live_position_without_an_exit_route_is_marked_at_zero(make_desk, clock):
    spec = TokenSpec("STUCKLIVE")
    ctx, lead_id = live_position(make_desk, clock, spec)
    spec.sell_route = False
    Exit(ctx).run()
    position = ctx.db.get_position(lead_id)
    assert position.is_open and position.unrealized == pytest.approx(-position.size_sol)


def test_equity_per_mode(make_desk, clock):
    spec = TokenSpec("EQ")
    ctx, lead_id = live_position(make_desk, clock, spec)
    Exit(ctx).run()                                     # marks the position at its exit quote
    position = ctx.db.get_position(lead_id)
    mark = position.size_sol + position.unrealized
    assert ctx.equity_sol() == pytest.approx(1.95 + mark), "live: wallet plus positions at exit quotes"
    ctx.db.set_state("signer_heartbeat_at", "")
    ctx.db.set_state("wallet_lamports", "")
    assert ctx.equity_sol() is None


def test_live_daily_loss_halt_uses_the_wallet(make_desk, clock):
    ctx = make_desk([], trading_mode="live")
    signer_online(ctx, lamports=2_000_000_000)
    head = Head(ctx)
    assert not head.apply_halt_rules()                  # sets today's baseline: 2 SOL
    signer_online(ctx, lamports=1_550_000_000)          # down 22.5%
    assert head.apply_halt_rules()
    assert "daily live loss 22.50% >= 20%" in ctx.db.halt_reason()


def test_halted_desk_stops_buys_but_not_sells(make_desk, clock):
    spec = TokenSpec("HALTSELL")
    ctx, lead_id = live_position(make_desk, clock, spec)
    Head(ctx).halt("test halt")
    spec.liquidity_usd = 10_000.0
    Exit(ctx).run()
    assert ctx.db.get_position(lead_id).sell_order_id is not None
