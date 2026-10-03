"""Environment settings. Only allow-listed names are read, and wallet secrets stop the desk."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .guards import assert_no_wallet_secrets

DEFAULTS = {
    "SOLANA_RPC_URL": "https://api.mainnet-beta.solana.com",
    "JUPITER_BASE_URL": "https://lite-api.jup.ag/swap/v1",
    "DEXSCREENER_BASE_URL": "https://api.dexscreener.com",
    "DESK_DB_PATH": "data/desk.sqlite3",
    "DESK_POLICY_PATH": "config/policy.yaml",
}


@dataclass(frozen=True)
class Settings:
    root: Path
    rpc_url: str
    jupiter_base_url: str
    jupiter_api_key: str | None
    dexscreener_base_url: str
    llm_base_url: str | None
    llm_api_key: str | None
    llm_model: str | None
    db_path: Path
    policy_path: Path

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)


def read_dotenv(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines. Empty values count as unset."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        values[name.strip().removeprefix("export ").strip()] = value.strip().strip("'\"")
    return values


def load_settings(root: Path, environ: Mapping[str, str] | None = None) -> Settings:
    environ = os.environ if environ is None else environ
    dotenv = read_dotenv(root / ".env")
    assert_no_wallet_secrets(environ, "the environment")
    assert_no_wallet_secrets(dotenv, ".env")

    def get(name: str) -> str | None:
        return environ.get(name) or dotenv.get(name) or DEFAULTS.get(name) or None

    def path(name: str) -> Path:
        value = Path(get(name) or "")
        return value if value.is_absolute() else root / value

    return Settings(
        root=root,
        rpc_url=get("SOLANA_RPC_URL") or "",
        jupiter_base_url=get("JUPITER_BASE_URL") or "",
        jupiter_api_key=get("JUPITER_API_KEY"),
        dexscreener_base_url=get("DEXSCREENER_BASE_URL") or "",
        llm_base_url=get("LLM_BASE_URL"),
        llm_api_key=get("LLM_API_KEY"),
        llm_model=get("LLM_MODEL"),
        db_path=path("DESK_DB_PATH"),
        policy_path=path("DESK_POLICY_PATH"),
    )
