"""Solana memecoin desk: a multi-role pipeline that stages trades and places orders.

Nothing in this package holds, requests, or signs with a private key. Orders are executed by
the separate `signer` package, run as its own process, which this package never imports.
"""

__version__ = "0.1.0"
