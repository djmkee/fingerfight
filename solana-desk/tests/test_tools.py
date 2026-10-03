"""The live clients against recorded-shape responses (httpx.MockTransport, no network)."""

import json

import httpx
import pytest

from desk.constants import SOL_MINT, TOKEN_2022_PROGRAM, TOKEN_PROGRAM
from desk.guards import MintMismatch
from desk.tools.base import ToolError
from desk.tools.dexscreener import DexScreener
from desk.tools.fixture import demo_address
from desk.tools.http import JsonHttp
from desk.tools.jupiter import Jupiter
from desk.tools.solana_rpc import SolanaRpc

MINT_A, MINT_B, MINT_C = demo_address("a"), demo_address("b"), demo_address("c")
POOL_AUTHORITY = demo_address("pool-authority")


def http(handler, base="https://example.test") -> JsonHttp:
    return JsonHttp(base, transport=httpx.MockTransport(handler), sleep=lambda _seconds: None)


def pair(base: str, quote: str, address: str, liquidity: float | None, created_ms: int | None = 1_759_400_000_000):
    return {"chainId": "solana", "dexId": "raydium", "pairAddress": address,
            "baseToken": {"address": base, "symbol": "BASE"}, "quoteToken": {"address": quote, "symbol": "SOL"},
            "priceUsd": "0.0012", "liquidity": {"usd": liquidity}, "volume": {"h24": 1000},
            "pairCreatedAt": created_ms}


def test_dexscreener_discovery_keeps_solana_and_the_deepest_base_pair():
    seen_paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen_paths.append(path)
        if path == "/token-profiles/latest/v1":
            return httpx.Response(200, json=[{"chainId": "solana", "tokenAddress": MINT_A},
                                             {"chainId": "ethereum", "tokenAddress": "0xabc"}])
        if path == "/token-boosts/latest/v1":
            return httpx.Response(200, json=[{"chainId": "solana", "tokenAddress": MINT_B}])
        if path == "/token-boosts/top/v1":
            return httpx.Response(500, json={})
        assert path.startswith("/tokens/v1/solana/")
        return httpx.Response(200, json=[
            pair(MINT_A, SOL_MINT, "pairA1", 20_000), pair(MINT_A, SOL_MINT, "pairA2", 90_000),
            pair(SOL_MINT, MINT_B, "pairB", 70_000),  # B only ever appears as the quote token
        ])

    snaps = DexScreener(http(handler)).discover()

    assert [snap.base_mint for snap in snaps] == [MINT_A, MINT_B]
    assert (snaps[0].pair_address, snaps[0].liquidity_usd) == ("pairA2", 90_000)
    assert snaps[0].source == "dexscreener/token-profiles/latest"
    assert snaps[0].pair_created_at is not None
    assert snaps[1].pair_address == "" and snaps[1].liquidity_usd is None
    assert sum(path.startswith("/tokens/v1/") for path in seen_paths) == 1


def test_dexscreener_total_outage_is_a_tool_error():
    with pytest.raises(ToolError):
        DexScreener(http(lambda request: httpx.Response(503, json={}))).discover()


def test_dexscreener_pair_lookup():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/latest/dex/pairs/solana/pairA"
        return httpx.Response(200, json={"schemaVersion": "1.0.0", "pairs": [pair(MINT_A, SOL_MINT, "pairA", 33_000)]})

    snap = DexScreener(http(handler)).pair("pairA")
    assert (snap.base_mint, snap.liquidity_usd) == (MINT_A, 33_000)


def quote_body(**changes):
    return {"inputMint": SOL_MINT, "outputMint": MINT_A, "inAmount": "300000000", "outAmount": "123456789",
            "otherAmountThreshold": "122222222", "swapMode": "ExactIn", "slippageBps": 100,
            "priceImpactPct": "0.0125", "routePlan": [{"swapInfo": {"label": "Raydium"}, "percent": 100}],
            "contextSlot": 299283763} | changes


def test_jupiter_quote_parses_and_converts_impact_to_percent():
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        assert request.url.path == "/swap/v1/quote"
        assert (params["inputMint"], params["outputMint"], params["amount"]) == (SOL_MINT, MINT_A, "300000000")
        assert params["slippageBps"] == "100" and params["restrictIntermediateTokens"] == "true"
        return httpx.Response(200, json=quote_body())

    quote = Jupiter(http(handler, "https://example.test/swap/v1")).quote(SOL_MINT, MINT_A, 300_000_000, 100)
    assert quote.price_impact_pct == pytest.approx(1.25)
    assert (quote.out_amount, quote.min_out_amount, quote.route) == (123456789, 122222222, ("Raydium",))


