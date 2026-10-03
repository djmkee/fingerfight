"""The five desk roles. Each is code plus a system prompt; code decides, an LLM may only tighten."""

from .exit import Exit
from .head import Head
from .risk import Risk
from .search import Search
from .sniper import Sniper

__all__ = ["Exit", "Head", "Risk", "Search", "Sniper"]
