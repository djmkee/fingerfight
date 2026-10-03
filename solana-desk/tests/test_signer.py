"""The signer, against an in-memory Solana and Jupiter. Nothing here touches a network.

FakeChain hands the executor real solders transactions and applies each swap's balance changes,
so the whole path runs: re-checks, transaction inspection, signing, simulation, sending,
confirmation, booking, recovery, and the refusals that matter for a wallet with real money.
"""

import copy
import json
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
import yaml

pytest.importorskip("solders")

from solders.compute_budget import set_compute_unit_limit  # noqa: E402
from solders.hash import Hash  # noqa: E402
from solders.instruction import AccountMeta, Instruction  # noqa: E402
from solders.keypair import Keypair  # noqa: E402
from solders.message import MessageV0  # noqa: E402
from solders.pubkey import Pubkey  # noqa: E402
from solders.signature import Signature  # noqa: E402
from solders.transaction import VersionedTransaction  # noqa: E402

from desk.approval import approve  # noqa: E402
from desk.constants import SOL_MINT, TOKEN_PROGRAM  # noqa: E402
from desk.db import OrderStatus, Status  # noqa: E402
from desk.orchestrator import Orchestrator  # noqa: E402
from desk.roles import Exit  # noqa: E402
from desk.tools.base import MintInfo  # noqa: E402
from desk.tools.fixture import TokenSpec  # noqa: E402
from desk.tools.jupiter import parse_quote  # noqa: E402
from signer import executor as executor_module  # noqa: E402
from signer.config import SignerConfigError, load_signer_config  # noqa: E402
from signer.executor import Executor, Fill  # noqa: E402
from signer.lock import SignerBusy, single_signer  # noqa: E402
from signer.verify import (Expectation, Refused, associated_token_address, check_simulation,  # noqa: E402
                           check_transaction)
from signer.wallet import WalletError, create_wallet, import_wallet, load_wallet  # noqa: E402

from conftest import ROOT, lead_for, signer_online  # noqa: E402

JUPITER = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
RENT = 2_039_280          # rent of a token account
FEE = 5_000               # network fee per signature
BOUGHT = 49_000_000_000   # raw tokens a 0.05 SOL buy delivers in these tests


# --- wallet and config -------------------------------------------------------------------------

def test_wallet_is_created_once_and_loads_back(tmp_path):
    path = tmp_path / "keys" / "wallet.json"
    keypair = create_wallet(path)
    assert load_wallet(path).pubkey() == keypair.pubkey()
    data = json.loads(path.read_text())
    assert isinstance(data, list) and len(data) == 64, "Solana CLI keypair format"
    with pytest.raises(WalletError, match="refusing to overwrite"):
        create_wallet(path)
    if sys.platform != "win32":
        assert oct(os.stat(path).st_mode & 0o777) == "0o600"


def test_import_takes_wallet_app_and_cli_formats_and_rejects_garbage(tmp_path):
    original = Keypair()
    from_app = import_wallet(tmp_path / "a.json", str(original))      # base58, as wallet apps export it
    from_cli = import_wallet(tmp_path / "b.json", json.dumps(list(bytes(original))))
    assert from_app.pubkey() == from_cli.pubkey() == original.pubkey()
    with pytest.raises(WalletError, match="not a valid Solana private key"):
        import_wallet(tmp_path / "c.json", "correct horse battery staple")
    halves = list(bytes(original))[:32] + list(bytes(Keypair().pubkey()))   # someone else's public key
    with pytest.raises(WalletError, match="not a valid Solana private key"):
        import_wallet(tmp_path / "d.json", json.dumps(halves))
    with pytest.raises(WalletError, match="python signer.py init"):
        load_wallet(tmp_path / "missing.json")


def test_repo_signer_config_loads(tmp_path):
    config = load_signer_config(ROOT / "config" / "signer.yaml")
    assert "11111111111111111111111111111111" in config.allowed_programs, "the system program, quoted in YAML"
    assert JUPITER in config.allowed_programs
    assert config.keypair_path.name == "wallet.json" and "~" not in str(config.keypair_path)
    bad = yaml.safe_load((ROOT / "config" / "signer.yaml").read_text()) | {"allowed_programs": ["nope"]}
    (tmp_path / "s.yaml").write_text(yaml.safe_dump(bad))
    with pytest.raises(SignerConfigError, match="allowed_programs"):
        load_signer_config(tmp_path / "s.yaml")


