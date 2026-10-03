"""Required: every stage reads and writes the same mint; a mismatch marks the lead dead."""

import json
import sqlite3

import pytest

from desk.approval import ApprovalError, approve
from desk.db import Status
from desk.orchestrator import Orchestrator
from desk.tools.fixture import TokenSpec, demo_address

from conftest import ScriptedLLM, agree, lead_for

OTHER_MINT = demo_address("some-other-token")


def test_quote_for_a_different_mint_kills_the_lead(make_desk):
    spec = TokenSpec("SWAPPED")
    ctx = make_desk([spec])
    ctx.tools.quoter.output_mint_override[spec.mint] = OTHER_MINT

    Orchestrator(ctx).run_cycle()

    lead = lead_for(ctx, spec)
    assert lead.status == Status.DEAD
    assert "mint mismatch" in lead.reject_reason and OTHER_MINT in lead.reject_reason
    assert lead.quote_json is None
    assert ctx.db.leads(Status.AWAITING) == []
    dead = [row for row in ctx.db.events(lead.lead_id) if row["action"] == "dead"]
    assert dead and json.loads(dead[0]["payload"])["line"].startswith(f"LEAD-{lead.lead_id} | {spec.mint} | sniper | dead")


def test_llm_handoff_with_a_changed_mint_kills_the_lead(make_desk):
    spec, steady = TokenSpec("GARBLED"), TokenSpec("STEADY")
    honest = agree()

    def garble(mint: str) -> str:
        return mint[:-1] + ("X" if mint[-1] != "X" else "Y")

    def garble_risk(stage, leads):
        if stage != "risk":
            return honest(stage, leads)
        return "\n".join(
            f"LEAD-{lead['lead_id']} | {garble(lead['mint']) if lead['mint'] == spec.mint else lead['mint']}"
            f" | risk | pass | ok" for lead in leads)

    ctx = make_desk([spec, steady], llm=ScriptedLLM(garble_risk))
    Orchestrator(ctx).run_cycle()

    assert lead_for(ctx, spec).status == Status.DEAD
    assert "risk LLM handoff" in lead_for(ctx, spec).reject_reason
    assert lead_for(ctx, steady).status == Status.AWAITING


def test_exit_recheck_on_a_different_mint_kills_the_lead_and_zeroes_the_position(make_desk, clock):
    spec = TokenSpec("DRIFT")
    ctx = make_desk([spec])
    Orchestrator(ctx).run_cycle()
    lead = lead_for(ctx, spec)
    approve(ctx, lead.ref)

    ctx.tools.market.base_mint_override[spec.pair] = OTHER_MINT
    clock.advance(minutes=1)
    Orchestrator(ctx).run_cycle()

    assert ctx.db.get_lead(lead.lead_id).status == Status.DEAD
    position = ctx.db.get_position(lead.lead_id)
    assert not position.is_open
    assert position.exit_reason == "mint_mismatch"
    assert position.realized_sol == pytest.approx(-position.size_sol)


def test_tampered_stored_quote_kills_the_lead_at_approval(make_desk):
    spec = TokenSpec("TAMPER")
    ctx = make_desk([spec])
    Orchestrator(ctx).run_cycle()
    lead = lead_for(ctx, spec)
    plan = lead.quote() | {"output_mint": OTHER_MINT}
    ctx.db.update_lead(lead.lead_id, ctx.ts(), quote_json=json.dumps(plan))

    with pytest.raises(ApprovalError, match="dead"):
        approve(ctx, lead.ref)

    assert ctx.db.get_lead(lead.lead_id).status == Status.DEAD
    assert ctx.db.positions() == []


def test_storage_refuses_to_rewrite_a_mint(make_desk):
    spec = TokenSpec("FIXED")
    ctx = make_desk([spec])
    Orchestrator(ctx).run_cycle()
    lead = lead_for(ctx, spec)

    with pytest.raises(sqlite3.IntegrityError, match="mint cannot change"):
        ctx.db.conn.execute("UPDATE leads SET mint = ? WHERE lead_id = ?", (OTHER_MINT, lead.lead_id))
    with pytest.raises(ValueError, match="not writable"):
        ctx.db.update_lead(lead.lead_id, ctx.ts(), mint=OTHER_MINT)
    with pytest.raises(sqlite3.IntegrityError, match="differs from its lead"):
        ctx.db.open_position(lead_id=lead.lead_id, mint=OTHER_MINT, paper_entry=1.0, size_sol=0.1,
                             token_amount=1, token_decimals=6, entry_liquidity_usd=None, opened_at=ctx.ts())
