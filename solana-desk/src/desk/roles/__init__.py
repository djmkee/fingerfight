"""The desk roles. Each is code plus a system prompt. Code decides; an LLM may only tighten,
except the Approver, which chooses among leads that code already passed."""

from .approver import Approver
from .exit import Exit
from .head import Head
from .risk import Risk
from .search import Search
from .sniper import Sniper

__all__ = ["Approver", "Exit", "Head", "Risk", "Search", "Sniper"]
