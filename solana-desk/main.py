#!/usr/bin/env python3
"""Paper desk entry point.

    python main.py run --cycles 3      # the orchestrator loop (src/desk/orchestrator.py)
    python main.py approve LEAD-12     # human gate: paper fill at the stored quote
    python main.py reject LEAD-12
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from desk.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(default_root=ROOT))
