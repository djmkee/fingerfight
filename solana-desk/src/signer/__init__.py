"""The signer: the only code that loads the trading wallet's key, and it runs as its own process.

Agent code (src/desk, main.py) never imports this package, and a test enforces that. The signer
reads orders from the desk database, re-checks them against config/policy.yaml, and signs only
transactions that pass its own checks (see executor.py and verify.py).
"""
