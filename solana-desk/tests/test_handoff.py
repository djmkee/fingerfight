import pytest

from desk.handoff import Handoff, HandoffError, parse, parse_lead_ref

MINT = "So11111111111111111111111111111111111111112"


def test_round_trip_and_format():
    line = Handoff(12, MINT, "risk", "pass", "liquidity_usd=23400 age_minutes=12.5").render()
    assert line == f"LEAD-12 | {MINT} | risk | pass | liquidity_usd=23400 age_minutes=12.5"
    assert parse(line) == Handoff(12, MINT, "risk", "pass", "liquidity_usd=23400 age_minutes=12.5")


def test_evidence_cannot_break_the_format():
    line = Handoff(3, MINT, "sniper", "reject", "impact | 7.5%\nnext line").render()
    assert line.count("|") == 4 and "\n" not in line
    assert parse(line).evidence == "impact / 7.5% next line"


@pytest.mark.parametrize("line", [
    f"LEAD-1 | {MINT} | risk | maybe | ?",       # result not allowed for the stage
    f"LEAD-1 | {MINT} | trader | buy | now",      # unknown stage
    f"LEAD-x | {MINT} | risk | pass | ok",        # bad id
    f"LEAD-1 | {MINT} | risk",                    # too few fields
])
def test_malformed_lines_are_rejected(line):
    with pytest.raises(HandoffError):
        parse(line)


def test_any_stage_may_report_dead():
    assert parse(f"LEAD-4 | {MINT} | exit | dead | mint mismatch").result == "dead"


def test_lead_refs():
    assert parse_lead_ref("LEAD-123") == 123
    assert parse_lead_ref(" lead-7 ") == 7
    for bad in ("123", "LEAD-", "LEAD-12a", "LEAD-1 LEAD-2"):
        with pytest.raises(HandoffError):
            parse_lead_ref(bad)
