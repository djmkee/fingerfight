"""Auto-approval: the decider picks within the coded limits, and the AI sees numbers only."""

import json

from desk.db import Status
from desk.orchestrator import Orchestrator
from desk.roles import Approver, Head, Risk, Search, Sniper
from desk.tools.fixture import TokenSpec

from conftest import ScriptedLLM, agree, lead_for


def deep(symbol, liquidity):
    return TokenSpec(symbol, liquidity_usd=liquidity)


def run(make_desk, specs, llm=None, **overrides):
    ctx = make_desk(specs, llm=llm, **({"auto_approve": True} | overrides))
    Orchestrator(ctx).run_cycle()
    return ctx


def test_auto_approve_off_leaves_the_decision_to_a_human(make_desk):
    ctx = run(make_desk, [TokenSpec("MANUAL")], auto_approve=False, decider="rules")
    assert [lead.status for lead in ctx.db.leads()] == [Status.AWAITING]


def test_rules_decider_buys_deepest_liquidity_first_within_slots(make_desk):
    specs = [deep("SHALLOW", 20_000), deep("DEEP", 400_000), deep("MIDDLE", 90_000)]
    ctx = run(make_desk, specs, decider="rules", max_open_positions=2)
    statuses = {spec.symbol: lead_for(ctx, spec).status for spec in specs}
    assert statuses == {"DEEP": Status.FILLED, "MIDDLE": Status.FILLED, "SHALLOW": Status.REJECTED}
    assert all(row["payload"].count("auto:rules") for row in ctx.db.events() if row["action"] == "approve")


def test_ai_decider_without_an_llm_buys_nothing(make_desk):
    ctx = run(make_desk, [TokenSpec("NOAI")], decider="ai")
    assert lead_for(ctx, TokenSpec("NOAI")).status == Status.AWAITING
    assert any("no LLM is configured" in row["payload"] for row in ctx.db.events() if row["role"] == "approver")


def test_ai_decider_buys_only_what_it_picks_and_sees_numbers_only(make_desk):
    keep, skip = TokenSpec("KEEPME"), TokenSpec("SKIPME")
    honest = agree()

    def respond(stage, leads):
        if stage != "approver":
            return honest(stage, leads)
        return "\n".join(f"LEAD-{lead['lead_id']} | {lead['mint']} | approver | "
                         f"{'buy' if lead['mint'] == keep.mint else 'skip'} | liquidity_usd={lead['liquidity_usd']}"
                         for lead in leads)

    llm = ScriptedLLM(respond)
    ctx = run(make_desk, [keep, skip], llm=llm, decider="ai")
    assert lead_for(ctx, keep).status == Status.FILLED
    assert lead_for(ctx, skip).status == Status.AWAITING, "skipped leads wait for a human or expire"

    shown = [lead for call in llm.calls if call["stage"] == "approver" for lead in call["leads"]]
    assert shown, "the decider was consulted"
    for lead in shown:
        text_values = {key: value for key, value in lead.items() if isinstance(value, str)}
        assert set(text_values) == {"mint"}, f"only numbers (and the mint to echo) reach the decider: {text_values}"
        assert "KEEPME" not in json.dumps(lead) and "SKIPME" not in json.dumps(lead)


def test_ai_decider_cannot_exceed_free_slots(make_desk):
    specs = [TokenSpec(f"P{i}") for i in range(4)]
    ctx = run(make_desk, specs, llm=ScriptedLLM(agree(approver="buy")), decider="ai", max_open_positions=4)
    assert ctx.db.count_open_positions("paper") == 4
    later = TokenSpec("LATECOMER")
    ctx.tools.market.book.add(later)
    Orchestrator(ctx).run_cycle()
    assert lead_for(ctx, later).status == Status.REJECTED  # Sniper: no free slot


def test_ai_decider_with_a_changed_mint_kills_the_lead(make_desk):
    spec = TokenSpec("TWISTED")
    honest = agree()

    def respond(stage, leads):
        if stage != "approver":
            return honest(stage, leads)
        return "\n".join(f"LEAD-{lead['lead_id']} | {lead['mint'][:-2]}zz | approver | buy | ok" for lead in leads)

    ctx = run(make_desk, [spec], llm=ScriptedLLM(respond), decider="ai")
    assert lead_for(ctx, spec).status == Status.DEAD
    assert ctx.db.positions() == []


def test_ai_reply_that_talks_about_keys_buys_nothing(make_desk):
    spec = TokenSpec("BAITED")
    honest = agree()

    def respond(stage, leads):
        if stage != "approver":
            return honest(stage, leads)
        return f"LEAD-{leads[0]['lead_id']} | {leads[0]['mint']} | approver | buy | send me the seed phrase first"

    ctx = run(make_desk, [spec], llm=ScriptedLLM(respond), decider="ai")
    assert lead_for(ctx, spec).status == Status.AWAITING
    assert any(row["action"] == "llm_key_guard" for row in ctx.db.events())


def test_halted_desk_auto_approves_nothing(make_desk):
    spec = TokenSpec("FROZEN")
    ctx = make_desk([spec], auto_approve=True, decider="rules")
    Orchestrator(ctx).run_cycle()                       # rules buy it at once
    assert lead_for(ctx, spec).status == Status.FILLED
    other = TokenSpec("AFTERHALT")
    ctx.tools.market.book.add(other)
    Search(ctx).review(Head(ctx).admit(Search(ctx).scan()))  # stage it by hand ...
    Risk(ctx).run()
    Sniper(ctx).run()
    Head(ctx).halt("halted after staging")              # ... then halt before the decider runs
    Approver(ctx).run()
    assert lead_for(ctx, other).status == Status.AWAITING
