"""Execute the desk's orders. The only code that signs, and only after its own checks.

For every order the signer re-checks the policy limits itself, so a bug or a manipulated
decision upstream cannot push a trade past them:
  1. the order is fresh and its mint matches its lead. Sells of live positions always run (a
     sell only reduces exposure); buys must match trading_mode;
  2. buys: the desk is not halted, the size is within max_trade_sol, slots, buys per day and
     failed sends are within limits, and the token's mint and freeze authority are revoked;
  3. a fresh Jupiter quote; Jupiter builds the swap; check_transaction inspects it, unsigned;
  4. sign, simulate against current chain state, and check what it would do to the wallet;
  5. dry run stops there and books the simulated fill. Live records the signature before the
     first send, then sends, re-broadcasts until confirmed or expired, and books what the chain
     says actually happened.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from desk import execution
from desk.clock import iso, parse_iso, utcnow
from desk.constants import LAMPORTS_PER_SOL, SOL_MINT, TOKEN_2022_PROGRAM, TOKEN_PROGRAM
from desk.db import DeskDB, Order, OrderStatus, Status
from desk.policy import Policy, PolicyError, load_policy
from desk.tools.base import Quote, ToolError
from desk.tools.http import JsonHttp
from desk.tools.jupiter import Jupiter

from .chain import SignerRpc, build_swap
from .config import SignerConfig
from .verify import (Expectation, Refused, account_count, associated_token_address, check_simulation,
                     check_transaction)

TOKEN_PROGRAM_IDS = {"spl-token": TOKEN_PROGRAM, "spl-token-2022": TOKEN_2022_PROGRAM}
HEARTBEAT_SECONDS = 5
DESK_STALE_SECONDS = 90      # supervised: stop when the dashboard has not been seen for this long
CONFIRM_PATIENCE_SECONDS = 180
SIGNATURE_FEE_LAMPORTS = 5000


class SendFailed(RuntimeError):
    """A transaction was sent and definitely did not succeed. Counts as a failed send."""


class StillPending(RuntimeError):
    """A transaction was sent but its fate is not known yet; the order stays `sending`."""


@dataclass(frozen=True)
class Fill:
    lamports_change: int
    tokens_change: int
    fee_lamports: int | None
    rent_lamports: int = 0  # deposited into a token account this transaction created; refunded on close


class Executor:
    def __init__(self, *, config: SignerConfig, keypair: Keypair, rpc: SignerRpc, jupiter: Jupiter,
                 swap_http: JsonHttp, db: DeskDB, policy_path: Path, echo: Callable[[str], None] = print,
                 clock: Callable[[], datetime] = utcnow, sleep: Callable[[float], None] = time.sleep) -> None:
        self.config = config
        self.keypair = keypair
        self.wallet = keypair.pubkey()
        self.rpc = rpc
        self.jupiter = jupiter
        self.swap_http = swap_http
        self.db = db
        self.policy_path = policy_path
        self.echo = lambda line: echo(f"[signer] {line}")
        self.clock = clock
        self.sleep = sleep
        self._unresolved: dict[int, str] = {}  # last problem reported per order, so the console is not flooded

    def ts(self) -> str:
        return iso(self.clock())

    def policy(self) -> Policy:
        return load_policy(self.policy_path)  # re-read for every order: edits apply at once

    # status ------------------------------------------------------------------

    def heartbeat(self, mode: str, error: str = "") -> None:
        lamports: int | None = None
        try:
            lamports = self.rpc.balance(str(self.wallet))
        except ToolError as exc:
            error = error or f"cannot read the wallet balance: {exc}"
        with self.db.tx():
            self.db.set_state("signer_heartbeat_at", self.ts())
            self.db.set_state("signer_mode", mode)
            self.db.set_state("wallet_address", str(self.wallet))
            if lamports is not None:
                self.db.set_state("wallet_lamports", lamports)
            self.db.set_state("signer_error", error)

    def desk_alive(self) -> bool:
        beat = self.db.get_state("desk_heartbeat_at")
        return bool(beat) and (self.clock() - parse_iso(beat)).total_seconds() <= DESK_STALE_SECONDS

    # loop --------------------------------------------------------------------

    def run(self, *, supervised: bool = False, should_stop: Callable[[], bool] = lambda: False) -> None:
        self.echo(f"wallet {self.wallet}; waiting for orders (Ctrl+C to stop)")
        recovered, last_beat = False, 0.0
        while not should_stop():
            try:
                policy, problem = self.policy(), ""
            except PolicyError as exc:
                policy, problem = None, f"policy.yaml is invalid, so nothing will be signed: {exc}"
            if time.monotonic() - last_beat >= HEARTBEAT_SECONDS:
                self.heartbeat(policy.trading_mode if policy else "unknown", problem)
                last_beat = time.monotonic()
            if supervised and not self.desk_alive():
                self.echo("the dashboard is gone; stopping")
                return
            if policy is not None:
                try:
                    if not recovered:
                        self.recover(policy)
                        recovered = True
                    self.process(policy)
                except Exception as exc:  # keep serving; the error is shown in the dashboard
                    self.echo(f"unexpected error: {type(exc).__name__}: {exc}")
                    self.db.set_state("signer_error", f"{type(exc).__name__}: {exc}"[:300])
            self.sleep(self.config.poll_seconds)

    def process(self, policy: Policy) -> None:
        for order in self.db.orders([OrderStatus.SENDING]):
            self.resolve_sending(order, policy)
        for order in self.db.orders([OrderStatus.PENDING]):
            self.execute(order, policy)

    def recover(self, policy: Policy) -> None:
        """After a restart a `working` order was never sent; `sending` ones are resolved from the chain."""
        for order in self.db.orders([OrderStatus.WORKING]):
            execution.apply_failure(self.db, policy, order.order_id, status=OrderStatus.FAILED,
                                    detail="the signer stopped before sending; nothing was sent", ts=self.ts(),
                                    from_statuses=(OrderStatus.WORKING,))

    # one order ---------------------------------------------------------------

    def execute(self, order: Order, policy: Policy) -> None:
        if not self.db.update_order(order.order_id, OrderStatus.PENDING, OrderStatus.WORKING, self.ts()):
            return  # someone else claimed or expired it
        try:
            self.check_order(order, policy)
            if order.side == "buy":
                self.buy(order, policy)
            else:
                self.sell(order, policy)
        except Refused as exc:
            self._fail(order, policy, OrderStatus.REFUSED, str(exc), sent=False)
        except SendFailed as exc:
            self._fail(order, policy, OrderStatus.FAILED, str(exc), sent=True)
        except StillPending as exc:
            self.echo(f"order {order.order_id}: {exc}; checking again shortly")
        except Exception as exc:
            current = self.db.get_order(order.order_id)
            if current is not None and current.status == OrderStatus.SENDING:
                # Something broke after the send: the chain decides later, never a guess now.
                self.echo(f"order {order.order_id}: {type(exc).__name__}: {exc}; will check the chain")
            else:
                self._fail(order, policy, OrderStatus.FAILED, f"{type(exc).__name__}: {exc}", sent=False)

    def _fail(self, order: Order, policy: Policy, status: str, detail: str, *, sent: bool) -> None:
        execution.apply_failure(self.db, policy, order.order_id, status=status, detail=detail, ts=self.ts(), sent=sent)
        self.echo(f"order {order.order_id} ({order.side} LEAD-{order.lead_id}) {status}: {detail}")

    def check_order(self, order: Order, policy: Policy) -> None:
        age = (self.clock() - parse_iso(order.created_at)).total_seconds()
        if age > self.config.order_max_age_seconds:
            raise Refused(f"the order is {age:.0f} s old; orders are never executed late")
        lead = self.db.get_lead(order.lead_id)
        if lead is None or lead.mint != order.mint:
            raise Refused("the order's mint does not match its lead")
        if order.side == "sell":
            # Tokens bought with real SOL are sold for real whatever trading_mode says now:
            # a sell only ever reduces exposure. Halts stop buys, never exits.
            position = self.db.get_position(order.lead_id)
            if order.mode != "live" or position is None or not position.is_open or position.mode != "live":
                raise Refused("there is no open live position to sell")
            if position.sell_order_id != order.order_id:
                raise Refused("the position is not waiting for this sell order")
            return
        if policy.trading_mode not in execution.ORDER_MODES:
            raise Refused("trading_mode is paper; the signer buys nothing")
        if order.mode != policy.trading_mode:
            raise Refused(f"the order was placed in {order.mode} mode but trading_mode is now {policy.trading_mode}")
        if self.db.is_halted():
            raise Refused(f"the desk is halted ({self.db.halt_reason()})")
        if lead.status != Status.ORDERED:
            raise Refused(f"the lead is {lead.status}, not ordered")
        cap = round(policy.max_trade_sol * LAMPORTS_PER_SOL)
        if int(order.amount) > cap:
            raise Refused(f"the order spends {int(order.amount)} lamports; max_trade_sol allows {cap}")
        in_flight = sum(1 for other in self.db.orders([OrderStatus.WORKING, OrderStatus.SENDING])
                        if other.side == "buy" and other.order_id != order.order_id)
        if self.db.count_open_positions(order.mode) + in_flight >= policy.max_open_positions:
            raise Refused(f"max_open_positions ({policy.max_open_positions}) is reached")
        today = iso(self.clock().replace(hour=0, minute=0, second=0, microsecond=0))
        counted = (*OrderStatus.OPEN, OrderStatus.FILLED, OrderStatus.SIMULATED, OrderStatus.FAILED)
        if self.db.count_orders(side="buy", since=today, statuses=counted, up_to_id=order.order_id) > policy.max_buys_per_day:
            raise Refused(f"max_buys_per_day ({policy.max_buys_per_day}) is reached")
        failed = int(self.db.get_state(f"failed_sends:{today[:10]}", "0") or 0)
        if failed >= policy.max_failed_sends:
            raise Refused(f"{failed} failed sends today; max_failed_sends is {policy.max_failed_sends}")

    def buy(self, order: Order, policy: Policy) -> None:
        mint, lamports = order.mint, int(order.amount)
        info = self.rpc.mint_info(mint)  # the signer's own look at the token, not the desk's
        if info.mint_authority is not None:
            raise Refused("the token's mint authority is active")
        if info.freeze_authority is not None:
            raise Refused("the token's freeze authority is set")
        if blocked := sorted(set(info.extensions) & set(policy.blocked_token2022_extensions)):
            raise Refused(f"the token uses blocked Token-2022 extensions: {', '.join(blocked)}")
        quote = self.jupiter.quote(SOL_MINT, mint, lamports, policy.slippage_bps)
        if (quote.input_mint, quote.output_mint, quote.in_amount) != (SOL_MINT, mint, lamports):
            raise Refused("the fresh quote does not match the order")
        if quote.price_impact_pct > policy.max_price_impact_pct:
            raise Refused(f"price impact is now {quote.price_impact_pct:.2f}% > {policy.max_price_impact_pct:g}%")

        signed, valid_until = self.prepare(quote)
        target = associated_token_address(self.wallet, mint, TOKEN_PROGRAM_IDS[info.token_program])
        effect = self.simulate(signed, target, expectation=Expectation(
            side="buy", max_lamports_spent=lamports + self.config.fee_reserve_lamports,
            min_tokens_received=quote.min_out_amount))
        if order.mode == "dry_run":
            cost = -effect.lamports_change - effect.rent_lamports
            execution.apply_buy_fill(self.db, order.order_id, lamports_spent=cost, tokens_received=effect.tokens_change,
                                     decimals=info.decimals, tx_sig=None, simulated=True, fee_lamports=None,
                                     ts=self.ts())
            self.echo(f"dry run: LEAD-{order.lead_id} buy simulated, {cost / LAMPORTS_PER_SOL:.6f} SOL for "
                      f"{effect.tokens_change} raw tokens; nothing sent")
            return
        signature = self.send_and_confirm(order, signed, valid_until)
        self.book(order, signature, decimals=info.decimals, token_account=target)

    def sell(self, order: Order, policy: Policy) -> None:
        mint = order.mint
        info = self.rpc.mint_info(mint)
        target = associated_token_address(self.wallet, mint, TOKEN_PROGRAM_IDS[info.token_program])
        held = self._token_amount(self.rpc.accounts([target])[0])
        if held <= 0:  # the tokens are gone: the position is worth nothing
            execution.apply_sell_fill(self.db, order.order_id, lamports_received=0, tokens_sold=0, tx_sig=None,
                                      fee_lamports=None, ts=self.ts())
            self.echo(f"LEAD-{order.lead_id}: no tokens left to sell; position closed at zero")
            return
        amount = min(int(order.amount), held)
        quote = self.jupiter.quote(mint, SOL_MINT, amount, policy.slippage_bps)
        if (quote.input_mint, quote.output_mint, quote.in_amount) != (mint, SOL_MINT, amount):
            raise Refused("the fresh quote does not match the order")
        signed, valid_until = self.prepare(quote)
        reserve = self.config.fee_reserve_lamports
        self.simulate(signed, target, expectation=Expectation(
            side="sell", max_lamports_spent=reserve, max_tokens_sent=amount,
            min_lamports_received=max(quote.min_out_amount - reserve, 0)))
        signature = self.send_and_confirm(order, signed, valid_until)
        self.book(order, signature, decimals=info.decimals, token_account=target)
        if amount == held:
            self.close_token_account(target, TOKEN_PROGRAM_IDS[info.token_program])

    # building, checking, sending ---------------------------------------------

    def prepare(self, quote: Quote) -> tuple[VersionedTransaction, int]:
        raw, valid_until = build_swap(self.swap_http, quote.raw, str(self.wallet), self.config.priority_fee_max_lamports)
        try:
            unsigned = VersionedTransaction.from_bytes(raw)
        except ValueError as exc:
            raise Refused(f"Jupiter returned an unreadable transaction: {exc}") from exc
        check_transaction(unsigned, self.wallet, self.config.allowed_programs)
        return VersionedTransaction(unsigned.message, [self.keypair]), valid_until

    def simulate(self, signed: VersionedTransaction, target: str, *, expectation: Expectation):
        """Run the signed swap against current chain state; check what it would do to the whole wallet."""
        others = [address for address, mint, amount in self.rpc.token_accounts(str(self.wallet))
                  if address != target and (amount > 0 or mint == SOL_MINT)]
        addresses = [str(self.wallet), target, *others]
        if len(addresses) > account_count(signed):  # the RPC takes no more addresses than the swap has accounts
            raise Refused(f"the wallet holds {len(others)} other tokens, more than this swap's check can watch; "
                          "sell or move some of them out of the trading wallet")
        before = self.rpc.accounts(addresses)
        result = self.rpc.simulate(bytes(signed), addresses)
        return check_simulation(before, result, str(self.wallet), expectation)

    def broadcast(self, signed: VersionedTransaction, valid_until: int) -> str:
        """Send, re-broadcast every 2 s, and wait until confirmed or the blockhash expires."""
        signature = str(signed.signatures[0])
        raw = bytes(signed)
        try:
            self.rpc.send(raw)
        except ToolError as exc:
            self.echo(f"send reported {exc}; watching for the transaction anyway")
        deadline = time.monotonic() + CONFIRM_PATIENCE_SECONDS
        while True:
            outcome = self._outcome(signature, valid_until)
            if outcome == "confirmed":
                return signature
            if outcome is not None:
                raise SendFailed(outcome)
            if time.monotonic() > deadline:
                raise StillPending(f"transaction {signature} not confirmed yet")
            self.sleep(2)
            try:
                self.rpc.send(raw)
            except ToolError:
                pass  # re-broadcast is best effort; the status check decides

    def _outcome(self, signature: str, valid_until: int | None) -> str | None:
        """'confirmed', a failure description, or None while it could still land."""
        try:
            status = self.rpc.signature_status(signature)
        except ToolError:
            return None
        if status is not None:
            if status.get("err") is not None:
                return f"the transaction failed on chain: {status['err']}"
            if status.get("confirmationStatus") in ("confirmed", "finalized"):
                return "confirmed"
        try:
            expired = valid_until is not None and self.rpc.block_height() > valid_until
            if expired and self.rpc.signature_status(signature) is None:
                return "the transaction did not land before its blockhash expired"
        except ToolError:
            pass
        return None

    def send_and_confirm(self, order: Order, signed: VersionedTransaction, valid_until: int) -> str:
        # Record the signature first, so a crash mid-send can always be resolved from the chain.
        if not self.db.update_order(order.order_id, OrderStatus.WORKING, OrderStatus.SENDING, self.ts(),
                                    tx_sig=str(signed.signatures[0]), valid_until_height=valid_until):
            raise Refused("the order changed state before sending")
        return self.broadcast(signed, valid_until)

    def resolve_sending(self, order: Order, policy: Policy) -> None:
        """A sent order whose outcome was not booked yet: ask the chain, never guess."""
        try:
            outcome = self._outcome(order.tx_sig or "", order.valid_until_height)
            if outcome == "confirmed":
                info = self.rpc.mint_info(order.mint)
                program = TOKEN_PROGRAM_IDS[info.token_program]
                target = associated_token_address(self.wallet, order.mint, program)
                self.book(order, order.tx_sig or "", decimals=info.decimals, token_account=target)
                self.echo(f"order {order.order_id} (LEAD-{order.lead_id}) confirmed after a delay")
                if order.side == "sell" and self._token_amount(self.rpc.accounts([target])[0]) == 0:
                    self.close_token_account(target, program)
            elif outcome is not None:
                self._fail(order, policy, OrderStatus.FAILED, outcome, sent=True)
        except SendFailed as exc:
            self._fail(order, policy, OrderStatus.FAILED, str(exc), sent=True)
        except Exception as exc:  # the order stays `sending` and is asked about again next round
            problem = f"order {order.order_id}: still unresolved ({type(exc).__name__}: {exc})"
            if self._unresolved.get(order.order_id) != problem:
                self._unresolved[order.order_id] = problem
                self.echo(problem)

    def fill_from_chain(self, signature: str, mint: str, token_account: str | None = None) -> Fill:
        for _ in range(10):
            tx = self.rpc.transaction(signature)
            if tx:
                break
            self.sleep(1)
        else:
            raise StillPending(f"transaction {signature} is confirmed but its details are not available yet")
        meta = tx.get("meta") or {}
        if meta.get("err") is not None:
            raise SendFailed(f"the transaction failed on chain: {meta['err']}")
        keys = [key["pubkey"] if isinstance(key, dict) else key for key in tx["transaction"]["message"]["accountKeys"]]
        index = keys.index(str(self.wallet))

        def held(balances: list[dict] | None) -> int:
            return sum(int(entry["uiTokenAmount"]["amount"]) for entry in balances or []
                       if entry.get("owner") == str(self.wallet) and entry.get("mint") == mint)

        # Wrapped SOL counts as SOL: a swap may unwrap a wrapped-SOL account the wallet already had.
        wrapped = {entry["accountIndex"] for entry in [*(meta.get("preTokenBalances") or []),
                                                       *(meta.get("postTokenBalances") or [])]
                   if entry.get("owner") == str(self.wallet) and entry.get("mint") == SOL_MINT}
        lamports_change = sum(meta["postBalances"][at] - meta["preBalances"][at] for at in {index, *wrapped})
        rent = 0
        if token_account in keys:  # a token account this transaction created holds refundable rent
            at = keys.index(token_account)
            if meta["preBalances"][at] == 0:
                rent = meta["postBalances"][at]
        return Fill(lamports_change=lamports_change,
                    tokens_change=held(meta.get("postTokenBalances")) - held(meta.get("preTokenBalances")),
                    fee_lamports=meta.get("fee"), rent_lamports=rent)

    def book(self, order: Order, signature: str, *, decimals: int, token_account: str | None = None) -> None:
        fill = self.fill_from_chain(signature, order.mint, token_account)
        if order.side == "buy":
            if fill.tokens_change <= 0:
                raise SendFailed(f"transaction {signature} landed but delivered no tokens")
            spent = -fill.lamports_change - fill.rent_lamports
            if spent <= 0:  # not a plain swap's balance change; still never leave real tokens unbooked
                spent = int(order.amount) + (fill.fee_lamports or 0)
            execution.apply_buy_fill(self.db, order.order_id, lamports_spent=spent,
                                     tokens_received=fill.tokens_change, decimals=decimals, tx_sig=signature,
                                     simulated=False, fee_lamports=fill.fee_lamports, ts=self.ts())
            self.echo(f"LEAD-{order.lead_id} bought: {spent / LAMPORTS_PER_SOL:.6f} SOL, tx {signature}")
        else:
            execution.apply_sell_fill(self.db, order.order_id, lamports_received=max(fill.lamports_change, 0),
                                      tokens_sold=-fill.tokens_change, tx_sig=signature,
                                      fee_lamports=fill.fee_lamports, ts=self.ts())
            self.echo(f"LEAD-{order.lead_id} sold: {fill.lamports_change / LAMPORTS_PER_SOL:+.6f} SOL, tx {signature}")

    def close_token_account(self, account: str, token_program: str) -> None:
        """Reclaim the empty token account's rent (about 0.002 SOL). Best effort."""
        instruction = Instruction(Pubkey.from_string(token_program), bytes([9]), [  # 9 = CloseAccount
            AccountMeta(Pubkey.from_string(account), is_signer=False, is_writable=True),
            AccountMeta(self.wallet, is_signer=False, is_writable=True),
            AccountMeta(self.wallet, is_signer=True, is_writable=False)])
        try:
            signed, valid_until = self._sign([instruction])
            result = self.rpc.simulate(bytes(signed), [str(self.wallet)])
            if result.get("err") is not None:
                self.echo(f"left token account {account} open: {result['err']}")
                return
            self.broadcast(signed, valid_until)
        except (ToolError, SendFailed, StillPending) as exc:
            self.echo(f"left token account {account} open: {exc}")

    def _sign(self, instructions: list[Instruction]) -> tuple[VersionedTransaction, int]:
        blockhash, valid_until = self.rpc.latest_blockhash()
        message = MessageV0.try_compile(self.wallet, instructions, [], Hash.from_string(blockhash))
        return VersionedTransaction(message, [self.keypair]), valid_until

    def withdraw(self, destination: str, lamports: int | None) -> str:
        """Send SOL out of the trading wallet; lamports=None sends everything but the fee."""
        if lamports is not None and lamports <= 0:
            raise Refused("the amount to send must be more than 0")
        balance = self.rpc.balance(str(self.wallet))
        amount = balance - SIGNATURE_FEE_LAMPORTS if lamports is None else lamports
        if amount <= 0 or amount + SIGNATURE_FEE_LAMPORTS > balance:
            raise Refused(f"the wallet holds {balance / LAMPORTS_PER_SOL:.6f} SOL; that is not enough")
        signed, valid_until = self._sign([transfer(TransferParams(
            from_pubkey=self.wallet, to_pubkey=Pubkey.from_string(destination), lamports=amount))])
        result = self.rpc.simulate(bytes(signed), [str(self.wallet)])
        if result.get("err") is not None:
            raise Refused(f"the withdrawal would fail: {result['err']}")
        return self.broadcast(signed, valid_until)

    @staticmethod
    def _token_amount(account: dict | None) -> int:
        data = (account or {}).get("data")
        info = ((data.get("parsed") or {}).get("info") or {}) if isinstance(data, dict) else {}
        return int((info.get("tokenAmount") or {}).get("amount") or 0)

