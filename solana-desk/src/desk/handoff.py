"""The role handoff line: LEAD-{id} | {mint} | {stage} | {result} | {evidence}."""

import re
from dataclasses import dataclass

STAGE_RESULTS: dict[str, frozenset[str]] = {
    "head": frozenset({"assign", "expired"}),
    "search": frozenset({"emit", "drop"}),
    "risk": frozenset({"pass", "fail"}),
    "sniper": frozenset({"awaiting_approval", "reject"}),
    "approver": frozenset({"buy", "skip"}),
    "approval": frozenset({"approve", "reject"}),
    "exit": frozenset({"hold", "exit"}),
}
DEAD = "dead"  # any stage may report it: the mint it saw differed from the lead's

_LEAD_REF = re.compile(r"LEAD-(\d+)", re.IGNORECASE)


class HandoffError(ValueError):
    """A handoff line or lead reference is malformed."""


@dataclass(frozen=True)
class Handoff:
    lead_id: int
    mint: str
    stage: str
    result: str
    evidence: str = ""

    def render(self) -> str:
        evidence = " ".join(self.evidence.replace("|", "/").split())
        return f"LEAD-{self.lead_id} | {self.mint} | {self.stage} | {self.result} | {evidence}".rstrip()


def lead_ref(lead_id: int) -> str:
    return f"LEAD-{lead_id}"


def parse_lead_ref(text: str) -> int:
    match = _LEAD_REF.fullmatch(text.strip())
    if match is None:
        raise HandoffError(f"expected a lead id like LEAD-123, got {text!r}")
    return int(match.group(1))


def parse(line: str) -> Handoff:
    parts = [part.strip() for part in line.strip().split("|", 4)]
    if len(parts) != 5:
        raise HandoffError(f"expected 5 '|'-separated fields, got {len(parts)}")
    ref, mint, stage, result, evidence = parts
    stage, result = stage.lower(), result.lower()
    if stage not in STAGE_RESULTS:
        raise HandoffError(f"unknown stage {stage!r}")
    if result != DEAD and result not in STAGE_RESULTS[stage]:
        raise HandoffError(f"result {result!r} is not valid for stage {stage!r}")
    return Handoff(parse_lead_ref(ref), mint, stage, result, evidence)
