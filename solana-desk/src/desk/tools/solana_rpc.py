"""Solana JSON-RPC (Helius-compatible), read-only: mint and freeze authority, supply, top holders."""

from typing import Any

from ..constants import TOKEN_2022_PROGRAM, TOKEN_PROGRAM
from ..guards import check_mint
from .base import HolderInfo, MintInfo, ToolError
from .http import JsonHttp

TOKEN_PROGRAMS = {TOKEN_PROGRAM: "spl-token", TOKEN_2022_PROGRAM: "spl-token-2022"}


class SolanaRpc:
    def __init__(self, http: JsonHttp) -> None:
        self.http = http

    def _call(self, method: str, params: list[Any]) -> Any:
        status, body = self.http.post("", {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if not isinstance(body, dict):
            raise ToolError(f"rpc {method}: unexpected response (HTTP {status})")
        if body.get("error"):
            error = body["error"]
            raise ToolError(f"rpc {method}: {error.get('message', error) if isinstance(error, dict) else error}")
        if status != 200 or "result" not in body:
            raise ToolError(f"rpc {method}: HTTP {status}")
        return body["result"]

    def mint_info(self, mint: str) -> MintInfo:
        result = self._call("getAccountInfo", [mint, {"encoding": "jsonParsed", "commitment": "confirmed"}])
        value = (result or {}).get("value")
        if value is None:
            raise ToolError("mint account not found")
        program = TOKEN_PROGRAMS.get(value.get("owner"))
        if program is None:
            raise ToolError(f"account is not owned by a token program (owner {value.get('owner')})")
        parsed = (value.get("data") or {}).get("parsed") if isinstance(value.get("data"), dict) else None
        if not isinstance(parsed, dict) or parsed.get("type") != "mint":
            raise ToolError("account is not a token mint")
        info = parsed.get("info") or {}
        try:
            return MintInfo(
                mint=mint,
                token_program=program,
                # Indexed, not .get(): absent authority fields must fail closed, never read as renounced.
                mint_authority=info["mintAuthority"],
                freeze_authority=info["freezeAuthority"],
                supply=int(info["supply"]),
                decimals=int(info["decimals"]),
                is_initialized=info.get("isInitialized") is True,
                extensions=tuple(sorted(str(ext.get("extension")) for ext in info.get("extensions") or [])),
                slot=(result.get("context") or {}).get("slot"),
                source="rpc/getAccountInfo",
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ToolError(f"unusable mint data: {type(exc).__name__}: {exc}") from exc

    def holders(self, mint: str, supply: int, exclude_owners: frozenset[str]) -> HolderInfo:
        """Top-10 share of supply among the 20 largest token accounts, skipping AMM vaults."""
        if supply <= 0:
            raise ToolError("supply is zero")
        largest = (self._call("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}]) or {}).get("value") or []
        if not largest:
            raise ToolError("no token accounts returned")
        addresses = [account["address"] for account in largest]
        accounts = (self._call("getMultipleAccounts", [addresses, {"encoding": "jsonParsed", "commitment": "confirmed"}])
                    or {}).get("value") or []
        if len(accounts) != len(largest):
            raise ToolError("token account lookup returned a different number of accounts")
        rows: list[tuple[int, str, str | None]] = []
        try:
            for account, detail in zip(largest, accounts, strict=True):
                data = (detail or {}).get("data")
                info = ((data.get("parsed") or {}).get("info") or {}) if isinstance(data, dict) else {}
                if "mint" in info:
                    check_mint(mint, info["mint"], "rpc token account mint")
                rows.append((int(account["amount"]), account["address"], info.get("owner")))
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolError(f"unusable holder data: {type(exc).__name__}: {exc}") from exc
        rows.sort(reverse=True)
        kept = [row for row in rows if row[2] not in exclude_owners]
        return HolderInfo(
            top10_pct=sum(amount for amount, _, _ in kept[:10]) / supply * 100,
            raw_top10_pct=sum(amount for amount, _, _ in rows[:10]) / supply * 100,
            excluded=tuple(address for _, address, owner in rows if owner in exclude_owners),
            accounts_seen=len(rows),
            source="rpc/getTokenLargestAccounts+getMultipleAccounts",
        )
