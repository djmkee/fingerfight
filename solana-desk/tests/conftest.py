import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from desk.context import DeskContext
from desk.db import DeskDB, Lead
from desk.policy import Policy, load_policy
from desk.prompts import PromptBook
from desk.tools.fixture import TokenSpec, fixture_toolbox

ROOT = Path(__file__).resolve().parents[1]


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class ScriptedLLM:
    """Stands in for the OpenAI-compatible client: `respond(stage, leads)` returns the reply text."""

    def __init__(self, respond: Callable[[str, list[dict[str, Any]]], str]) -> None:
        self.respond = respond
        self.calls: list[dict[str, Any]] = []

    def complete(self, system: str, user: str) -> str:
        payload = json.loads(user)
        self.calls.append(payload)
        return self.respond(payload.get("stage", "head"), payload.get("leads", []))


def agree(**results: str) -> Callable[[str, list[dict[str, Any]]], str]:
    """An LLM that answers every lead with `results[stage]`, copying the mint exactly."""
    defaults = {"search": "emit", "risk": "pass", "sniper": "awaiting_approval", "exit": "hold"} | results
    return lambda stage, leads: "\n".join(
        f"LEAD-{lead['lead_id']} | {lead['mint']} | {stage} | {defaults[stage]} | per supplied fields"
        for lead in leads)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def policy() -> Policy:
    return load_policy(ROOT / "config" / "policy.yaml")


@pytest.fixture
def make_desk(tmp_path: Path, policy: Policy, clock: FakeClock) -> Callable[..., DeskContext]:
    """desk = make_desk(tokens, llm=None, **policy_overrides): fresh DB, fixture tools, fake clock."""
    prompts = PromptBook(ROOT / "prompts")

    def make(tokens: list[TokenSpec], llm: Any = None, **overrides: Any) -> DeskContext:
        return DeskContext(
            policy=replace(policy, **overrides),
            db=DeskDB(tmp_path / "desk.sqlite3"),
            prompts=prompts,
            tools=fixture_toolbox(tokens, clock()),
            llm=llm,
            clock=clock,
            echo=lambda _line: None,
        )

    return make


def lead_for(ctx: DeskContext, spec: TokenSpec) -> Lead:
    leads = [lead for lead in ctx.db.leads() if lead.mint == spec.mint]
    assert len(leads) == 1, f"expected one lead for {spec.symbol}, found {len(leads)}"
    return leads[0]
