"""Signer settings from config/signer.yaml. The agent process never reads this file."""

from dataclasses import dataclass
from pathlib import Path

import yaml

from desk.b58 import is_address


class SignerConfigError(ValueError):
    """config/signer.yaml is missing or malformed."""


@dataclass(frozen=True)
class SignerConfig:
    keypair_path: Path
    priority_fee_max_lamports: int
    fee_reserve_lamports: int
    order_max_age_seconds: float
    poll_seconds: float
    allowed_programs: frozenset[str]


def load_signer_config(path: Path) -> SignerConfig:
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise SignerConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SignerConfigError(f"{path} must be a mapping")
    known = {"keypair_path", "priority_fee_max_lamports", "fee_reserve_lamports", "order_max_age_seconds",
             "poll_seconds", "allowed_programs"}
    if unknown := sorted(set(data) - known):
        raise SignerConfigError(f"unknown signer settings: {', '.join(unknown)}")
    if missing := sorted(known - set(data)):
        raise SignerConfigError(f"missing signer settings: {', '.join(missing)}")

    def whole(key: str) -> int:
        value = data[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SignerConfigError(f"{key} must be a whole number of 0 or more")
        return value

    def seconds(key: str) -> float:
        value = data[key]
        if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
            raise SignerConfigError(f"{key} must be a positive number")
        return float(value)

    programs = data["allowed_programs"]
    if not isinstance(programs, list) or not programs or not all(is_address(item) for item in programs):
        raise SignerConfigError("allowed_programs must be a list of program addresses")
    if not isinstance(data["keypair_path"], str) or not data["keypair_path"].strip():
        raise SignerConfigError("keypair_path must be a file path")
    return SignerConfig(
        keypair_path=Path(data["keypair_path"]).expanduser(),
        priority_fee_max_lamports=whole("priority_fee_max_lamports"),
        fee_reserve_lamports=whole("fee_reserve_lamports"),
        order_max_age_seconds=seconds("order_max_age_seconds"),
        poll_seconds=seconds("poll_seconds"),
        allowed_programs=frozenset(programs),
    )
