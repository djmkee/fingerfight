"""Offline stand-ins for DexScreener, Solana RPC, and Jupiter, used by the tests and `--demo`.

Everything here is SYNTHETIC. Every result carries a `fixture/...` source tag, and that tag is
what lands on the lead row, so demo rows can never pass for market data.
"""

import hashlib
import json
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from pathlib import Path

from ..b58 import b58encode
from ..constants import LAMPORTS_PER_SOL, SOL_MINT
from .base import HolderInfo, MintInfo, PairSnapshot, ToolError, Toolbox
from .jupiter import parse_quote


def demo_address(label: str) -> str:
    """A valid-looking, obviously derived address: base58(sha256(label))."""
    return b58encode(hashlib.sha256(label.encode()).digest())


@dataclass
class TokenSpec:
    """One synthetic token. The defaults describe a token that passes every check."""

    symbol: str
    liquidity_usd: float | None = 50_000.0
    age_minutes: float | None = 30.0
    listed_as_base: bool = True
    dex_id: str = "raydium"
    mint_authority: bool = False
    freeze_authority: bool = False
    token_program: str = "spl-token"
    extensions: tuple[str, ...] = ()
    decimals: int = 6
    supply: int = 1_000_000_000 * 10**6
    top10_pct: float | None = 18.0       # None: the holder lookup fails
    tokens_per_sol: float = 1_000_000.0  # mid price used by the fixture quotes
    buy_impact_pct: float = 0.8
    sell_impact_pct: float = 0.9
    sell_route: bool = True
    tradable: bool = True                # False: no route either way
    volume_h1_usd: float | None = 12_000.0
    price_change_h1_pct: float | None = 4.0
    price_change_h24_pct: float | None = 35.0
    buys_h1: int | None = 140
    sells_h1: int | None = 110

    @property
    def mint(self) -> str:
        return demo_address(f"demo-mint:{self.symbol}")

    @property
    def pair(self) -> str:
        return demo_address(f"demo-pair:{self.symbol}")


class FixtureBook:
    """The specs all three fixture tools share, so a test can change the market mid-run."""

    def __init__(self, specs: list[TokenSpec], listed_at: datetime) -> None:
        self.specs = {spec.mint: spec for spec in specs}
        self.listed_at = listed_at

    def add(self, spec: TokenSpec) -> None:
        self.specs[spec.mint] = spec

    def get(self, mint: str) -> TokenSpec:
        spec = self.specs.get(mint)
        if spec is None:
            raise ToolError(f"unknown fixture mint {mint}")
        return spec


class FixtureMarket:
    source = "fixture/dexscreener"

    def __init__(self, book: FixtureBook) -> None:
        self.book = book
        self.discover_calls = 0
        self.pair_calls: list[str] = []
        self.base_mint_override: dict[str, str] = {}  # pair address -> mint to report instead
        self.down = False

    def _snapshot(self, spec: TokenSpec) -> PairSnapshot:
        created = None if spec.age_minutes is None else self.book.listed_at - timedelta(minutes=spec.age_minutes)
        return PairSnapshot(
            pair_address=spec.pair, dex_id=spec.dex_id,
            base_mint=self.base_mint_override.get(spec.pair, spec.mint), base_symbol=spec.symbol,
            quote_mint=SOL_MINT, quote_symbol="SOL", liquidity_usd=spec.liquidity_usd,
            pair_created_at=created, price_usd=None, volume_h24_usd=None, url=None, source=self.source,
            volume_h1_usd=spec.volume_h1_usd, price_change_h1_pct=spec.price_change_h1_pct,
            price_change_h24_pct=spec.price_change_h24_pct, buys_h1=spec.buys_h1, sells_h1=spec.sells_h1,
            fdv_usd=None if spec.liquidity_usd is None else spec.liquidity_usd * 8,
        )

    def discover(self) -> list[PairSnapshot]:
        self.discover_calls += 1
        if self.down:
            raise ToolError("fixture market is down")
        return [self._snapshot(spec) if spec.listed_as_base else PairSnapshot.missing(spec.mint, self.source)
                for spec in self.book.specs.values()]

    def pair(self, pair_address: str) -> PairSnapshot:
        self.pair_calls.append(pair_address)
        if self.down:
            raise ToolError("fixture market is down")
        for spec in self.book.specs.values():
            if spec.pair == pair_address:
                return self._snapshot(spec)
        raise ToolError(f"pair {pair_address} not found")


