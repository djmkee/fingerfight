"""Signer commands: create or import the trading wallet, show it, execute orders, withdraw."""

import argparse
import getpass
import sys
from pathlib import Path

NEEDS_SOLDERS = 'the signer needs the solders package; install it with: pip install -e ".[live]"'


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    root = root or Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(prog="signer.py",
                                     description="Trading wallet and order execution: the only process that loads the key.")
    parser.add_argument("--root", type=Path, default=root, help=argparse.SUPPRESS)
    parser.add_argument("--config", type=Path, help="signer settings (default: config/signer.yaml)")
    parser.add_argument("--db", type=Path, help="desk database (default: DESK_DB_PATH, else data/desk.sqlite3)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="create a new trading wallet and print the address to fund")
    commands.add_parser("import", help="use a key exported from a wallet app (typed in hidden)")
    commands.add_parser("status", help="wallet address, SOL balance, and trading mode")
    run = commands.add_parser("run", help="execute the desk's orders until stopped")
    run.add_argument("--supervised", action="store_true", help=argparse.SUPPRESS)  # started by the dashboard
    withdraw = commands.add_parser("withdraw", help="send SOL from the trading wallet to another address")
    withdraw.add_argument("--to", required=True, help="destination address")
    amount = withdraw.add_mutually_exclusive_group(required=True)
    amount.add_argument("--sol", type=float, help="how much SOL to send")
    amount.add_argument("--all", action="store_true", help="send everything except the network fee")
    withdraw.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args(argv)

    try:
        import solders  # noqa: F401
    except ImportError:
        print(NEEDS_SOLDERS, file=sys.stderr)
        return 2
    from desk.guards import KeyMaterialError
    from desk.policy import PolicyError
    from desk.tools.base import ToolError

    from .config import SignerConfigError
    from .executor import SendFailed, StillPending
    from .lock import SignerBusy
    from .verify import Refused
    from .wallet import WalletError

    handlers = {"init": _init, "import": _import, "status": _status, "run": _run, "withdraw": _withdraw}
    try:
        return handlers[args.command](args)
    except (SignerConfigError, WalletError, PolicyError, KeyMaterialError, SignerBusy) as exc:
        print(f"[signer] {exc}", file=sys.stderr)
        return 2
    except (Refused, SendFailed, StillPending, ToolError) as exc:
        print(f"[signer] {exc}", file=sys.stderr)
        return 1


def _config(args: argparse.Namespace):
    from .config import load_signer_config

    return load_signer_config(args.config or args.root / "config" / "signer.yaml")


def _init(args: argparse.Namespace) -> int:
    from .wallet import create_wallet

    config = _config(args)
    keypair = create_wallet(config.keypair_path)
    print(f"[signer] created the trading wallet in {config.keypair_path}")
    print(f"[signer] address: {keypair.pubkey()}")
    print("[signer] send SOL to this address from your main wallet, only what you can afford to lose.")
    print("[signer] the key never leaves that file; do not paste it anywhere.")
    return 0


def _import(args: argparse.Namespace) -> int:
    from .wallet import import_wallet

    config = _config(args)
    print("[signer] use a wallet that holds only trading money. The input is hidden.")
    keypair = import_wallet(config.keypair_path, getpass.getpass("[signer] private key: "))
    print(f"[signer] saved to {config.keypair_path}; address: {keypair.pubkey()}")
    return 0


def _executor(args: argparse.Namespace, *, echo=print):
    from desk.db import DeskDB
    from desk.settings import load_settings
    from desk.tools.http import JsonHttp
    from desk.tools.jupiter import Jupiter

    from .chain import SignerRpc
    from .executor import Executor
    from .wallet import load_wallet

    settings = load_settings(args.root)
    config = _config(args)
    keypair = load_wallet(config.keypair_path)
    headers = {"x-api-key": settings.jupiter_api_key} if settings.jupiter_api_key else None
    jupiter_http = JsonHttp(settings.jupiter_base_url, headers=headers)
    return Executor(config=config, keypair=keypair, rpc=SignerRpc(JsonHttp(settings.rpc_url)),
                    jupiter=Jupiter(jupiter_http), swap_http=jupiter_http,
                    db=DeskDB(args.db or settings.db_path), policy_path=settings.policy_path, echo=echo)


def _status(args: argparse.Namespace) -> int:
    from desk.constants import LAMPORTS_PER_SOL

    executor = _executor(args)
    print(f"[signer] address: {executor.wallet}")
    print(f"[signer] balance: {executor.rpc.balance(str(executor.wallet)) / LAMPORTS_PER_SOL:.6f} SOL")
    print(f"[signer] trading_mode: {executor.policy().trading_mode}")
    return 0


def _run(args: argparse.Namespace) -> int:
    from .lock import single_signer

    executor = _executor(args)
    with single_signer(executor.db.path):
        try:
            executor.run(supervised=args.supervised)
        except KeyboardInterrupt:
            print("\n[signer] stopping")
    return 0


def _withdraw(args: argparse.Namespace) -> int:
    from desk.b58 import is_address
    from desk.constants import LAMPORTS_PER_SOL

    if not is_address(args.to):
        print(f"[signer] {args.to!r} is not a Solana address", file=sys.stderr)
        return 2
    executor = _executor(args)
    lamports = None if args.all else round(args.sol * LAMPORTS_PER_SOL)
    what = "everything except the fee" if lamports is None else f"{args.sol:g} SOL"
    if not args.yes:
        typed = input(f"[signer] send {what} to {args.to}? Type the first 4 characters of the address to confirm: ")
        if typed.strip() != args.to[:4]:
            print("[signer] not confirmed; nothing sent")
            return 1
    signature = executor.withdraw(args.to, lamports)
    print(f"[signer] sent, transaction {signature}")
    return 0
