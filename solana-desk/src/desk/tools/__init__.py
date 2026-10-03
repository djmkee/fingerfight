"""Data tools. Roles call them and store the results on the lead row; LLMs never call them."""

from ..settings import Settings
from .base import Toolbox, ToolError
from .dexscreener import DexScreener
from .http import JsonHttp
from .jupiter import Jupiter
from .solana_rpc import SolanaRpc

__all__ = ["Toolbox", "ToolError", "live_toolbox"]


def live_toolbox(settings: Settings) -> Toolbox:
    jupiter_headers = {"x-api-key": settings.jupiter_api_key} if settings.jupiter_api_key else None
    return Toolbox(
        market=DexScreener(JsonHttp(settings.dexscreener_base_url)),
        chain=SolanaRpc(JsonHttp(settings.rpc_url)),
        quoter=Jupiter(JsonHttp(settings.jupiter_base_url, headers=jupiter_headers)),
    )
