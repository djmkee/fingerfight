#!/usr/bin/env python3
"""Desk entry point: the agent process. It never loads a wallet key (signer.py does).

    python main.py web --autorun 60    # the dashboard, running the loop every 60 s
    python main.py run --cycles 3      # the orchestrator loop (src/desk/orchestrator.py)
    python main.py approve LEAD-12     # paper: a book entry; dry_run/live: an order for the signer
    python main.py reject LEAD-12
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from desk.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(default_root=ROOT))