# --- transaction and simulation checks ---------------------------------------------------------

def swap_transaction(payer: Pubkey, *, program: str = JUPITER, extra_signer: bool = False,
                     nonce: int = 0) -> VersionedTransaction:
    """An unsigned transaction shaped like a Jupiter swap: compute budget plus a route instruction.

    `nonce` keeps successive swaps distinct; with one fixed blockhash they would otherwise sign identically.
    """
    route = Pubkey.from_string(program)
    data = bytes([1, 2, 3]) + nonce.to_bytes(8, "little")
    pools = [AccountMeta(Keypair().pubkey(), False, True) for _ in range(8)]  # a real route touches many accounts
    instructions = [set_compute_unit_limit(200_000), Instruction(route, data, [AccountMeta(payer, True, True), *pools])]
    if extra_signer:
        instructions.append(Instruction(route, b"", [AccountMeta(Keypair().pubkey(), True, False)]))
    message = MessageV0.try_compile(payer, instructions, [], Hash.default())
    return VersionedTransaction.populate(message, [Signature.default()] * message.header.num_required_signatures)


ALLOWED = load_signer_config(ROOT / "config" / "signer.yaml").allowed_programs


def test_transaction_check_passes_a_plain_swap():
    wallet = Keypair().pubkey()
    check_transaction(swap_transaction(wallet), wallet, ALLOWED)


@pytest.mark.parametrize(("build", "message"), [
    (lambda wallet: swap_transaction(Keypair().pubkey()), "fee payer"),
    (lambda wallet: swap_transaction(wallet, extra_signer=True), "only the trading wallet may sign"),
    (lambda wallet: swap_transaction(wallet, program=str(Keypair().pubkey())), "not in allowed_programs"),
])
def test_transaction_check_refuses(build, message):
    wallet = Keypair().pubkey()
    with pytest.raises(Refused, match=message):
        check_transaction(build(wallet), wallet, ALLOWED)


def account(lamports: int) -> dict:
    return {"lamports": lamports, "data": ["", "base64"]}


def token(amount: int, owner: str, *, delegate: str | None = None, lamports: int = RENT, mint: str = "M") -> dict:
    info: dict[str, Any] = {"owner": owner, "mint": mint, "tokenAmount": {"amount": str(amount)}}
    if delegate:
        info["delegate"] = delegate
    return {"lamports": lamports, "data": {"parsed": {"type": "account", "info": info}}}


BUY = Expectation(side="buy", max_lamports_spent=50_000_000 + 3_000_000, min_tokens_received=1_000)


def test_simulation_check_accepts_a_normal_buy_and_reports_rent():
    pre = [account(2_000_000_000), None]
    post = {"err": None, "accounts": [account(2_000_000_000 - 50_000_000 - RENT - FEE), token(5_000, "W")]}
    effect = check_simulation(pre, post, "W", BUY)
    assert (effect.lamports_change, effect.tokens_change, effect.rent_lamports) == (-(50_000_000 + RENT + FEE), 5_000, RENT)


@pytest.mark.parametrize(("post_accounts", "message"), [
    ([account(1_000_000_000), token(5_000, "W")], "would take 1000000000 lamports"),        # drains the wallet
    ([account(1_949_000_000), token(10, "W")], "promised at least 1000"),                    # too few tokens
    ([account(1_949_000_000), token(5_000, "Thief")], "another owner"),                      # hands the account away
    ([account(1_949_000_000), token(5_000, "W", delegate="Thief")], "delegate"),              # lets someone spend it
    ([account(1_949_000_000), token(5_000, "W"), token(0, "W")], "no business touching"),     # empties another position
    ([account(1_949_000_000), token(5_000, "W"), token(700, "W", delegate="Thief")], "let another address spend"),
])
def test_simulation_check_refuses_harmful_effects(post_accounts, message):
    pre = [account(2_000_000_000), None, token(700, "W")][:len(post_accounts)]
    with pytest.raises(Refused, match=message):
        check_simulation(pre, {"err": None, "accounts": post_accounts}, "W", BUY)


def test_failed_simulation_is_refused():
    with pytest.raises(Refused, match="simulation failed"):
        check_simulation([account(1), None], {"err": {"InstructionError": [1, "Custom"]}, "accounts": [account(1), None],
                                              "logs": ["slippage tolerance exceeded"]}, "W", BUY)


