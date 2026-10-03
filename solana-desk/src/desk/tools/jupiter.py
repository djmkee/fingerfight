"""Jupiter swap API, quote endpoint only: executable out amount, price impact, and route.

The desk never calls the swap-building endpoints; a future signer rebuilds the swap from the
stored quote outside the agent process.
"""

import math
from typing import Any

from .base import Quote, ToolError
from .http import JsonHttp


def parse_quote(body: dict[str, Any], source: str) -> Quote:
    try:
        # Jupiter reports priceImpactPct as a fraction (0.01 = 1%). Were a version to report
        # percent instead, this reading would overstate impact, so the error rejects more.
        impact = abs(float(body["priceImpactPct"])) * 100
        if not math.isfinite(impact):
            raise ValueError("priceImpactPct is not finite")
        return Quote(
            input_mint=str(body["inputMint"]),
            output_mint=str(body["outputMint"]),
            in_amount=int(body["inAmount"]),
            out_amount=int(body["outAmount"]),
            min_out_amount=int(body.get("otherAmountThreshold") or 0),
            price_impact_pct=impact,
            slippage_bps=int(body.get("slippageBps") or 0),
            route=tuple(str((step.get("swapInfo") or {}).get("label") or "?")
                        for step in body.get("routePlan") or []),
            context_slot=body.get("contextSlot"),
            raw=body,
            source=source,
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ToolError(f"unusable Jupiter quote: {type(exc).__name__}: {exc}") from exc


class Jupiter:
    def __init__(self, http: JsonHttp) -> None:
        self.http = http

    def quote(self, input_mint: str, output_mint: str, amount: int, slippage_bps: int) -> Quote:
        status, body = self.http.get("/quote", params={
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": str(slippage_bps),
            "swapMode": "ExactIn",
            "restrictIntermediateTokens": "true",
        })
        if status != 200 or not isinstance(body, dict) or "outAmount" not in body:
            detail = (body.get("errorCode") or body.get("error")) if isinstance(body, dict) else None
            raise ToolError(f"Jupiter quote refused (HTTP {status}): {detail or 'no route'}")
        return parse_quote(body, source="jupiter/quote")
