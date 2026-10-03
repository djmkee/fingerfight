"""The orchestrator loop end to end on the demo fixture, plus loop-level guarantees."""

import json
import sqlite3
from collections import Counter

import pytest

from desk.db import Status
from desk.orchestrator import Orchestrator
from desk.reporting import collect_stats
from desk.tools.fixture import TokenSpec, load_specs

from conftest import ROOT


def test_demo_market_rejects_almost_everything(make_desk):
    ctx = make_desk(load_specs(ROOT / "fixtures" / "demo_market.json"))
    summary = Orchestrator(ctx).loop(cycles=1, interval_s=0)

    leads = ctx.db.leads()
    assert ctx.db.scalar("SELECT COUNT(*) FROM candidates") == 12
    assert len(leads) == 8
    assert Counter(lead.status for lead in leads) == {Status.REJECTED: 6, Status.AWAITING: 2}
    assert Counter(lead.reject_reason.split(":")[0] for lead in leads if lead.reject_reason) == {
        "risk/mint_authority": 1, "risk/freeze_authority": 1, "risk/top10": 1,
        "risk/token2022_extension": 1, "sniper/impact": 1, "sniper/sell_route": 1}
    assert all(lead.source.startswith("fixture/") for lead in leads)
    assert "TOTAL: scanned 12 | rejected 10 | awaiting approval 2" in summary
    assert "profitable" in summary


def test_every_lead_row_keeps_the_facts_its_decisions_used(make_desk):
    ctx = make_desk(load_specs(ROOT / "fixtures" / "demo_market.json"))
    Orchestrator(ctx).run_cycle()
    for lead in ctx.db.leads():
        assert lead.market()["base_mint"] == lead.mint
        assert lead.liquidity_usd is not None and lead.age_minutes is not None
        assert lead.authorities_json is not None, f"{lead.ref} was decided without stored mint data"
        if lead.status == Status.AWAITING:
            plan = lead.quote()
            assert plan["output_mint"] == lead.mint
            assert plan["price_impact_pct"] <= ctx.policy.max_price_impact_pct
            assert plan["jupiter_quote"]["outputMint"] == lead.mint


def test_second_cycle_does_not_duplicate_leads(make_desk, clock):
    ctx = make_desk([TokenSpec("AAA"), TokenSpec("BBB")])
    desk = Orchestrator(ctx)
    desk.run_cycle()
    clock.advance(minutes=1)
    desk.run_cycle()
    assert len(ctx.db.leads()) == 2
    assert ctx.db.scalar("SELECT COUNT(*) FROM candidates WHERE last_result = 'duplicate'") == 2
    run_stats = collect_stats(ctx.db, since="2026-10-03T00:00:00+00:00")
    assert (run_stats.scanned, len(run_stats.leads), run_stats.duplicates) == (2, 2, 0), "no double counting"
    later = collect_stats(ctx.db, since=ctx.ts())
    assert (later.duplicates, len(later.leads)) == (2, 0)


def test_new_leads_per_cycle_and_position_slots_are_capped(make_desk, clock):
    specs = [TokenSpec(f"T{i}") for i in range(10)]
    ctx = make_desk(specs, max_new_leads_per_cycle=3, max_open_positions=2)
    desk = Orchestrator(ctx)
    desk.run_cycle()
    assert len(ctx.db.leads()) == 3
    clock.advance(minutes=1)
    desk.run_cycle()
    assert len(ctx.db.leads()) == 6
    assert ctx.db.count_leads(Status.AWAITING) == 2
    assert all(lead.reject_reason.startswith("sniper/no_slot")
               for lead in ctx.db.leads(Status.REJECTED))


def test_discovery_outage_scans_nothing_and_admits_nothing(make_desk):
    ctx = make_desk([TokenSpec("AAA")])
    ctx.tools.market.down = True
    summary = Orchestrator(ctx).loop(cycles=1, interval_s=0)
    assert ctx.db.leads() == []
    assert "TOTAL: scanned 0 | rejected 0 | awaiting approval 0" in summary
    assert any(row["action"] == "tool_error" for row in ctx.db.events())


def test_filtered_candidates_are_logged_with_reasons(make_desk):
    young, thin = TokenSpec("YOUNG", age_minutes=1.0), TokenSpec("THIN", liquidity_usd=900.0)
    ctx = make_desk([young, thin])
    Orchestrator(ctx).run_cycle()
    assert ctx.db.leads() == []
    reasons = {json.loads(row["payload"])["mint"]: json.loads(row["payload"])["reason"]
               for row in ctx.db.events() if row["action"] == "filtered"}
    assert reasons[young.mint].startswith("age: 1.0m < 5m")
    assert reasons[thin.mint].startswith("liquidity: $900 < $15,000")


def test_events_are_append_only(make_desk):
    ctx = make_desk([TokenSpec("AAA")])
    Orchestrator(ctx).run_cycle()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ctx.db.conn.execute("UPDATE events SET action = 'edited'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ctx.db.conn.execute("DELETE FROM events")
    actions = [row["action"] for row in ctx.db.events(limit=500)]
    assert actions[0] == "cycle_start" and actions[-1] == "cycle_end"
