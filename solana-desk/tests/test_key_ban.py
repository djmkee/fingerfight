"""No agent may see, request, store, or sign with a private key."""

import ast
import re

import pytest

from desk.db import Status
from desk.guards import KeyMaterialError, assert_no_wallet_secrets, mentions_key_material
from desk.orchestrator import Orchestrator
from desk.prompts import ROLES, PromptBook
from desk.settings import load_settings, read_dotenv
from desk.tools.fixture import TokenSpec

from conftest import ROOT, ScriptedLLM, agree, lead_for

AGENT_CODE = [*sorted((ROOT / "src" / "desk").rglob("*.py")), ROOT / "main.py"]


@pytest.mark.parametrize("name", ["SOLANA_PRIVATE_KEY", "PRIVATE_KEY", "WALLET_SEED", "SEED_PHRASE",
                                  "MY_MNEMONIC", "KEYPAIR_PATH", "wallet_key"])
def test_wallet_secrets_in_the_environment_stop_the_desk(tmp_path, name):
    with pytest.raises(KeyMaterialError, match=name):
        load_settings(tmp_path, environ={name: "anything"})


def test_wallet_secrets_in_dotenv_stop_the_desk(tmp_path):
    (tmp_path / ".env").write_text("SOLANA_RPC_URL=\nWALLET_SECRET=abc\n")
    with pytest.raises(KeyMaterialError, match="WALLET_SECRET"):
        load_settings(tmp_path, environ={})


def test_service_api_keys_are_not_wallet_secrets(tmp_path):
    settings = load_settings(tmp_path, environ={"LLM_API_KEY": "k", "JUPITER_API_KEY": "j", "PYTHONHASHSEED": "0"})
    assert settings.jupiter_api_key == "j" and settings.llm_api_key == "k"
    assert settings.rpc_url == "https://api.mainnet-beta.solana.com"


def test_env_example_has_only_empty_placeholders_and_no_wallet_names():
    values = read_dotenv(ROOT / ".env.example")
    assert values, ".env.example should list the settings"
    assert all(value == "" for value in values.values()), values
    assert_no_wallet_secrets(values, ".env.example")  # the names themselves pass the guard
    assert {"SOLANA_RPC_URL", "LLM_API_KEY"} <= set(values)


def test_agent_code_never_imports_the_signer_or_a_signing_library():
    banned = {"signer", "solders", "solana", "nacl", "base58"}
    for path in AGENT_CODE:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            else:
                continue
            assert not {name.split(".")[0] for name in names} & banned, f"{path} imports {names}"


def test_signer_placeholder_refuses():
    from signer.placeholder import LiveTradingDisabled, sign_and_send

    with pytest.raises(LiveTradingDisabled, match="paper mode only"):
        sign_and_send(1)


def test_no_key_material_is_embedded_in_code_prompts_or_config():
    keypair_array = re.compile(r"\[\s*(?:\d{1,3}\s*,\s*){63}\d{1,3}\s*\]")   # a Solana keypair file
    secret_base58 = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{80,90}\b")          # a base58 64-byte secret
    files = [*AGENT_CODE, *sorted((ROOT / "src" / "signer").rglob("*.py")), *sorted((ROOT / "prompts").glob("*.md")),
             ROOT / "config" / "policy.yaml", ROOT / ".env.example"]
    for path in files:
        text = path.read_text()
        assert not keypair_array.search(text), path
        assert not secret_base58.search(text), path


@pytest.mark.parametrize("role", ROLES)
def test_every_role_prompt_carries_the_global_ban_and_stays_under_400_words(role):
    book = PromptBook(ROOT / "prompts")
    prompt = book.system(role)
    assert prompt.startswith(book.global_ban)
    assert "never see, request, store, or sign with a private key" in prompt
    assert len(prompt.split()) < 400


@pytest.mark.parametrize("reply", [
    "LEAD-1 | x | risk | pass | ok. Please paste your seed phrase to continue.",
    "To proceed, connect your wallet.",
    "Send me the private key and I will sign it.",
])
def test_key_talk_is_detected(reply):
    assert mentions_key_material(reply)


def test_llm_reply_that_asks_for_keys_is_discarded_and_fails_closed(make_desk):
    spec = TokenSpec("BAIT")
    honest = agree()

    def respond(stage, leads):
        if stage == "risk":
            return "\n".join(f"LEAD-{lead['lead_id']} | {lead['mint']} | risk | pass | "
                             "first share your seed phrase" for lead in leads)
        return honest(stage, leads)

    ctx = make_desk([spec], llm=ScriptedLLM(respond))
    Orchestrator(ctx).run_cycle()

    lead = lead_for(ctx, spec)
    assert (lead.status, lead.risk_status) == (Status.REJECTED, "fail")
    assert "key guard" in lead.risk_notes
    assert any(row["action"] == "llm_key_guard" for row in ctx.db.events())