class FixtureChain:
    source = "fixture/rpc"

    def __init__(self, book: FixtureBook) -> None:
        self.book = book
        self.calls: list[tuple[str, str]] = []
        self.down = False

    def mint_info(self, mint: str) -> MintInfo:
        self.calls.append(("mint_info", mint))
        if self.down:
            raise ToolError("fixture rpc is down")
        spec = self.book.get(mint)
        return MintInfo(
            mint=mint,
            token_program=spec.token_program,
            mint_authority=demo_address(f"demo-mint-authority:{spec.symbol}") if spec.mint_authority else None,
            freeze_authority=demo_address(f"demo-freeze-authority:{spec.symbol}") if spec.freeze_authority else None,
            supply=spec.supply,
            decimals=spec.decimals,
            is_initialized=True,
            extensions=tuple(sorted(spec.extensions)),
            slot=None,
            source=self.source,
        )

    def holders(self, mint: str, supply: int, exclude_owners: frozenset[str]) -> HolderInfo:
        self.calls.append(("holders", mint))
        spec = self.book.get(mint)
        if spec.top10_pct is None or self.down:
            raise ToolError("getTokenLargestAccounts unavailable (fixture)")
        return HolderInfo(spec.top10_pct, spec.top10_pct, (), 20, self.source)


class FixtureQuoter:
    source = "fixture/jupiter"

    def __init__(self, book: FixtureBook) -> None:
        self.book = book
        self.calls: list[tuple[str, str, int]] = []
        self.output_mint_override: dict[str, str] = {}  # token mint -> mint a buy quote reports instead

    def quote(self, input_mint: str, output_mint: str, amount: int, slippage_bps: int):
        self.calls.append((input_mint, output_mint, amount))
        if input_mint == SOL_MINT:
            spec = self.book.get(output_mint)
            impact = spec.buy_impact_pct
            out = int(amount / LAMPORTS_PER_SOL * spec.tokens_per_sol * 10**spec.decimals * (1 - impact / 100))
            reported_output = self.output_mint_override.get(output_mint, output_mint)
        elif output_mint == SOL_MINT:
            spec = self.book.get(input_mint)
            if not spec.sell_route:
                raise ToolError("COULD_NOT_FIND_ANY_ROUTE (fixture)")
            impact = spec.sell_impact_pct
            out = int(amount / 10**spec.decimals / spec.tokens_per_sol * LAMPORTS_PER_SOL * (1 - impact / 100))
            reported_output = output_mint
        else:
            raise ToolError("TOKEN_NOT_TRADABLE (fixture)")
        if not spec.tradable:
            raise ToolError("TOKEN_NOT_TRADABLE (fixture)")
        return parse_quote({
            "inputMint": input_mint, "outputMint": reported_output, "inAmount": str(amount),
            "outAmount": str(out), "otherAmountThreshold": str(out * (10_000 - slippage_bps) // 10_000),
            "priceImpactPct": str(impact / 100), "slippageBps": slippage_bps,
            "routePlan": [{"swapInfo": {"label": f"{spec.dex_id} (fixture)"}, "percent": 100}],
            "contextSlot": None,
        }, source=self.source)


def fixture_toolbox(specs: list[TokenSpec], listed_at: datetime) -> Toolbox:
    book = FixtureBook(specs, listed_at)
    return Toolbox(market=FixtureMarket(book), chain=FixtureChain(book), quoter=FixtureQuoter(book))


def load_specs(path: Path) -> list[TokenSpec]:
    known = {field.name for field in fields(TokenSpec)}
    specs = []
    for raw in json.loads(path.read_text())["tokens"]:
        values = {key: value for key, value in raw.items() if key in known}
        if "extensions" in values:
            values["extensions"] = tuple(values["extensions"])
        specs.append(TokenSpec(**values))
    return specs