def wrapped_sol(amount: int, owner: str = "W") -> dict:
    return token(amount, owner, lamports=RENT + amount, mint=SOL_MINT)


def test_unwrapping_wrapped_sol_the_wallet_already_had_counts_as_sol():
    pre = [account(2_000_000_000), None, wrapped_sol(500_000_000)]
    post = {"err": None, "accounts": [account(2_000_000_000 - 50_000_000 - RENT - FEE + 500_000_000 + RENT),
                                      token(5_000, "W"), None]}
    effect = check_simulation(pre, post, "W", BUY)
    assert effect.lamports_change == -(50_000_000 + RENT + FEE), "the unwrapped SOL is not income"


def test_wrapped_sol_sent_elsewhere_counts_as_spent():
    pre = [account(2_000_000_000), None, wrapped_sol(500_000_000)]
    post = {"err": None, "accounts": [account(2_000_000_000 - 50_000_000 - RENT - FEE), token(5_000, "W"), None]}
    with pytest.raises(Refused, match="would take 554083560 lamports"):
        check_simulation(pre, post, "W", BUY)


def test_buying_into_a_token_account_someone_else_may_spend_is_refused():
    pre = [account(2_000_000_000), token(0, "W", delegate="Thief")]
    post = {"err": None, "accounts": [account(2_000_000_000 - 50_000_000 - FEE), token(5_000, "W", delegate="Thief")]}
    with pytest.raises(Refused, match="delegate who could spend"):
        check_simulation(pre, post, "W", BUY)


# --- an in-memory chain -----------------------------------------------------------------------

