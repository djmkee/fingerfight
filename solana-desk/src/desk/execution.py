"""Orders and fills: how approvals and exits become trades in dry_run and live modes.

The agent process places orders. The separate signer process, the only one that holds the
wallet key, executes them and reports results through apply_buy_fill, apply_sell_fill and
apply_failure. Each of those updates the order, the position and the lead in one transaction,
so the books never disagree with the orders. Paper mode never creates orders: approvals and
exits are book entries at quoted prices.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .clock import iso, parse_iso
from .constants import LAMPORTS_PER_SOL
from .db import DeskDB, Lead, OrderStatus, Position, Status
from .handoff import Handoff
from .policy import Policy

if TYPE_CHECKING:
    from .context import DeskContext

ORDER_MODES = ("dry_run", "live")
SIGNER_STALE_SECONDS = 30   # a signer heartbeat older than this means the signer is offline
MIN_TRADE_SOL = 0.005       # smaller trades lose too much to fees to be worth placing


class OrderError(RuntimeError):
    """An order could not be placed."""


@dataclass(frozen=True)
class SignerStatus:
    online: bool
    mode: str | None
    wallet: str | None
    wallet_lamports: int | None
    heartbeat_at: str | None
    error: str | None

    @property
    def wallet_sol(self) -> float | None:
        return None if self.wallet_lamports is None else self.wallet_lamports / LAMPORTS_PER_SOL


def signer_status(db: DeskDB, now: datetime) -> SignerStatus:
    """What the signer last reported about itself (it writes these keys every few seconds)."""
    beat = db.get_state("signer_heartbeat_at")
    lamports = db.get_state("wallet_lamports")
    return SignerStatus(
        online=bool(beat) and (now - parse_iso(beat)).total_seconds() <= SIGNER_STALE_SECONDS,
        mode=db.get_state("signer_mode") or None,
        wallet=db.get_state("wallet_address") or None,
        wallet_lamports=int(lamports) if lamports else None,
        heartbeat_at=beat or None,
        error=db.get_state("signer_error") or None,
    )


def equity_sol(ctx: "DeskContext") -> float | None:
    """Equity for sizing and the daily loss limit, or None when the wallet balance is unknown."""
    db, policy, mode = ctx.db, ctx.policy, ctx.policy.trading_mode
    if mode == "paper":
        return policy.paper_equity_sol + db.realized_total("paper") + db.unrealized_total("paper")
    wallet = signer_status(db, ctx.now()).wallet_sol
    if wallet is None:
        return None
    if mode == "dry_run":  # nothing moved: the real balance plus the simulated results
        return wallet + db.realized_total("dry_run") + db.unrealized_total("dry_run")
    # live: what open positions cost already left the wallet; count them at their exit quotes
    return wallet + db.open_value_total("live")


def position_size_sol(ctx: "DeskContext") -> float | None:
    """max_position_pct of equity, capped at max_trade_sol; None when equity is unknown."""
    equity = equity_sol(ctx)
    if equity is None:
        return None
    return round(min(equity * ctx.policy.max_position_pct / 100, ctx.policy.max_trade_sol), 9)


def free_slots(ctx: "DeskContext") -> int:
    """Open position slots left, counting buys that are approved but not filled yet."""
    db = ctx.db
    return (ctx.policy.max_open_positions - db.count_open_positions(ctx.policy.trading_mode)
            - db.count_leads(Status.ORDERED))


def _day_start(now: datetime) -> str:
    return iso(now.replace(hour=0, minute=0, second=0, microsecond=0))


def buys_today(ctx: "DeskContext") -> int:
    """Buys placed this UTC day, for max_buys_per_day. Failed sends count; refusals do not."""
    since, db, mode = _day_start(ctx.now()), ctx.db, ctx.policy.trading_mode
    if mode == "paper":
        return int(db.scalar("SELECT COUNT(*) FROM positions WHERE mode = 'paper' AND opened_at >= ?", (since,)))
    counted = (*OrderStatus.OPEN, OrderStatus.FILLED, OrderStatus.SIMULATED, OrderStatus.FAILED)
    return db.count_orders(side="buy", since=since, statuses=counted)


def place_buy(ctx: "DeskContext", lead: Lead, plan: dict, by: str) -> int:
    """Turn an approved lead into a buy order for the signer."""
    mode = ctx.policy.trading_mode
    if mode not in ORDER_MODES:
        raise OrderError(f"no orders in {mode} mode")
    lamports = int(plan["size_lamports"])
    ts = ctx.ts()
    with ctx.db.tx():
        if not ctx.db.transition(lead.lead_id, Status.AWAITING, Status.ORDERED, ts, human_decision="approve"):
            raise OrderError(f"{lead.ref} changed state; no order placed")
        order_id = ctx.db.insert_order(lead_id=lead.lead_id, mint=lead.mint, side="buy", mode=mode,
                                       amount=lamports, reason=f"approved by {by}", ts=ts)
        line = Handoff(lead.lead_id, lead.mint, "approval", "approve",
                       f"by={by} buy order {order_id} for {lamports / LAMPORTS_PER_SOL:g} SOL ({mode})").render()
        ctx.log("approval", "approve", lead.lead_id, by=by, order_id=order_id, mode=mode, line=line)
    ctx.echo(line)
    return order_id


def place_sell(ctx: "DeskContext", position: Position, reason: str) -> int:
    """Ask the signer to sell a live position. The position closes when the sell fills."""
    ts = ctx.ts()
    with ctx.db.tx():
        order_id = ctx.db.insert_order(lead_id=position.lead_id, mint=position.mint, side="sell", mode="live",
                                       amount=position.token_amount, reason=reason, ts=ts)
        ctx.db.update_position(position.lead_id, sell_order_id=order_id)
        ctx.log("exit", "sell_order", position.lead_id, order_id=order_id, reason=reason)
    ctx.echo(f"exit: LEAD-{position.lead_id} sell order {order_id} placed ({reason})")
    return order_id


def cancel_pending_orders(db: DeskDB, lead_id: int, reason: str, ts: str) -> None:
    for order in db.orders([OrderStatus.PENDING]):
        if order.lead_id == lead_id:
            db.update_order(order.order_id, OrderStatus.PENDING, OrderStatus.REFUSED, ts, detail=reason)


def apply_buy_fill(db: DeskDB, order_id: int, *, lamports_spent: int, tokens_received: int, decimals: int,
                   tx_sig: str | None, simulated: bool, fee_lamports: int | None, ts: str) -> None:
    """Record a confirmed (or, in dry run, simulated) buy: order filled, position opened."""
    order = db.get_order(order_id)
    lead = db.get_lead(order.lead_id) if order else None
    if order is None or lead is None or tokens_received <= 0 or lamports_spent <= 0:
        raise ValueError(f"order {order_id}: not a usable buy fill")
    size_sol = lamports_spent / LAMPORTS_PER_SOL
    price = size_sol / (tokens_received / 10**decimals)
    with db.tx():
        if not db.update_order(order_id, (OrderStatus.WORKING, OrderStatus.SENDING),
                               OrderStatus.SIMULATED if simulated else OrderStatus.FILLED, ts,
                               tx_sig=tx_sig, in_amount=str(lamports_spent), out_amount=str(tokens_received),
                               fee_lamports=fee_lamports):
            raise ValueError(f"order {order_id} is not being executed")
        # Tokens that arrived are real even if the lead died meanwhile: always book the position.
        db.open_position(lead_id=lead.lead_id, mint=order.mint, paper_entry=price, size_sol=size_sol,
                         token_amount=tokens_received, token_decimals=decimals,
                         entry_liquidity_usd=lead.liquidity_usd, opened_at=ts, mode=order.mode)
        db.transition(lead.lead_id, Status.ORDERED, Status.FILLED, ts, tx_sig=tx_sig)
        line = Handoff(lead.lead_id, lead.mint, "approval", "approve",
                       f"{'simulated' if simulated else 'filled'} buy order {order_id}: "
                       f"{size_sol:.6f} SOL for {tokens_received / 10**decimals:g} tokens"
                       + (f" tx={tx_sig}" if tx_sig and not simulated else "")).render()
        db.log_event(ts, "signer", "buy_simulated" if simulated else "buy_filled", lead.lead_id,
                     {"order_id": order_id, "line": line})


def apply_sell_fill(db: DeskDB, order_id: int, *, lamports_received: int, tokens_sold: int,
                    tx_sig: str | None, fee_lamports: int | None, ts: str) -> None:
    """Record a confirmed sell: order filled, position closed with what actually came back."""
    order = db.get_order(order_id)
    position = db.get_position(order.lead_id) if order else None
    if order is None or position is None:
        raise ValueError(f"order {order_id}: no position to close")
    tokens = int(position.token_amount) / 10**position.token_decimals
    proceeds = lamports_received / LAMPORTS_PER_SOL
    with db.tx():
        if not db.update_order(order_id, (OrderStatus.WORKING, OrderStatus.SENDING), OrderStatus.FILLED, ts,
                               tx_sig=tx_sig, in_amount=str(tokens_sold), out_amount=str(lamports_received),
                               fee_lamports=fee_lamports):
            raise ValueError(f"order {order_id} is not being executed")
        db.close_position(order.lead_id, ts=ts, paper_exit=proceeds / tokens if tokens else 0.0,
                          realized_sol=proceeds - position.size_sol, exit_reason=order.reason or "sell")
        db.transition(order.lead_id, Status.FILLED, Status.CLOSED, ts)  # a dead lead stays dead
        db.log_event(ts, "signer", "sell_filled", order.lead_id,
                     {"order_id": order_id, "proceeds_sol": proceeds, "cost_sol": position.size_sol,
                      "realized_sol": proceeds - position.size_sol, "tx_sig": tx_sig})


def apply_failure(db: DeskDB, policy: Policy, order_id: int, *, status: str, detail: str, ts: str,
                  sent: bool = False,
                  from_statuses: Iterable[str] = (OrderStatus.WORKING, OrderStatus.SENDING)) -> bool:
    """Close an order that did not execute. A sent transaction that failed counts as a failed send."""
    order = db.get_order(order_id)
    if order is None:
        return False
    with db.tx():
        if not db.update_order(order_id, tuple(from_statuses), status, ts, detail=detail[:500]):
            return False
        if order.side == "buy":
            db.transition(order.lead_id, Status.ORDERED, Status.REJECTED, ts,
                          reject_reason=f"execution/{status}: {detail}"[:500])
        else:
            db.update_position(order.lead_id, sell_order_id=None)  # Exit retries on its next check
        db.log_event(ts, "signer" if status != OrderStatus.EXPIRED else "head", f"order_{status}",
                     order.lead_id, {"order_id": order_id, "side": order.side, "detail": detail[:500]})
        if sent:
            record_failed_send(db, policy, ts, detail, order.lead_id)
    return True


def record_failed_send(db: DeskDB, policy: Policy, ts: str, detail: str, lead_id: int | None = None) -> None:
    """Count a transaction that was sent but failed or never landed; halt at the daily limit."""
    key = f"failed_sends:{ts[:10]}"
    with db.tx():
        count = int(db.get_state(key, "0") or 0) + 1
        db.set_state(key, count)
        db.log_event(ts, "execution", "failed_send", lead_id, {"count": count, "detail": detail[:500]})
        if count >= policy.max_failed_sends and not db.is_halted():
            reason = f"{count} failed sends today >= {policy.max_failed_sends}"
            db.set_halted(reason, ts)
            db.log_event(ts, "execution", "halt", None, {"reason": reason})


def expire_stale_orders(ctx: "DeskContext") -> None:
    """Orders nobody picked up in time are closed, never executed late."""
    cutoff = iso(ctx.now() - timedelta(seconds=ctx.policy.order_timeout_seconds))
    for order in ctx.db.orders([OrderStatus.PENDING]):
        if order.created_at < cutoff:
            apply_failure(ctx.db, ctx.policy, order.order_id, status=OrderStatus.EXPIRED,
                          detail="the signer did not pick it up in time; is it running?", ts=ctx.ts(),
                          from_statuses=(OrderStatus.PENDING,))