@pytest.mark.parametrize("response", [
    httpx.Response(400, json={"error": "Could not find any route", "errorCode": "COULD_NOT_FIND_ANY_ROUTE"}),
    httpx.Response(200, json=quote_body(priceImpactPct=None)),     # no impact: never read as zero
    httpx.Response(200, json=quote_body(priceImpactPct="NaN")),
    httpx.Response(200, json={"unexpected": True}),
])
def test_jupiter_failures_fail_closed(response):
    with pytest.raises(ToolError):
        Jupiter(http(lambda request: response)).quote(SOL_MINT, MINT_A, 1, 100)


def rpc(results: dict):
    """An RPC endpoint answering by method name; values are `result` objects or `error` dicts."""
    def handler(request: httpx.Request) -> httpx.Response:
        call = json.loads(request.content)
        answer = results[call["method"]]
        key = "error" if isinstance(answer, dict) and "code" in answer else "result"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": call["id"], key: answer})
    return SolanaRpc(http(handler, "https://rpc.example.test/?api-key=SECRET"))


def mint_account(owner: str, info: dict) -> dict:
    return {"context": {"slot": 341197053}, "value": {
        "owner": owner, "lamports": 1, "executable": False,
        "data": {"program": "spl-token", "space": 82, "parsed": {"type": "mint", "info": info}}}}


def test_rpc_reads_authorities_supply_and_token2022_extensions():
    client = rpc({"getAccountInfo": mint_account(TOKEN_2022_PROGRAM, {
        "decimals": 6, "supply": "1000000000000000", "isInitialized": True,
        "mintAuthority": None, "freezeAuthority": MINT_C,
        "extensions": [{"extension": "transferHook", "state": {}}, {"extension": "metadataPointer", "state": {}}],
    })})
    info = client.mint_info(MINT_A)
    assert info.token_program == "spl-token-2022"
    assert (info.mint_authority, info.freeze_authority) == (None, MINT_C)
    assert (info.supply, info.decimals, info.is_initialized) == (10**15, 6, True)
    assert info.extensions == ("metadataPointer", "transferHook")
    assert info.slot == 341197053


@pytest.mark.parametrize("result", [
    {"context": {"slot": 1}, "value": None},                                   # no such account
    mint_account("11111111111111111111111111111111", {"decimals": 6}),        # not a token program
    mint_account(TOKEN_PROGRAM, {"decimals": 6, "supply": "1", "isInitialized": True,
                                 "freezeAuthority": None}),                   # mintAuthority absent
    {"code": -32602, "message": "Invalid param: WrongSize"},                   # RPC error
])
def test_rpc_unusable_mint_data_fails_closed(result):
    with pytest.raises(ToolError) as caught:
        rpc({"getAccountInfo": result}).mint_info(MINT_A)
    assert "SECRET" not in str(caught.value)


def token_account(mint: str, owner: str, amount: str) -> dict:
    return {"data": {"program": "spl-token", "parsed": {"type": "account", "info": {
        "mint": mint, "owner": owner, "tokenAmount": {"amount": amount, "decimals": 0}}}}}


def test_rpc_top10_excludes_pool_vault_owners():
    largest = [{"address": f"acct{i}", "amount": str(amount), "decimals": 0}
               for i, amount in enumerate([400, 100, 50, 40, 30, 20, 10, 10, 10, 10, 10, 5])]
    owners = [POOL_AUTHORITY] + [demo_address(f"holder{i}") for i in range(1, 12)]
    client = rpc({
        "getTokenLargestAccounts": {"context": {"slot": 1}, "value": largest},
        "getMultipleAccounts": {"context": {"slot": 1}, "value": [
            token_account(MINT_A, owner, row["amount"]) for owner, row in zip(owners, largest, strict=True)]},
    })
    holders = client.holders(MINT_A, supply=1000, exclude_owners=frozenset({POOL_AUTHORITY}))
    assert holders.raw_top10_pct == pytest.approx(68.0)   # 400+100+50+40+30+20+10+10+10+10
    assert holders.top10_pct == pytest.approx(29.0)       # vault skipped: 100+50+40+30+20+10+10+10+10+10
    assert holders.excluded == ("acct0",)


def test_rpc_holder_account_for_another_mint_is_a_mismatch():
    client = rpc({
        "getTokenLargestAccounts": {"value": [{"address": "acct0", "amount": "5", "decimals": 0}]},
        "getMultipleAccounts": {"value": [token_account(MINT_B, demo_address("h"), "5")]},
    })
    with pytest.raises(MintMismatch):
        client.holders(MINT_A, supply=100, exclude_owners=frozenset())


def test_transport_errors_retry_then_fail_closed_without_leaking_the_url_query():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        raise httpx.ConnectError("connection refused", request=request)

    client = SolanaRpc(http(handler, "https://rpc.example.test/?api-key=SECRET"))
    with pytest.raises(ToolError) as caught:
        client.mint_info(MINT_A)
    assert len(attempts) == 3
    assert "SECRET" not in str(caught.value) and "rpc.example.test" in str(caught.value)
