"""Required: a Risk fail closes the lead and never reaches Sniper."""

import pytest

from desk.db import Status
from desk.orchestrator import Orchestrator
from desk.roles import Sniper
from desk.tools.fixture import TokenSpec

from conftest import ScriptedLLM, agree, lead_for


def quoted_mints(ctx) -> set[str]:
    return {mint for call in ctx.tools.quoter.calls for mint in call[:2]}


@pytest.mark.parametrize(("overrides", "code"), [
    ({"mint_authority": True}, "risk/mint_authority"),
    ({"freeze_authority": True}, "risk/freeze_authority"),
    ({"top10_pct": 55.0}, "risk/top10"),
    ({"token_program": "spl-token-2022", "extensions": ("permanentDelegate",)}, "risk/token2022_extension"),
])
def test_risk_fail_never_reaches_sniper(make_desk, overrides, code):
    bad, good = TokenSpec("BAD", **overrides), TokenSpec("GOOD")
    ctx = make_desk([bad, good])

    Orchestrator(ctx).run_cycle()

    lead = lead_for(ctx, bad)
    assert (lead.risk_status, lead.status) == ("fail", Status.REJECTED)
    assert lead.reject_reason.startswith(code)
    assert lead.risk_notes.startswith("FAIL")
    assert lead.quote_json is None
    assert bad.mint not in quoted_mints(ctx), "Sniper must never quote a Risk=fail lead"
    assert lead_for(ctx, good).status == Status.AWAITING  # control: the same cycle did reach Sniper


def test_missing_onchain_data_fails_closed(make_desk):
    spec = TokenSpec("DARK")
    ctx = make_desk([spec])
    ctx.tools.chain.down = True

    Orchestrator(ctx).run_cycle()

    lead = lead_for(ctx, spec)
    assert lead.risk_status == "fail"
    assert lead.reject_reason.startswith("risk/data_unavailable")
    assert spec.mint not in quoted_mints(ctx)


def test_missing_holder_data_skips_only_that_check(make_desk):
    spec = TokenSpec("NOHOLDERS", top10_pct=None)
    ctx = make_desk([spec])
    Orchestrator(ctx).run_cycle()
    lead = lead_for(ctx, spec)
    assert lead.risk_status == "pass"
    assert "no data, check skipped per policy" in lead.risk_notes


def test_llm_cannot_revive_a_coded_fail(make_desk):
    bad, good = TokenSpec("BAD", mint_authority=True), TokenSpec("GOOD")
    llm = ScriptedLLM(agree())  # says pass/emit/stage to everything it sees
    ctx = make_desk([bad, good], llm=llm)

    Orchestrator(ctx).run_cycle()

    assert lead_for(ctx, bad).risk_status == "fail"
    assert bad.mint not in quoted_mints(ctx)
    risk_batches = [call["leads"] for call in llm.calls if call["stage"] == "risk"]
    assert all(lead["mint"] != bad.mint for batch in risk_batches for lead in batch), \
        "coded failures are final and are not even offered to the LLM"


def test_llm_veto_fails_a_coded_pass(make_desk):
    spec = TokenSpec("VETOED")
    ctx = make_desk([spec], llm=ScriptedLLM(agree(risk="fail")))
    Orchestrator(ctx).run_cycle()
    lead = lead_for(ctx, spec)
    assert (lead.risk_status, lead.reject_reason.split(":")[0]) == ("fail", "risk/llm")
    assert spec.mint not in quoted_mints(ctx)


def test_silent_llm_fails_closed(make_desk):
    spec = TokenSpec("UNANSWERED")
    ctx = make_desk([spec], llm=ScriptedLLM(lambda stage, leads: "I am not sure."))
    Orchestrator(ctx).run_cycle()
    assert lead_for(ctx, spec).risk_status == "fail"
    assert "no verdict" in lead_for(ctx, spec).risk_notes


def test_sniper_refuses_a_row_whose_risk_status_is_not_pass(make_desk):
    spec = TokenSpec("FORGED")
    ctx = make_desk([spec])
    lead_id = ctx.db.insert_lead(ts=ctx.ts(), mint=spec.mint, pair=spec.pair, liquidity_usd=50_000.0,
                                 age_minutes=30.0, source="test", market_json="{}")
    # Forge the status without a Risk=pass, as a buggy or tampered writer might.
    assert ctx.db.transition(lead_id, Status.NEW, Status.RISK_PASSED, ctx.ts(), risk_status="fail")

    Sniper(ctx).run()

    lead = ctx.db.get_lead(lead_id)
    assert lead.status == Status.REJECTED
    assert lead.reject_reason.startswith("sniper/integrity")
    assert ctx.tools.quoter.calls == []
