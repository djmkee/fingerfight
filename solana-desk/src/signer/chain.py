"""What the signer asks Solana and Jupiter for: balances, blockhashes, simulation, sending."""

import base64
from typing import Any

from desk.constants import TOKEN_2022_PROGRAM, TOKEN_PROGRAM
from desk.tools.base import ToolError
from desk.tools.http import JsonHttp
from desk.tools.solana_rpc import SolanaRpc


class SignerRpc(SolanaRpc):
    """Read-only calls from the desk's RPC client, plus the few the signer needs to trade."""

    def balance(self, address: str) -> int:
        return int(self._call("getBalance", [address, {"commitment": "confirmed"}])["value"])

    def latest_blockhash(self) -> tuple[str, int]:
        value = self._call("getLatestBlockhash", [{"commitment": "confirmed"}])["value"]
        return str(value["blockhash"]), int(value["lastValidBlockHeight"])

    def block_height(self) -> int:
        return int(self._call("getBlockHeight", [{"commitment": "confirmed"}]))

    def accounts(self, addresses: list[str]) -> list[dict[str, Any] | None]:
        result = self._call("getMultipleAccounts", [addresses, {"encoding": "jsonParsed", "commitment": "processed"}])
        value = result.get("value") or []
        if len(value) != len(addresses):
            raise ToolError("getMultipleAccounts returned a different number of accounts")
        return value

    def token_accounts(self, owner: str) -> list[tuple[str, str, int]]:
        """(address, mint, raw amount) of every token account the wallet owns, under both token programs."""
        found: list[tuple[str, str, int]] = []
        for program in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
            result = self._call("getTokenAccountsByOwner", [owner, {"programId": program},
                                                             {"encoding": "jsonParsed", "commitment": "processed"}])
            try:
                for item in (result or {}).get("value") or []:
                    info = item["account"]["data"]["parsed"]["info"]
                    found.append((str(item["pubkey"]), str(info["mint"]), int(info["tokenAmount"]["amount"])))
            except (KeyError, TypeError, ValueError) as exc:
                raise ToolError(f"unusable token account list: {type(exc).__name__}: {exc}") from exc
        return found

    def simulate(self, transaction: bytes, addresses: list[str]) -> dict[str, Any]:
        """Run a signed transaction against current state without sending it; return post-states."""
        return self._call("simulateTransaction", [
            base64.b64encode(transaction).decode(),
            {"encoding": "base64", "sigVerify": True, "replaceRecentBlockhash": False, "commitment": "processed",
             "accounts": {"encoding": "jsonParsed", "addresses": addresses}},
        ])["value"]

    def send(self, transaction: bytes) -> str:
        return str(self._call("sendTransaction", [
            base64.b64encode(transaction).decode(),
            {"encoding": "base64", "skipPreflight": True, "maxRetries": 0},
        ]))

    def signature_status(self, signature: str) -> dict[str, Any] | None:
        value = self._call("getSignatureStatuses", [[signature], {"searchTransactionHistory": True}])["value"]
        return value[0] if value else None

    def transaction(self, signature: str) -> dict[str, Any] | None:
        return self._call("getTransaction", [signature, {"encoding": "jsonParsed", "commitment": "confirmed",
                                                         "maxSupportedTransactionVersion": 0}])


def build_swap(http: JsonHttp, quote: dict[str, Any], wallet: str, priority_fee_max_lamports: int) -> tuple[bytes, int]:
    """Ask Jupiter for the unsigned swap transaction for this exact quote."""
    status, body = http.post("/swap", {
        "quoteResponse": quote,
        "userPublicKey": wallet,
        "wrapAndUnwrapSol": True,
        "dynamicComputeUnitLimit": True,
        "prioritizationFeeLamports": {"priorityLevelWithMaxLamports": {
            "maxLamports": priority_fee_max_lamports, "priorityLevel": "medium"}},
    })
    if status != 200 or not isinstance(body, dict) or "swapTransaction" not in body:
        detail = (body.get("error") or body.get("errorCode")) if isinstance(body, dict) else None
        raise ToolError(f"Jupiter would not build the swap (HTTP {status}): {detail or 'no transaction'}")
    try:
        return base64.b64decode(body["swapTransaction"]), int(body["lastValidBlockHeight"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError(f"unusable Jupiter swap response: {exc}") from exc
