#!/usr/bin/env python3
"""Trading wallet and order execution: the only process that ever loads the wallet key.

    python signer.py init        # create the trading wallet and print the address to fund
    python signer.py status      # address, balance, trading mode
    python signer.py run         # execute the desk's orders (the dashboard starts this for you)
    python signer.py withdraw --to <your main wallet> --all
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from signer.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(root=ROOT))
