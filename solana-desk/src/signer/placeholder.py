"""Signer placeholder. NOTHING HERE SIGNS OR BROADCASTS: v1 is paper-only.

This module only fixes where live execution would live in a later milestone:

* It runs as its own process, outside the LLM/agent process. Agent code never imports it,
  and no agent prompt or tool can reach it.
* For one lead a human approved, it would rebuild the swap from the stored quote
  (leads.quote_json["jupiter_quote"]) outside the LLM process, re-check that plan against
  config/policy.yaml (same mint, size, slippage, price impact, a fresh quote), sign only
  here, broadcast, then write tx_sig back to the leads row. A failed send would be recorded
  through desk.roles.head.Head.record_failed_send so the failed-send halt can trip.
* Key material never goes into a prompt, a tool the agents can call, the events table,
  logs, environment variables the agent process reads, or this repository. If a signing
  library such as `solders` is ever used, it is imported here and nowhere else.
"""


class LiveTradingDisabled(RuntimeError):
    """Raised for any attempt to sign or send in v1."""


def sign_and_send(lead_id: int) -> str:
    """Would return the transaction signature. In v1 it always refuses."""
    raise LiveTradingDisabled(
        f"LEAD-{lead_id}: live signing and broadcast are not implemented in v1 (paper mode only)"
    )
