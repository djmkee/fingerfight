"""Hard guards every stage shares: one mint per lead, and no key material near the agents."""

import re
from collections.abc import Iterable

from .b58 import is_address


class MintMismatch(Exception):
    """A tool result or role reply named a different mint than the lead's. The lead must die."""

    def __init__(self, expected: str, observed: object, where: str) -> None:
        super().__init__(f"mint mismatch in {where}: expected {expected}, got {observed}")
        self.expected = expected
        self.observed = observed
        self.where = where


def check_mint(expected: str, observed: object, where: str) -> None:
    if observed != expected:
        raise MintMismatch(expected, observed, where)


def is_valid_mint(value: object) -> bool:
    return is_address(value)


class KeyMaterialError(RuntimeError):
    """Wallet secrets were offered to the agent process, which must never hold them."""


# Variable names that suggest wallet secrets. Their presence alone stops the desk:
# signing (not part of v1) belongs to a separate process that agents cannot reach.
_WALLET_SECRET_NAME = re.compile(
    r"PRIVATE_?KEY|SEED_?PHRASE|MNEMONIC|KEYPAIR|WALLET_?(KEY|SECRET|SEED)", re.IGNORECASE
)


def assert_no_wallet_secrets(names: Iterable[str], where: str) -> None:
    found = sorted(name for name in names if _WALLET_SECRET_NAME.search(name))
    if found:
        raise KeyMaterialError(
            f"{where} defines {', '.join(found)}. The desk never holds wallet secrets; "
            "unset them. Live signing is not part of v1."
        )


# An LLM reply that talks about keys or wallet access is discarded, never acted on.
_KEY_TALK = re.compile(
    r"private[\s_-]*key|secret[\s_-]*key|seed[\s_-]*phrase|recovery[\s_-]*phrase|mnemonic|keypair"
    r"|connect\s+(?:your|a|the)\s+wallet|wallet[\s_-]*connect",
    re.IGNORECASE,
)


def mentions_key_material(text: str) -> bool:
    return _KEY_TALK.search(text) is not None
