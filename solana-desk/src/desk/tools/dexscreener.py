"""DexScreener public API (no key): discovery feeds and pair lookups for Solana."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from .base import PairSnapshot, ToolError
from .http import JsonHttp

CHAIN = "solana"
FEEDS = ("/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1")
BATCH = 30  # /tokens/v1 takes up to 30 addresses per call


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_pair(raw: Any, source: str) -> PairSnapshot | None:
    """Normalize one DexScreener pair object; None if it is not a usable Solana pair."""
    try:
        if raw.get("chainId") != CHAIN:
            return None
        base, quote = raw["baseToken"], raw["quoteToken"]
        created = raw.get("pairCreatedAt")
        return PairSnapshot(
            pair_address=str(raw["pairAddress"]),
            dex_id=str(raw.get("dexId") or ""),
            base_mint=str(base["address"]),
            base_symbol=str(base.get("symbol") or ""),
            quote_mint=str(quote["address"]),
            quote_symbol=str(quote.get("symbol") or ""),
            liquidity_usd=_number((raw.get("liquidity") or {}).get("usd")),
            pair_created_at=(datetime.fromtimestamp(created / 1000, UTC)
                             if isinstance(created, int | float) and created > 0 else None),
            price_usd=_number(raw.get("priceUsd")),
            volume_h24_usd=_number((raw.get("volume") or {}).get("h24")),
            url=raw.get("url"),
            source=source,
        )
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError, OSError):
        return None


class DexScreener:
    def __init__(self, http: JsonHttp) -> None:
        self.http = http

    def discover(self) -> list[PairSnapshot]:
        """Newest profiled and boosted Solana tokens, each with its deepest pair as the base token."""
        feed_of: dict[str, str] = {}
        errors = []
        for path in FEEDS:
            try:
                status, body = self.http.get(path)
            except ToolError as exc:
                errors.append(str(exc))
                continue
            if status != 200:
                errors.append(f"{path}: HTTP {status}")
                continue
            for item in body if isinstance(body, list) else [body]:
                if isinstance(item, dict) and item.get("chainId") == CHAIN and isinstance(item.get("tokenAddress"), str):
                    feed_of.setdefault(item["tokenAddress"], "dexscreener" + path.removesuffix("/v1"))
        if not feed_of:
            if errors:
                raise ToolError("; ".join(errors))
            return []

        mints = list(feed_of)
        snapshots = []
        for start in range(0, len(mints), BATCH):
            chunk = mints[start:start + BATCH]
            status, body = self.http.get(f"/tokens/v1/{CHAIN}/{','.join(chunk)}")
            if status != 200:
                raise ToolError(f"/tokens/v1: HTTP {status}")
            deepest: dict[str, PairSnapshot] = {}
            for raw in body if isinstance(body, list) else []:
                snap = parse_pair(raw, "dexscreener")
                if snap is None or snap.base_mint not in chunk:
                    continue
                current = deepest.get(snap.base_mint)
                if current is None or (snap.liquidity_usd or 0) > (current.liquidity_usd or 0):
                    deepest[snap.base_mint] = snap
            for mint in chunk:
                snap = deepest.get(mint)
                snapshots.append(replace(snap, source=feed_of[mint]) if snap
                                 else PairSnapshot.missing(mint, feed_of[mint]))
        return snapshots

    def pair(self, pair_address: str) -> PairSnapshot:
        status, body = self.http.get(f"/latest/dex/pairs/{CHAIN}/{pair_address}")
        if status != 200 or not isinstance(body, dict):
            raise ToolError(f"pair lookup: HTTP {status}")
        raws = body.get("pairs") or ([body["pair"]] if body.get("pair") else [])
        for raw in raws:
            snap = parse_pair(raw, "dexscreener/latest/dex/pairs")
            if snap is not None and snap.pair_address == pair_address:
                return snap
        raise ToolError(f"pair {pair_address} not found")