class FakeChain:
    """One wallet and its token accounts. Swaps apply `swap_effect`; sends confirm unless told otherwise."""

    def __init__(self, wallet: Pubkey, lamports: int = 2_000_000_000) -> None:
        self.wallet = str(wallet)
        self.lamports = lamports
        self.tokens: dict[str, dict[str, Any]] = {}
        self.mints: dict[str, MintInfo] = {}
        self.swap_effect: dict[str, Any] = {}
        self.swap_program = JUPITER
        self.extra_signer = False
        self.impact_pct = 0.8
        self.land = "confirmed"         # confirmed | failed | never
        self.sent: list[bytes] = []
        self.statuses: dict[str, dict[str, Any]] = {}
        self.metas: dict[str, dict[str, Any]] = {}
        self.height = 100
        self.valid_until = 250

    # the RPC surface the executor uses
    def mint_info(self, mint: str) -> MintInfo:
        return self.mints[mint]

    def balance(self, address: str) -> int:
        return self.lamports

    def latest_blockhash(self) -> tuple[str, int]:
        return str(Hash.default()), self.valid_until

    def block_height(self) -> int:
        self.height += 60  # time passes while the executor waits
        return self.height

    def accounts(self, addresses: list[str]) -> list[dict | None]:
        return [self._view(address, self.lamports, self.tokens) for address in addresses]

    def _view(self, address: str, lamports: int, tokens: dict) -> dict | None:
        if address == self.wallet:
            return account(lamports)
        held = tokens.get(address)
        if held is None:
            return None
        lamports = RENT + (held["amount"] if held["mint"] == SOL_MINT else 0)  # wrapped SOL is lamports too
        return token(held["amount"], held.get("owner", self.wallet), delegate=held.get("delegate"),
                     lamports=lamports, mint=held["mint"])

    def token_accounts(self, owner: str) -> list[tuple[str, str, int]]:
        return [(address, held["mint"], held["amount"]) for address, held in self.tokens.items()
                if held.get("owner", self.wallet) == owner]

    def _effect(self, raw: bytes) -> dict[str, Any]:
        tx = VersionedTransaction.from_bytes(raw)
        keys = tx.message.account_keys
        programs = {str(keys[ix.program_id_index]) for ix in tx.message.instructions}
        if self.swap_program in programs:
            return self.swap_effect
        for ix in tx.message.instructions:
            program = str(keys[ix.program_id_index])
            if program == TOKEN_PROGRAM and bytes(ix.data) == bytes([9]):
                return {"lamports": RENT - FEE, "close": str(keys[ix.accounts[0]])}
            if program == "11111111111111111111111111111111":
                return {"lamports": -int.from_bytes(bytes(ix.data)[4:12], "little") - FEE}
        raise AssertionError("unexpected transaction")

    def _after(self, effect: dict[str, Any]) -> tuple[int, dict]:
        lamports, tokens = self.lamports + effect["lamports"], copy.deepcopy(self.tokens)
        if "account" in effect:
            held = tokens.setdefault(effect["account"], {"mint": effect["mint"], "amount": 0})
            held["amount"] += effect["tokens"]
            for key in ("owner", "delegate"):
                if key in effect:
                    held[key] = effect[key]
        if "close" in effect:
            tokens.pop(effect["close"], None)
        for address, changes in effect.get("also", {}).items():  # what a tampered swap does on the side
            tokens[address] = tokens[address] | changes
        return lamports, tokens

    def simulate(self, raw: bytes, addresses: list[str]) -> dict[str, Any]:
        lamports, tokens = self._after(self._effect(raw))
        return {"err": None, "logs": [], "accounts": [self._view(address, lamports, tokens) for address in addresses]}

    def send(self, raw: bytes) -> str:
        self.sent.append(raw)
        signature = str(VersionedTransaction.from_bytes(raw).signatures[0])
        if signature in self.statuses or self.land == "never":
            return signature
        if self.land == "failed":
            self.lamports -= FEE
            self.statuses[signature] = {"err": {"InstructionError": [1, {"Custom": 6001}]}, "confirmationStatus": "confirmed"}
            return signature
        effect = self._effect(raw)
        target = effect.get("account") or effect.get("close")
        before_l, before_t = self.lamports, copy.deepcopy(self.tokens)
        self.lamports, self.tokens = self._after(effect)

        def balances(tokens: dict) -> list[dict]:
            held = tokens.get(target)
            return [] if held is None else [{"accountIndex": 1, "mint": held["mint"], "owner": self.wallet,
                                             "uiTokenAmount": {"amount": str(held["amount"])}}]

        self.metas[signature] = {
            "meta": {"err": None, "fee": FEE,
                     "preBalances": [before_l, RENT if target in before_t else 0],
                     "postBalances": [self.lamports, RENT if target in self.tokens else 0],
                     "preTokenBalances": balances(before_t), "postTokenBalances": balances(self.tokens)},
            "transaction": {"message": {"accountKeys": [{"pubkey": self.wallet}, {"pubkey": target}]}},
        }
        self.statuses[signature] = {"err": None, "confirmationStatus": "confirmed"}
        return signature

    def signature_status(self, signature: str) -> dict | None:
        return self.statuses.get(signature)

    def transaction(self, signature: str) -> dict | None:
        return self.metas.get(signature)


