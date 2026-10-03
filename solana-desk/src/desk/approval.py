"""Human gate: `approve LEAD-123` or `reject LEAD-123`, by lead id and nothing else.

In paper mode an approval only marks the lead paper_filled and opens a paper position at the
stored quote's price. It reads the database and nothing more: no tools, no key, no signer.
"""

from .clock import parse_iso
from .context import DeskContext
from .db import Lead, Position, Status
from .guards import MintMismatch, check_mint
from .handoff import Handoff, HandoffError, parse_lead_ref
from .roles.head import Head

_PLAN_FIELDS = ("expires_at", "output_mint", "entry_price_sol", "size_sol", "out_amount",
                "token_decimals", "price_impact_pct")


class ApprovalError(RuntimeError):
    """The decision was refused; the lead is unchanged unless the message says otherwise."""


def _load(ctx: DeskContext, ref: str) -> Lead:
    try:
        lead_id = parse_lead_ref(ref)
    except HandoffError as exc:
        raise ApprovalError(str(exc)) from exc
    lead = ctx.db.get_lead(lead_id)
    if lead is None:
        raise ApprovalError(f"{ref} does not exist")
    if lead.status != Status.AWAITING:
        raise ApprovalError(f"{lead.ref} is {lead.status}, not awaiting_approval")
    return lead


def approve(ctx: DeskContext, ref: str, by: str = "human") -> Position:
    lead = _load(ctx, ref)
    if not ctx.policy.paper_mode:
        raise ApprovalError("live broadcast is not part of v1; only paper approvals exist")
    if Head(ctx).apply_halt_rules():
        raise ApprovalError(f"the desk is halted ({ctx.db.halt_reason()}); a human must clear it first")
    if lead.risk_status != "pass":
        raise ApprovalError(f"{lead.ref} has risk_status={lead.risk_status!r}; only Risk=pass leads can fill")
    plan = lead.quote()
    missing = [name for name in _PLAN_FIELDS if plan.get(name) is None]
    if missing:
        raise ApprovalError(f"{lead.ref} has an incomplete stored quote (missing {', '.join(missing)})")
    try:
        check_mint(lead.mint, plan["output_mint"], "stored quote outputMint")
    except MintMismatch as exc:
        ctx.kill_lead(lead, "approval", exc)
        raise ApprovalError(f"{lead.ref} is now dead: {exc}") from exc
    if ctx.now() >= parse_iso(plan["expires_at"]):
        ctx.db.transition(lead.lead_id, Status.AWAITING, Status.EXPIRED, ctx.ts(),
                          reject_reason=f"head/expired: no human decision before {plan['expires_at']}")
        raise ApprovalError(f"{lead.ref}'s quote expired at {plan['expires_at']}; nothing filled")
    if ctx.db.count_open_positions() >= ctx.policy.max_open_positions:
        raise ApprovalError(f"max_open_positions ({ctx.policy.max_open_positions}) reached; nothing filled")

    line = Handoff(lead.lead_id, lead.mint, "approval", "approve",
                   f"by={by} paper fill at quoted price entry_price_sol={plan['entry_price_sol']:.6e} "
                   f"size_sol={plan['size_sol']:g} price_impact_pct={plan['price_impact_pct']:.2f}").render()
    ts = ctx.ts()
    with ctx.db.tx():
        if not ctx.db.transition(lead.lead_id, Status.AWAITING, Status.PAPER_FILLED, ts, human_decision="approve"):
            raise ApprovalError(f"{lead.ref} changed state while approving; nothing filled")
        ctx.db.open_position(lead_id=lead.lead_id, mint=lead.mint, paper_entry=plan["entry_price_sol"],
                             size_sol=plan["size_sol"], token_amount=plan["out_amount"],
                             token_decimals=plan["token_decimals"], entry_liquidity_usd=lead.liquidity_usd,
                             opened_at=ts)
        ctx.log("approval", "approve", lead.lead_id, by=by, line=line)
    ctx.echo(line)
    position = ctx.db.get_position(lead.lead_id)
    assert position is not None
    return position


def reject(ctx: DeskContext, ref: str, by: str = "human") -> None:
    lead = _load(ctx, ref)
    line = Handoff(lead.lead_id, lead.mint, "approval", "reject", f"by={by}").render()
    with ctx.db.tx():
        if not ctx.db.transition(lead.lead_id, Status.AWAITING, Status.REJECTED, ctx.ts(),
                                 human_decision="reject", reject_reason=f"human/rejected: by {by}"):
            raise ApprovalError(f"{lead.ref} changed state while rejecting")
        ctx.log("approval", "reject", lead.lead_id, by=by, line=line)
    ctx.echo(line)
