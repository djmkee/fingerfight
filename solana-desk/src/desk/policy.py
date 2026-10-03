"""Risk policy: loaded once from config/policy.yaml at startup and enforced in code."""

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from .b58 import is_address


class PolicyError(ValueError):
    """The policy file is missing, malformed, or asks for something v1 does not allow."""


@dataclass(frozen=True)
class ExitPolicy:
    check_interval_minutes: float
    liquidity_drop_pct: float
    max_exit_impact_pct: float
    time_stop_minutes: float
    max_recheck_failures: int


@dataclass(frozen=True)
class Policy:
    paper_mode: bool
    paper_equity_sol: float
    max_position_pct: float
    max_open_positions: int
    daily_loss_halt_pct: float
    max_failed_sends: int
    min_liquidity_usd: float
    min_token_age_minutes: float
    max_new_leads_per_cycle: int
    lead_cooldown_hours: float
    reject_mint_authority_active: bool
    reject_freeze_authority_set: bool
    max_top10_holder_pct: float
    blocked_token2022_extensions: tuple[str, ...]
    pool_authorities: tuple[str, ...]
    max_price_impact_pct: float
    slippage_bps: int
    approval_ttl_minutes: float
    exit: ExitPolicy

    def for_prompt(self) -> dict[str, Any]:
        """The thresholds a role may cite. Prompts get them as context; code enforces them."""
        view = asdict(self)
        del view["pool_authorities"]
        return view


def load_policy(path: str | Path) -> Policy:
    try:
        data = yaml.safe_load(Path(path).read_text())
    except OSError as exc:
        raise PolicyError(f"cannot read policy file {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise PolicyError(f"policy file {path} is not valid YAML: {exc}") from exc
    return parse_policy(data)


def parse_policy(data: object) -> Policy:
    top = _Reader(data, "")
    ex = _Reader(top.value("exit"), "exit.")
    policy = Policy(
        paper_mode=top.boolean("paper_mode"),
        paper_equity_sol=top.number("paper_equity_sol", positive=True),
        max_position_pct=top.percent("max_position_pct"),
        max_open_positions=top.count("max_open_positions"),
        daily_loss_halt_pct=top.percent("daily_loss_halt_pct"),
        max_failed_sends=top.count("max_failed_sends"),
        min_liquidity_usd=top.number("min_liquidity_usd"),
        min_token_age_minutes=top.number("min_token_age_minutes"),
        max_new_leads_per_cycle=top.count("max_new_leads_per_cycle"),
        lead_cooldown_hours=top.number("lead_cooldown_hours"),
        reject_mint_authority_active=top.boolean("reject_mint_authority_active"),
        reject_freeze_authority_set=top.boolean("reject_freeze_authority_set"),
        max_top10_holder_pct=top.percent("max_top10_holder_pct"),
        blocked_token2022_extensions=top.strings("blocked_token2022_extensions"),
        pool_authorities=top.strings("pool_authorities"),
        max_price_impact_pct=top.percent("max_price_impact_pct"),
        slippage_bps=top.count("slippage_bps"),
        approval_ttl_minutes=top.number("approval_ttl_minutes", positive=True),
        exit=ExitPolicy(
            check_interval_minutes=ex.number("check_interval_minutes"),
            liquidity_drop_pct=ex.percent("liquidity_drop_pct"),
            max_exit_impact_pct=ex.percent("max_exit_impact_pct"),
            time_stop_minutes=ex.number("time_stop_minutes", positive=True),
            max_recheck_failures=ex.count("max_recheck_failures"),
        ),
    )
    top.reject_unknown()
    ex.reject_unknown()

    if not policy.paper_mode:
        raise PolicyError("paper_mode must be true: v1 is paper-only and has no live broadcast")
    if not (policy.reject_mint_authority_active and policy.reject_freeze_authority_set):
        raise PolicyError("mint and freeze authority rejection cannot be disabled in v1")
    if policy.slippage_bps > 10_000:
        raise PolicyError("slippage_bps cannot exceed 10000")
    bad = [address for address in policy.pool_authorities if not is_address(address)]
    if bad:
        raise PolicyError(f"pool_authorities holds invalid addresses: {', '.join(bad)}")
    return policy


class _Reader:
    """Typed, range-checked access to one mapping of the policy file."""

    def __init__(self, data: object, prefix: str) -> None:
        if not isinstance(data, dict):
            raise PolicyError(f"{prefix.rstrip('.') or 'the policy file'} must be a mapping")
        self.data = data
        self.prefix = prefix
        self.seen: set[str] = set()

    def value(self, key: str) -> Any:
        self.seen.add(key)
        if key not in self.data:
            raise PolicyError(f"missing policy key: {self.prefix}{key}")
        return self.data[key]

    def boolean(self, key: str) -> bool:
        value = self.value(key)
        if not isinstance(value, bool):
            raise PolicyError(f"{self.prefix}{key} must be true or false")
        return value

    def number(self, key: str, *, positive: bool = False) -> float:
        value = self.value(key)
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise PolicyError(f"{self.prefix}{key} must be a number")
        if value < 0 or (positive and value == 0):
            raise PolicyError(f"{self.prefix}{key} must be {'positive' if positive else 'zero or more'}")
        return float(value)

    def percent(self, key: str) -> float:
        value = self.number(key, positive=True)
        if value > 100:
            raise PolicyError(f"{self.prefix}{key} is a percentage and cannot exceed 100")
        return value

    def count(self, key: str) -> int:
        value = self.value(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise PolicyError(f"{self.prefix}{key} must be a whole number of at least 1")
        return value

    def strings(self, key: str) -> tuple[str, ...]:
        value = self.value(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise PolicyError(f"{self.prefix}{key} must be a list of strings")
        return tuple(value)

    def reject_unknown(self) -> None:
        unknown = sorted(set(self.data) - self.seen)
        if unknown:
            raise PolicyError(f"unknown policy keys: {', '.join(self.prefix + key for key in unknown)}")