class FakeJupiter:
    def __init__(self, chain: FakeChain) -> None:
        self.chain = chain
        self.output_override: str | None = None  # a quote for some other token than the one asked for

    def quote(self, input_mint: str, output_mint: str, amount: int, slippage_bps: int):
        out = BOUGHT if input_mint == SOL_MINT else 30_000_000
        output_mint = self.output_override or output_mint
        return parse_quote({"inputMint": input_mint, "outputMint": output_mint, "inAmount": str(amount),
                            "outAmount": str(out), "otherAmountThreshold": str(out * 99 // 100),
                            "priceImpactPct": str(self.chain.impact_pct / 100), "slippageBps": slippage_bps,
                            "routePlan": [], "contextSlot": 1}, source="fake-jupiter")


@dataclass
class Rig:
    ctx: Any
    chain: FakeChain
    executor: Executor
    policy_path: Path

    def policy(self):
        return self.executor.policy()

    def ata(self, spec: TokenSpec) -> str:
        return associated_token_address(self.executor.wallet, spec.mint, TOKEN_PROGRAM)

    def stage(self, spec: TokenSpec):
        """Run a desk cycle and approve the lead: a pending buy order, as the dashboard would make."""
        Orchestrator(self.ctx).run_cycle()
        self.chain.mints[spec.mint] = MintInfo(spec.mint, "spl-token", None, None, spec.supply, spec.decimals,
                                               True, (), None, "fake-rpc")
        self.chain.swap_effect = {"lamports": -(50_000_000 + RENT + FEE), "account": self.ata(spec),
                                  "mint": spec.mint, "tokens": BOUGHT}
        return approve(self.ctx, lead_for(self.ctx, spec).ref, by="test")


@pytest.fixture
def rig(make_desk, tmp_path, clock, monkeypatch):
    def build(specs: list[TokenSpec], mode: str = "dry_run", **policy_changes: Any) -> Rig:
        raw = yaml.safe_load((ROOT / "config" / "policy.yaml").read_text())
        raw |= {"trading_mode": mode, "auto_approve": False} | policy_changes
        policy_path = tmp_path / "policy.yaml"
        policy_path.write_text(yaml.safe_dump(raw))
        ctx = make_desk(specs, trading_mode=mode, auto_approve=False, **policy_changes)
        keypair = Keypair()
        chain = FakeChain(keypair.pubkey())
        signer_online(ctx, wallet=str(keypair.pubkey()))
        config = replace(load_signer_config(ROOT / "config" / "signer.yaml"), keypair_path=tmp_path / "unused.json")
        executor = Executor(config=config, keypair=keypair, rpc=chain, jupiter=FakeJupiter(chain), swap_http=None,
                            db=ctx.db, policy_path=policy_path, echo=lambda _line: None, clock=clock,
                            sleep=lambda _seconds: None)
        built = iter(range(1, 1_000))

        def fake_build_swap(http, quote, wallet, fee):
            tx = swap_transaction(keypair.pubkey(), program=chain.swap_program, extra_signer=chain.extra_signer,
                                  nonce=next(built))
            return bytes(tx), chain.valid_until

        monkeypatch.setattr(executor_module, "build_swap", fake_build_swap)
        return Rig(ctx, chain, executor, policy_path)

    return build


# --- execution ---------------------------------------------------------------------------------

def test_dry_run_buy_is_simulated_never_sent_and_costed_without_rent(rig):
    spec = TokenSpec("SIMBUY")
    r = rig([spec])
    order = r.stage(spec)
    r.executor.process(r.policy())

    assert r.ctx.db.get_order(order.order_id).status == OrderStatus.SIMULATED
    assert r.chain.sent == [], "a dry run never sends"
    position = r.ctx.db.get_position(lead_for(r.ctx, spec).lead_id)
    assert position.mode == "dry_run" and position.token_amount == str(BOUGHT)
    assert position.size_sol == pytest.approx((50_000_000 + FEE) / 1e9), "rent comes back on close: not a cost"


def test_live_buy_sends_confirms_and_books_what_the_chain_says(rig):
    spec = TokenSpec("LIVEBUY")
    r = rig([spec], mode="live")
    order = r.stage(spec)
    r.executor.process(r.policy())

    order = r.ctx.db.get_order(order.order_id)
    assert order.status == OrderStatus.FILLED and order.tx_sig and order.valid_until_height == r.chain.valid_until
    assert len(r.chain.sent) == 1
    signed = VersionedTransaction.from_bytes(r.chain.sent[0])
    assert signed.verify_with_results() == [True], "signed by the trading wallet"
    lead = lead_for(r.ctx, spec)
    assert (lead.status, lead.tx_sig) == (Status.FILLED, order.tx_sig)
    position = r.ctx.db.get_position(lead.lead_id)
    assert (position.mode, position.token_amount) == ("live", str(BOUGHT))
    assert position.size_sol == pytest.approx((50_000_000 + FEE) / 1e9)


def refuse_stale(r, order):
    r.executor.clock.advance(seconds=r.executor.config.order_max_age_seconds + 5)


def refuse_halted(r, order):
    r.ctx.db.set_halted("test halt", r.ctx.ts())


def refuse_over_cap(r, order):
    r.ctx.db.conn.execute("UPDATE orders SET amount = '60000000' WHERE order_id = ?", (order.order_id,))


def refuse_paper_mode(r, order):
    raw = yaml.safe_load(r.policy_path.read_text()) | {"trading_mode": "paper"}
    r.policy_path.write_text(yaml.safe_dump(raw))


def refuse_mode_change(r, order):
    raw = yaml.safe_load(r.policy_path.read_text()) | {"trading_mode": "dry_run"}
    r.policy_path.write_text(yaml.safe_dump(raw))


def refuse_mint_authority(r, order):
    r.chain.mints[order.mint] = replace(r.chain.mints[order.mint], mint_authority=str(Keypair().pubkey()))


def refuse_freeze_authority(r, order):
    r.chain.mints[order.mint] = replace(r.chain.mints[order.mint], freeze_authority=str(Keypair().pubkey()))


def refuse_blocked_extension(r, order):
    r.chain.mints[order.mint] = replace(r.chain.mints[order.mint], extensions=("permanentDelegate",))


def refuse_quote_mismatch(r, order):
    r.executor.jupiter.output_override = str(Keypair().pubkey())


def refuse_lead_closed(r, order):
    r.ctx.db.conn.execute("UPDATE leads SET status = 'rejected' WHERE lead_id = ?", (order.lead_id,))


def refuse_mint_changed(r, order):
    r.ctx.db.conn.execute("DROP TRIGGER orders_mint_fixed")  # get past the database's own guard
    r.ctx.db.conn.execute("UPDATE orders SET mint = ? WHERE order_id = ?", (TokenSpec("OTHER").mint, order.order_id))


def refuse_failed_sends(r, order):
    r.ctx.db.set_state(f"failed_sends:{r.ctx.ts()[:10]}", 3)


def refuse_impact(r, order):
    r.chain.impact_pct = 4.0


def refuse_foreign_program(r, order):
    r.chain.swap_program = str(Keypair().pubkey())


def refuse_extra_signer(r, order):
    r.chain.extra_signer = True


def refuse_drain(r, order):
    r.chain.swap_effect = r.chain.swap_effect | {"lamports": -1_500_000_000}


def refuse_other_token(r, order):
    r.chain.tokens["SavingsAccount"] = {"mint": "SomeStablecoin", "amount": 1_000_000}
    r.chain.swap_effect = r.chain.swap_effect | {"also": {"SavingsAccount": {"amount": 0}}}


def refuse_wrapped_sol_theft(r, order):
    r.chain.tokens["WrappedSol"] = {"mint": SOL_MINT, "amount": 400_000_000}
    r.chain.swap_effect = r.chain.swap_effect | {"also": {"WrappedSol": {"amount": 0}}}


def refuse_too_many_tokens(r, order):
    for i in range(20):
        r.chain.tokens[f"Airdrop{i}"] = {"mint": f"Spam{i}", "amount": 1}


@pytest.mark.parametrize(("tamper", "message"), [
    (refuse_stale, "never executed late"),
    (refuse_halted, "halted"),
    (refuse_over_cap, "max_trade_sol"),
    (refuse_paper_mode, "trading_mode is paper; the signer buys nothing"),
    (refuse_mode_change, "trading_mode is now dry_run"),
    (refuse_mint_authority, "mint authority is active"),
    (refuse_freeze_authority, "freeze authority is set"),
    (refuse_blocked_extension, "blocked Token-2022 extensions: permanentDelegate"),
    (refuse_quote_mismatch, "fresh quote does not match"),
    (refuse_lead_closed, "the lead is rejected, not ordered"),
    (refuse_mint_changed, "mint does not match its lead"),
    (refuse_failed_sends, "3 failed sends today"),
    (refuse_impact, "price impact is now 4.00%"),
    (refuse_foreign_program, "not in allowed_programs"),
    (refuse_extra_signer, "only the trading wallet may sign"),
    (refuse_drain, "would take 1500000000 lamports"),
    (refuse_other_token, "no business touching"),
    (refuse_wrapped_sol_theft, "would take 452"),
    (refuse_too_many_tokens, "more than this swap's check can watch"),
])
def test_signer_refuses_and_nothing_is_sent(rig, tamper, message):
    spec = TokenSpec("REFUSE")
    r = rig([spec], mode="live")
    order = r.stage(spec)
    tamper(r, order)
    r.executor.process(r.policy())

    order = r.ctx.db.get_order(order.order_id)
    assert order.status == OrderStatus.REFUSED and message in order.detail
    assert r.chain.sent == []
    assert lead_for(r.ctx, spec).status == Status.REJECTED
    assert r.ctx.db.positions() == []


@pytest.mark.parametrize(("limit", "message"), [({"max_open_positions": 1}, "max_open_positions (1) is reached"),
                                              ({"max_buys_per_day": 1}, "max_buys_per_day (1) is reached")])
def test_signer_enforces_limits_lowered_after_the_orders_were_placed(rig, limit, message):
    first, second = TokenSpec("FIRST"), TokenSpec("SECOND")
    r = rig([first, second], mode="live")
    r.stage(first)
    approve(r.ctx, lead_for(r.ctx, second).ref)
    raw = yaml.safe_load(r.policy_path.read_text()) | limit
    r.policy_path.write_text(yaml.safe_dump(raw))       # the user tightens the policy file
    r.executor.process(r.policy())
    orders = sorted(r.ctx.db.recent_orders(5), key=lambda order: order.order_id)
    assert orders[0].status == OrderStatus.FILLED
    assert orders[1].status == OrderStatus.REFUSED and message in orders[1].detail


def test_transaction_failing_on_chain_counts_as_a_failed_send(rig):
    spec = TokenSpec("SLIPPED")
    r = rig([spec], mode="live")
    order = r.stage(spec)
    r.chain.land = "failed"
    r.executor.process(r.policy())
    order = r.ctx.db.get_order(order.order_id)
    assert order.status == OrderStatus.FAILED and "failed on chain" in order.detail
    assert r.ctx.db.get_state(f"failed_sends:{r.ctx.ts()[:10]}") == "1"
    assert r.ctx.db.positions() == []


def test_transaction_that_never_lands_fails_once_its_blockhash_expires(rig):
    spec = TokenSpec("LOST")
    r = rig([spec], mode="live")
    order = r.stage(spec)
    r.chain.land = "never"
    r.executor.process(r.policy())
    order = r.ctx.db.get_order(order.order_id)
    assert order.status == OrderStatus.FAILED and "did not land" in order.detail
    assert len(r.chain.sent) > 1, "re-broadcast while waiting"
    assert r.ctx.db.get_state(f"failed_sends:{r.ctx.ts()[:10]}") == "1"


def test_live_sell_closes_the_position_and_reclaims_the_rent(rig):
    spec = TokenSpec("ROUNDTRIP")
    r = rig([spec], mode="live")
    r.stage(spec)
    r.executor.process(r.policy())                      # live buy
    lead_id = lead_for(r.ctx, spec).lead_id
    r.executor.clock.advance(minutes=1)
    signer_online(r.ctx, wallet=str(r.executor.wallet), lamports=r.chain.lamports)
    spec.liquidity_usd = 10_000.0                       # exit trigger
    Exit(r.ctx).run()
    assert r.ctx.db.get_position(lead_id).sell_order_id is not None

    r.chain.swap_effect = {"lamports": 30_000_000 - FEE, "account": r.ata(spec), "mint": spec.mint, "tokens": -BOUGHT}
    r.executor.process(r.policy())
    position = r.ctx.db.get_position(lead_id)
    assert not position.is_open
    assert position.realized_sol == pytest.approx((30_000_000 - FEE) / 1e9 - position.size_sol)
    assert lead_for(r.ctx, spec).status == Status.CLOSED
    assert r.ata(spec) not in r.chain.tokens, "the empty token account was closed"
    assert len(r.chain.sent) == 3, "buy, sell, close account"


def test_live_positions_are_still_sold_after_switching_back_to_dry_run(rig):
    spec = TokenSpec("SWITCHBACK")
    r = rig([spec], mode="live")
    r.stage(spec)
    r.executor.process(r.policy())                      # live buy
    raw = yaml.safe_load(r.policy_path.read_text()) | {"trading_mode": "dry_run"}
    r.policy_path.write_text(yaml.safe_dump(raw))       # the user goes back to dry run
    r.executor.clock.advance(minutes=1)
    signer_online(r.ctx, wallet=str(r.executor.wallet), lamports=r.chain.lamports)
    spec.liquidity_usd = 10_000.0
    Exit(r.ctx).run()
    r.chain.swap_effect = {"lamports": 30_000_000 - FEE, "account": r.ata(spec), "mint": spec.mint, "tokens": -BOUGHT}
    r.executor.process(r.policy())
    assert not r.ctx.db.get_position(lead_for(r.ctx, spec).lead_id).is_open, "real tokens are sold for real"


def test_restart_resolves_unfinished_orders_from_the_chain(rig):
    spec, other = TokenSpec("RESUME"), TokenSpec("NEVERSENT")
    r = rig([spec, other], mode="live")
    order = r.stage(spec)
    approve(r.ctx, lead_for(r.ctx, other).ref)
    unsent = [o for o in r.ctx.db.recent_orders(5) if o.lead_id == lead_for(r.ctx, other).lead_id][0]
    r.ctx.db.update_order(unsent.order_id, OrderStatus.PENDING, OrderStatus.WORKING, r.ctx.ts())

    # The first order was sent and confirmed, but the signer died before booking it.
    signed = VersionedTransaction(swap_transaction(r.executor.wallet).message, [r.executor.keypair])
    r.chain.send(bytes(signed))
    r.ctx.db.update_order(order.order_id, OrderStatus.PENDING, OrderStatus.SENDING, r.ctx.ts(),
                          tx_sig=str(signed.signatures[0]), valid_until_height=r.chain.valid_until)

    r.executor.recover(r.policy())
    r.executor.process(r.policy())
    assert r.ctx.db.get_order(unsent.order_id).status == OrderStatus.FAILED
    assert r.ctx.db.get_order(order.order_id).status == OrderStatus.FILLED
    assert r.ctx.db.get_position(lead_for(r.ctx, spec).lead_id).is_open


def test_error_after_sending_leaves_the_order_for_the_chain_to_decide(rig, monkeypatch):
    spec = TokenSpec("CRASHY")
    r = rig([spec], mode="live")
    order = r.stage(spec)
    monkeypatch.setattr(r.executor, "book", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("disk full")))
    r.executor.process(r.policy())
    assert r.ctx.db.get_order(order.order_id).status == OrderStatus.SENDING, "never guessed failed after a send"
    monkeypatch.undo()
    r.executor.process(r.policy())
    assert r.ctx.db.get_order(order.order_id).status == OrderStatus.FILLED


