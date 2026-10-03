"""Shared role plumbing: events, handoff lines, and the optional LLM consult.

Code makes every decision. When an LLM is configured, a role also sends its system prompt
plus the stored tool results for a batch of leads and reads back handoff lines. Those lines
can only make an outcome more conservative; nothing an LLM says can pass a lead that code
failed, change a number, or reach a key.
"""

import json
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ..context import DeskContext
from ..db import DeskDB, Lead
from ..guards import MintMismatch, mentions_key_material
from ..handoff import Handoff, HandoffError, parse
from ..llm import LLMError
from ..policy import Policy
from ..tools.base import Toolbox


@dataclass
class Opinions:
    """What the LLM said about a batch of leads, after validation."""

    verdicts: dict[int, Handoff] = field(default_factory=dict)
    mismatches: dict[int, MintMismatch] = field(default_factory=dict)
    failed: str | None = None  # the call failed or the reply was discarded


class Role:
    name: ClassVar[str]
    llm_results: ClassVar[frozenset[str]] = frozenset()
    conservative: ClassVar[str] = ""  # the result an LLM may push a lead toward

    def __init__(self, ctx: DeskContext) -> None:
        self.ctx = ctx

    @property
    def db(self) -> DeskDB:
        return self.ctx.db

    @property
    def policy(self) -> Policy:
        return self.ctx.policy

    @property
    def tools(self) -> Toolbox:
        if self.ctx.tools is None:
            raise RuntimeError(f"the {self.name} role needs data tools, and none are configured")
        return self.ctx.tools

    def log(self, action: str, lead_id: int | None = None, **payload: Any) -> None:
        self.ctx.log(self.name, action, lead_id, **payload)

    def handoff(self, lead: Lead, result: str, evidence: str) -> str:
        line = Handoff(lead.lead_id, lead.mint, self.name, result, evidence).render()
        self.log(result, lead.lead_id, line=line)
        self.ctx.echo(line)
        return line

    def consult(self, items: list[dict[str, Any]]) -> Opinions | None:
        """Ask the LLM for this role's verdicts on `items`. None when no LLM is configured."""
        if self.ctx.llm is None or not items:
            return None
        expected = {item["lead_id"]: item["mint"] for item in items}
        message = json.dumps(
            {"stage": self.name, "policy": self.policy.for_prompt(), "leads": items}, default=str
        )
        try:
            reply = self.ctx.llm.complete(self.ctx.prompts.system(self.name), message)
        except LLMError as exc:
            self.log("llm_error", error=str(exc))
            return Opinions(failed=f"LLM call failed: {exc}")
        if mentions_key_material(reply):
            self.log("llm_key_guard", note="reply mentioned key material; discarded unread")
            return Opinions(failed="reply discarded by the key guard")

        opinions = Opinions()
        for raw in reply.splitlines():
            line = raw.strip().strip("`").lstrip("-*• ").strip()
            if not line.upper().startswith("LEAD-"):
                continue
            try:
                verdict = parse(line)
            except HandoffError as exc:
                self.log("llm_unparsed", line=line[:300], error=str(exc))
                continue
            if (verdict.lead_id not in expected or verdict.stage != self.name
                    or verdict.result not in self.llm_results):
                self.log("llm_ignored", verdict.lead_id if verdict.lead_id in expected else None,
                         line=verdict.render())
                continue
            if verdict.mint != expected[verdict.lead_id]:
                opinions.mismatches[verdict.lead_id] = MintMismatch(
                    expected[verdict.lead_id], verdict.mint, f"{self.name} LLM handoff")
                continue
            # Conflicting lines for one lead resolve to the conservative result.
            if verdict.lead_id not in opinions.verdicts or verdict.result == self.conservative:
                opinions.verdicts[verdict.lead_id] = verdict
            self.log("llm_verdict", verdict.lead_id, line=verdict.render())
        return opinions

    def required_verdict(self, opinions: Opinions | None, lead: Lead) -> str | None:
        """Where an enabled LLM must agree (Risk, Sniper): why it blocks `lead`, or None."""
        if opinions is None:
            return None
        if opinions.failed:
            return f"LLM review unavailable ({opinions.failed}); failing closed"
        verdict = opinions.verdicts.get(lead.lead_id)
        if verdict is None:
            return "LLM gave no verdict; failing closed"
        if verdict.result == self.conservative:
            return f"LLM veto: {verdict.evidence or verdict.result}"
        return None

    def optional_verdict(self, opinions: Opinions | None, lead: Lead) -> Handoff | None:
        """Where the LLM may only add a conservative outcome (Search, Exit): that verdict, or None."""
        if opinions is None or opinions.failed:
            return None
        verdict = opinions.verdicts.get(lead.lead_id)
        return verdict if verdict is not None and verdict.result == self.conservative else None
