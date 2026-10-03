import pytest
import yaml

from desk.policy import PolicyError, load_policy, parse_policy

from conftest import ROOT


def raw_policy() -> dict:
    return yaml.safe_load((ROOT / "config" / "policy.yaml").read_text())


def test_repo_policy_carries_the_spec_limits(policy):
    assert policy.paper_mode is True
    assert policy.max_position_pct == 3
    assert policy.max_open_positions == 4
    assert policy.daily_loss_halt_pct == 10
    assert policy.min_liquidity_usd == 15_000
    assert policy.max_price_impact_pct == 3
    assert policy.min_token_age_minutes == 5
    assert policy.reject_mint_authority_active and policy.reject_freeze_authority_set
    assert 0 < policy.max_top10_holder_pct <= 100


@pytest.mark.parametrize(("key", "value", "message"), [
    ("paper_mode", False, "paper-only"),
    ("reject_mint_authority_active", False, "cannot be disabled"),
    ("reject_freeze_authority_set", False, "cannot be disabled"),
    ("max_position_pct", 0, "positive"),
    ("max_position_pct", 150, "cannot exceed 100"),
    ("max_open_positions", 2.5, "whole number"),
    ("min_liquidity_usd", "15000", "must be a number"),
    ("min_liquidity_usd", True, "must be a number"),
    ("pool_authorities", ["not-an-address"], "invalid addresses"),
])
def test_unsafe_or_malformed_values_are_refused(key, value, message):
    data = raw_policy() | {key: value}
    with pytest.raises(PolicyError, match=message):
        parse_policy(data)


def test_unknown_and_missing_keys_are_refused():
    with pytest.raises(PolicyError, match="unknown policy keys: max_postion_pct"):
        parse_policy(raw_policy() | {"max_postion_pct": 3})
    data = raw_policy()
    del data["daily_loss_halt_pct"]
    with pytest.raises(PolicyError, match="missing policy key: daily_loss_halt_pct"):
        parse_policy(data)
    data = raw_policy()
    data["exit"] = {**data["exit"], "surprise": 1}
    with pytest.raises(PolicyError, match="exit.surprise"):
        parse_policy(data)


def test_unreadable_policy_file(tmp_path):
    with pytest.raises(PolicyError, match="cannot read"):
        load_policy(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("paper_mode: [unclosed")
    with pytest.raises(PolicyError, match="not valid YAML"):
        load_policy(bad)
