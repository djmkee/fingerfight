"""The trading wallet's keypair file. Only the signer process ever reads it.

The file uses the Solana CLI format, a JSON array of the 64 secret-key bytes, and is created
with owner-only permissions where the operating system supports them.
"""

import json
import os
from pathlib import Path

from solders.keypair import Keypair


class WalletError(RuntimeError):
    """The wallet file is missing, malformed, or would be overwritten."""


def create_wallet(path: Path) -> Keypair:
    """A brand-new keypair. Refuses to replace an existing wallet file."""
    keypair = Keypair()
    _write(path, keypair)
    return keypair


def import_wallet(path: Path, secret: str) -> Keypair:
    """A keypair from a base58 private key (wallet-app export) or a JSON byte array."""
    secret = secret.strip()
    try:
        if secret.startswith("["):
            keypair = _from_bytes(bytes(json.loads(secret)))
        else:
            keypair = _from_bytes(bytes(Keypair.from_base58_string(secret)))
    except (ValueError, TypeError):
        raise WalletError("that is not a valid Solana private key") from None
    _write(path, keypair)
    return keypair


def _from_bytes(raw: bytes) -> Keypair:
    """A 64-byte keypair whose public half really belongs to its secret half."""
    keypair = Keypair.from_bytes(raw)
    if Keypair.from_seed(raw[:32]).pubkey() != keypair.pubkey():
        raise ValueError("the public key does not match the secret key")
    return keypair


def load_wallet(path: Path) -> Keypair:
    if not path.is_file():
        raise WalletError(f"no trading wallet at {path}. Create one with: python signer.py init")
    try:
        data = json.loads(path.read_text())
        if not (isinstance(data, list) and len(data) == 64
                and all(isinstance(byte, int) and 0 <= byte <= 255 for byte in data)):
            raise ValueError("expected 64 numbers")
        return _from_bytes(bytes(data))
    except (OSError, ValueError) as exc:
        raise WalletError(f"{path} is not a Solana keypair file ({exc})") from None


def _write(path: Path, keypair: Keypair) -> None:
    if path.exists():
        raise WalletError(f"{path} already exists; refusing to overwrite a wallet")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(list(bytes(keypair)), handle)