def test_withdraw_sends_sol_and_refuses_more_than_the_balance(rig):
    r = rig([], mode="live")
    destination = str(Keypair().pubkey())
    with pytest.raises(Refused, match="not enough"):
        r.executor.withdraw(destination, 5_000_000_000)
    signature = r.executor.withdraw(destination, None)
    assert signature and r.chain.lamports == 0


def test_a_buy_that_landed_is_always_booked(rig, monkeypatch):
    spec = TokenSpec("ODDFILL")
    r = rig([spec], mode="live")
    order = r.stage(spec)
    # A balance change no plain swap makes (the wallet gained SOL): book at the order's size, never drop it.
    monkeypatch.setattr(r.executor, "fill_from_chain", lambda *args: Fill(1_000, BOUGHT, FEE))
    r.executor.process(r.policy())
    position = r.ctx.db.get_position(lead_for(r.ctx, spec).lead_id)
    assert position.is_open and position.size_sol == pytest.approx((int(order.amount) + FEE) / 1e9)


def test_fill_counts_an_unwrapped_wsol_account_as_sol(rig):
    r = rig([], mode="live")
    wallet = str(r.executor.wallet)
    r.chain.metas["Sig"] = {
        "meta": {"err": None, "fee": FEE,
                 "preBalances": [2_000_000_000, 0, RENT + 500_000_000],
                 "postBalances": [2_000_000_000 - 50_000_000 - RENT - FEE + 500_000_000 + RENT, RENT, 0],
                 "preTokenBalances": [{"accountIndex": 2, "mint": SOL_MINT, "owner": wallet,
                                       "uiTokenAmount": {"amount": "500000000"}}],
                 "postTokenBalances": [{"accountIndex": 1, "mint": "Mint", "owner": wallet,
                                        "uiTokenAmount": {"amount": "5000"}}]},
        "transaction": {"message": {"accountKeys": [{"pubkey": wallet}, {"pubkey": "Ata"}, {"pubkey": "Wsol"}]}},
    }
    fill = r.executor.fill_from_chain("Sig", "Mint", "Ata")
    assert (fill.lamports_change, fill.tokens_change, fill.rent_lamports) == (-(50_000_000 + RENT + FEE), 5_000, RENT)


def test_only_one_signer_per_database(tmp_path):
    db = tmp_path / "desk.sqlite3"
    with single_signer(db):
        with pytest.raises(SignerBusy, match="already running"):
            with single_signer(db):
                pass
    with single_signer(db):
        pass  # released when the first one stopped


def test_withdraw_refuses_nothing(rig):
    r = rig([], mode="live")
    with pytest.raises(Refused, match="more than 0"):
        r.executor.withdraw(str(Keypair().pubkey()), 0)
