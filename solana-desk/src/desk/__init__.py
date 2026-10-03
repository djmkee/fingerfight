"""Paper-only Solana memecoin desk: a multi-role pipeline with a human approval gate.

Nothing in this package holds, requests, or signs with a private key. The optional
signer boundary lives in the separate `signer` package, which this package never imports.
"""

__version__ = "0.1.0"
