"""Tool result types shared by the live clients and the offline fixtures."""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol


class ToolError(RuntimeError):
    """A data source failed or returned something unusable. Callers fail closed."""


@dataclass(frozen=True)
class PairSnapshot:
    """One DexScreener pair where the candidate token is the base token."""

    pair_address: str
    dex_id: str
    base_mint: str
    base_symbol: str
    quote_mint: str
    quote_symbol: str
    liquidity_usd: float | None
    pair_created_at: datetime | None
    price_usd: float | None
    volume_h24_usd: float | None
    url: str | None
    source: str

    @classmethod
    def missing(cls, mint: str, source: str) -> "PairSnapshot":
        """A discovered token with no Solana pair where it is the base token."""
        return cls("", "", mint, "", "", "", None, None, None, None, None, source)

    def age_minutes(self, now: datetime) -> float | None:
        if self.pair_created_at is None:
            return None
        return (now - self.pair_created_at).total_seconds() / 60

    def evidence(self) -> dict[str, Any]:
        record = asdict(self)
        record["pair_created_at"] = self.pair_created_at.isoformat() if self.pair_created_at else None
        return record


@dataclass(frozen=True)
class MintInfo:
    mint: str
    token_program: str  # "spl-token" or "spl-token-2022"
    mint_authority: str | None
    freeze_authority: str | None
    supply: int
    decimals: int
    is_initialized: bool
    extensions: tuple[str, ...]
    slot: int | None
    source: str

    def evidence(self) -> dict[str, Any]:
        record = asdict(self)
        record["supply"] = str(self.supply)
        record["extensions"] = list(self.extensions)
        return record


@dataclass(frozen=True)
class HolderInfo:
    top10_pct: float       # share of supply in the 10 largest accounts, pool vaults excluded
    raw_top10_pct: float   # the same without excluding anything
    excluded: tuple[str, ...]
    accounts_seen: int
    source: str

    def evidence(self) -> dict[str, Any]:
        record = asdict(self)
        record["excluded"] = list(self.excluded)
        return record


@dataclass(frozen=True)
class Quote:
    input_mint: str
    output_mint: str
    in_amount: int
    out_amount: int
    min_out_amount: int
    price_impact_pct: float  # percent: 1.5 means 1.5%
    slippage_bps: int
    route: tuple[str, ...]
    context_slot: int | None
    raw: dict[str, Any]
    source: str


class MarketData(Protocol):
    def discover(self) -> list[PairSnapshot]: ...

    def pair(self, pair_address: str) -> PairSnapshot: ...


class ChainData(Protocol):
    def mint_info(self, mint: str) -> MintInfo: ...

    def holders(self, mint: str, supply: int, exclude_owners: frozenset[str]) -> HolderInfo: ...


class Quoter(Protocol):
    def quote(self, input_mint: str, output_mint: str, amount: int, slippage_bps: int) -> Quote: ...


@dataclass
class Toolbox:
    market: MarketData
    chain: ChainData
    quoter: Quoter
