"""Checks a transaction must pass before it is signed, and again (simulated) before it is sent.

Jupiter builds the swap, so the signer does not trust it blindly:
* structure: the trading wallet pays and is the only signer, and every top-level instruction
  calls an allowed program;
* effect: a simulation against current chain state must show the wallet losing no more SOL
  (wrapped SOL included) than intended, receiving at least the quoted minimum, and every other
  token account that holds something keeping its balance, its owner, and no new delegate.
"""

from dataclasses import dataclass
from typing import Any

from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from desk.constants import SOL_MINT

ASSOCIATED_TOKEN_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")


class Refused(RuntimeError):
    """A safety check failed. Nothing was signed, or nothing was sent."""


def associated_token_address(wallet: Pubkey, mint: str, token_program: str) -> str:
    address, _ = Pubkey.find_program_address(
        [bytes(wallet), bytes(Pubkey.from_string(token_program)), bytes(Pubkey.from_string(mint))],
        ASSOCIATED_TOKEN_PROGRAM)
    return str(address)


def account_count(transaction: VersionedTransaction) -> int:
    """Accounts the transaction can touch: its own keys plus those it loads from lookup tables."""
    message = transaction.message
    lookups = getattr(message, "address_table_lookups", None) or []
    return len(message.account_keys) + sum(len(lookup.writable_indexes) + len(lookup.readonly_indexes)
                                           for lookup in lookups)


def check_transaction(transaction: VersionedTransaction, wallet: Pubkey, allowed_programs: frozenset[str]) -> None:
    message = transaction.message
    keys = message.account_keys
    if not keys or keys[0] != wallet:
        raise Refused("the transaction's fee payer is not the trading wallet")
    if message.header.num_required_signatures != 1:
        raise Refused(f"the transaction wants {message.header.num_required_signatures} signatures; "
                      "only the trading wallet may sign")
    for instruction in message.instructions:
        if instruction.program_id_index >= len(keys):
            raise Refused("an instruction's program is not among the transaction's own accounts")
        program = str(keys[instruction.program_id_index])
        if program not in allowed_programs:
            raise Refused(f"the transaction calls program {program}, which is not in allowed_programs")


@dataclass(frozen=True)
class Expectation:
    """What a trade may do to the wallet. Lamport and token amounts are raw units."""

    side: str                      # buy | sell
    max_lamports_spent: int        # buy: size + fee reserve; sell: fee reserve
    min_lamports_received: int = 0  # sell: quoted minimum out, less the fee reserve
    min_tokens_received: int = 0   # buy: quoted minimum out
    max_tokens_sent: int = 0       # sell: the amount being sold


@dataclass(frozen=True)
class Effect:
    lamports_change: int           # wallet SOL change (negative when SOL left)
    tokens_change: int             # traded-token change (negative when tokens left)
    rent_lamports: int = 0         # put into a token account this transaction creates; refunded on close


def _token_info(account: dict[str, Any] | None) -> dict[str, Any] | None:
    if account is None:
        return None
    data = account.get("data")
    parsed = data.get("parsed") if isinstance(data, dict) else None
    return (parsed or {}).get("info") if isinstance(parsed, dict) else None


def _tokens(account: dict[str, Any] | None) -> int:
    info = _token_info(account)
    return int(((info or {}).get("tokenAmount") or {}).get("amount") or 0)


def _lamports(account: dict[str, Any] | None) -> int:
    return 0 if account is None else int(account.get("lamports") or 0)


def _wrapped_sol(*accounts: dict[str, Any] | None) -> bool:
    return any((_token_info(account) or {}).get("mint") == SOL_MINT for account in accounts)


def check_simulation(pre: list[dict[str, Any] | None], simulation: dict[str, Any], wallet: str,
                     expectation: Expectation) -> Effect:
    """pre and the simulated accounts: [wallet, traded token account, *the wallet's other token accounts].

    Wrapped SOL counts as SOL: a swap may unwrap an existing wrapped-SOL account into the wallet,
    and one that sent it elsewhere would show up as SOL spent. An account the transaction does not
    touch comes back unchanged (or, from older RPC nodes, empty) and is not judged.
    """
    if simulation.get("err") is not None:
        logs = " | ".join((simulation.get("logs") or [])[-3:])
        raise Refused(f"the simulation failed: {simulation['err']} {logs}".strip())
    post = simulation.get("accounts") or []
    if len(post) != len(pre) or len(pre) < 2 or pre[0] is None or post[0] is None:
        raise Refused("the simulation did not return the wallet's accounts")

    lamports_change = _lamports(post[0]) - _lamports(pre[0])
    for before, after in zip(pre[2:], post[2:], strict=True):
        if _wrapped_sol(before, after):
            lamports_change += _lamports(after) - _lamports(before)
    tokens_change = _tokens(post[1]) - _tokens(pre[1])
    if -lamports_change > expectation.max_lamports_spent:
        raise Refused(f"the swap would take {-lamports_change} lamports from the wallet; "
                      f"at most {expectation.max_lamports_spent} is allowed")
    if expectation.side == "buy" and tokens_change < max(expectation.min_tokens_received, 1):
        raise Refused(f"the swap would deliver {tokens_change} tokens; the quote promised at least "
                      f"{expectation.min_tokens_received}")
    if expectation.side == "sell":
        if -tokens_change > expectation.max_tokens_sent:
            raise Refused(f"the swap would take {-tokens_change} tokens; the order sells {expectation.max_tokens_sent}")
        if lamports_change < expectation.min_lamports_received:
            raise Refused(f"the swap would pay {lamports_change} lamports; at least "
                          f"{expectation.min_lamports_received} was expected")

    for index, (before, after) in enumerate(zip(pre[1:], post[1:], strict=True), start=1):
        info = _token_info(after)
        if info is None:
            continue  # closed (only possible when empty, or wrapped SOL, counted above) or untouched
        if info.get("owner") != wallet:
            raise Refused("the swap would hand a token account to another owner")
        delegate = info.get("delegate")
        if delegate and delegate != (_token_info(before) or {}).get("delegate"):
            raise Refused("the swap would let another address spend the wallet's tokens (delegate)")
        if index == 1 and delegate and expectation.side == "buy":
            raise Refused("the token account has a delegate who could spend what this buy delivers")
        if index > 1 and not _wrapped_sol(before, after) and _tokens(after) < _tokens(before):
            raise Refused("the swap would move tokens out of an account it has no business touching")
    rent = _lamports(post[1]) if pre[1] is None and post[1] is not None else 0
    return Effect(lamports_change=lamports_change, tokens_change=tokens_change, rent_lamports=rent)
