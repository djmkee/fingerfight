# Solana memecoin desk

A local Solana memecoin desk that runs as a multi-role pipeline. Every cycle it discovers
candidate pairs, rejects almost all of them through coded checks plus a Risk role, stages
quotes for the few that survive, and writes an append-only audit log. What happens next
depends on `trading_mode` in `config/policy.yaml`:

| `trading_mode` | A buy | An exit | The wallet |
|---|---|---|---|
| `paper` | a book entry at the quoted price | a book entry at the exit quote | not used |
| `dry_run` (default) | the signer builds, signs and **simulates** the real swap on Solana; nothing is sent | a book entry at the exit quote | its balance sizes trades; nothing moves |
| `live` | the signer sends a **real swap** and books what the chain says happened | a real sell | real SOL |

With `auto_approve: true` the desk trades by itself: an **Approver** role picks among the
leads that passed every coded check, and code sets every size and limit. With
`auto_approve: false` you approve each trade with a button or a command.

The trading wallet's key is held by a **separate signer process**, the only code that ever
loads it. The desk and its LLM roles never see, request, store, or sign with a key.

> **Read this before you fund a wallet.** Memecoins are among the riskiest things you can
> trade; most lose most of their value, and a single trade can go to zero. Nothing here is a
> claim that this desk makes money, and paper or dry-run results are not evidence that it
> would. The live path was built and tested against an in-memory Solana and Jupiter; it has
> not sent a transaction on mainnet during development, so your first live trades are also
> its first. Run in `dry_run` first, use a new wallet that holds only money you can afford to
> lose, and never paste a private key or seed phrase into a chat, an issue, or a prompt.

## Hands-off trading in six steps

1. **Install** (Python 3.12 or newer; see [Setup](#setup) for Windows):
   ```bash
   python3.12 -m venv .venv && source .venv/bin/activate
   pip install -e ".[dev,live]"     # live = solders, used by the signer only
   cp .env.example .env
   ```
2. **Create the trading wallet.** This makes a new keypair and prints its address:
   ```bash
   python signer.py init
   ```
   The key is written to `~/.solana-desk/wallet.json` (on Windows
   `C:\Users\<you>\.solana-desk\wallet.json`), readable only by you, outside the project
   folder and ignored by git. Whoever has that file controls the money in the wallet, and if
   you lose it the money is gone, so keep a private offline backup and withdraw what you do
   not need.
3. **Fund it** from your main wallet with what you can afford to lose. Each buy is
   `max_position_pct` (3%) of equity, capped at `max_trade_sol` (0.05 SOL), so full-size
   trades need about 1.7 SOL; with 0.5 SOL a trade is 0.015 SOL. Below 0.005 SOL the desk
   does not trade. Each buy also costs network and priority fees (at most about 0.0002 SOL)
   and about 0.002 SOL of token-account rent that comes back when the position is sold.
4. **Fill in `.env`:** `SOLANA_RPC_URL` with your own RPC endpoint (Helius, Triton, QuickNode;
   the public one rate-limits hard and is not meant for sending transactions) and the
   `LLM_*` settings, which `decider: ai` needs. Without an LLM it buys nothing; set
   `decider: rules` to trade on coded rules instead.
5. **Start it in dry run** (the default):
   ```bash
   python main.py web --autorun 60
   ```
   The dashboard opens, starts the signer as its own process, and runs a cycle every 60
   seconds. In VS Code: **Run and Debug**, pick **Trading desk: dashboard + auto-run every
   60 s**, press F5. Leave it running for a day or more and read what it would have done:
   the orders table, the simulated fills, the reasons it refused things.
6. **Go live** only once the dry run looks right: set `trading_mode: live` in
   `config/policy.yaml` and restart the dashboard. From then on it sends real swaps.

To stop new buys at once, press **Halt trading now**: open positions are still managed and
sold. Ctrl+C in the dashboard's terminal stops everything, including exits, so open positions
are **not watched while the dashboard is closed**. To take money out:

```bash
python signer.py status                                  # address, balance, trading mode
python signer.py withdraw --to <your main wallet> --all  # or --sol 0.5; asks you to confirm
```

## Safety model

**The key stays in the signer.**

- Only `signer.py` and `src/signer/` load the wallet file. Agent code (`main.py`,
  `src/desk/`) never imports the signer or a signing library and never names the wallet
  file; tests enforce both. `solders` is imported only inside `src/signer/`.
- The desk refuses to start if the environment or `.env` defines a wallet-secret variable
  (`*PRIVATE_KEY*`, `*SEED_PHRASE*`, `*MNEMONIC*`, `*KEYPAIR*`, `WALLET_KEY/SECRET/SEED`).
  The signer is started without the LLM settings. Any LLM reply that mentions key material
  or wallet access is discarded unread and the role fails closed.
- The desk and the signer talk only through orders in the SQLite database. The desk writes
  `pending` orders; only the signer moves them on. One signer per database: a second one
  refuses to start.

**Code sets the limits; the Approver only chooses.**

- The Approver chooses among leads that passed every coded check. It sees numbers only, never
  token names or descriptions (strangers write those, and they can be crafted to steer a
  model). It cannot change a size, a limit, or a check, and if the LLM fails or says nothing,
  nothing is bought. Every other role can only make an outcome more conservative.
- Auto-approvals go through the same gate as yours: refused while halted, after
  `approval_ttl_minutes`, beyond `max_open_positions` (orders in flight count) or
  `max_buys_per_day`, or when the signer is offline or in another mode.
- Halts are sticky. A daily loss of `daily_loss_halt_pct` (20%) of start-of-day equity or
  `max_failed_sends` (3) failed transactions sets `halted=true`; only a human clears it, with
  a reason that goes in the audit log.

**The signer checks every order again, itself, before it signs.**

1. The order is less than `order_max_age_seconds` old (never executed late) and its mint
   matches its lead.
2. Buys: the mode matches `trading_mode`, the desk is not halted, the size is within
   `max_trade_sol`, and slots, buys per day and failed sends are within limits. It re-reads
   `config/policy.yaml` for every order.
3. Buys: it reads the token's mint itself. An active mint authority, a freeze authority, or a
   blocked Token-2022 extension is a refusal.
4. A fresh Jupiter quote must match the order, and its price impact must be within
   `max_price_impact_pct`.
5. Jupiter builds the swap, so the signer inspects it before signing: the trading wallet pays
   and is the only signer, and every instruction calls a program in `allowed_programs`
   (`config/signer.yaml`).
6. It simulates the signed swap against current chain state and checks the effect on the
   whole wallet: SOL spent (wrapped SOL included) at most the size plus
   `fee_reserve_lamports`, at least the quote's minimum out, and every other token account
   with a balance keeps its tokens, its owner, and gets no new delegate. A buy into a token
   account someone else may spend is refused.
7. Live: the signature is recorded before the first send, the transaction is re-broadcast
   until it confirms or its blockhash expires, and the fill is booked from the confirmed
   transaction. After a crash, unsent orders fail and sent ones are resolved from the chain,
   never guessed.

**And the rest of the pipeline.**

- Reject is the default path. Missing data, a failed tool call, an unparseable or silent LLM,
  a stale quote, or no free slot closes the lead with a recorded reason.
- Facts live on the lead row. Every claim in a handoff line comes from stored tool results.
- One mint per lead. A tool result or LLM line naming a different mint marks the lead `dead`;
  a live position on a dead lead is sold, not written off.

## Layout

```
solana-desk/
├── main.py                  the desk: the loop, the dashboard, approve/reject, status
├── signer.py                the signer: wallet, order execution, withdrawals
├── config/policy.yaml       trading mode and limits, enforced by the desk and the signer
├── config/signer.yaml       signer-only settings: key file path, fees, allowed programs
├── prompts/                 global_ban.md + one system prompt per role (each < 400 words)
├── fixtures/demo_market.json  synthetic tokens for --demo and the tests
├── src/desk/                agent code; never loads a key
│   ├── orchestrator.py      the cycle
│   ├── roles/               head, search, risk, sniper, approver, exit
│   ├── execution.py         orders and fills, equity and sizing per mode
│   ├── approval.py          the approval gate, for you and the Approver alike
│   ├── tools/               dexscreener, jupiter quotes, solana_rpc (read-only), fixtures
│   ├── db.py                SQLite: leads, positions, orders, events, candidates, state
│   ├── web.py + static/     the dashboard
│   └── policy.py settings.py guards.py handoff.py llm.py reporting.py cli.py
├── src/signer/              the only code that loads the key
│   ├── executor.py          re-checks, builds, signs, simulates, sends, books
│   ├── verify.py            transaction and simulation checks
│   ├── chain.py             RPC calls and Jupiter's swap builder
│   └── wallet.py config.py lock.py cli.py
└── tests/
```

## Setup

Requires Python 3.12 or newer (tested on 3.12, 3.13, and 3.14).

macOS or Linux:

```bash
cd solana-desk
python3.12 -m venv .venv       # or python3.13 / python3.14
source .venv/bin/activate
pip install -e ".[dev,live]"   # httpx, PyYAML, pytest, solders
cp .env.example .env
```

Windows (PowerShell):

```powershell
cd solana-desk
py -3.12 -m venv .venv         # or py -3.13 / py -3.14
.venv\Scripts\Activate.ps1     # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -e ".[dev,live]"
copy .env.example .env
```

Paper mode alone needs only `pip install -e ".[dev]"`.

### VS Code

1. **File > Open Folder** and choose `solana-desk`, the folder that contains `main.py`. Install
   the recommended Python extension when VS Code offers it.
2. Create the environment with the commands above in VS Code's terminal, then choose `.venv`
   with **Python: Select Interpreter**.
3. **Run and Debug** (Ctrl+Shift+D) lists ready-made configurations:
   - **Trading desk: dashboard + auto-run every 60 s**: hands-off trading, steps 5 and 6 above.
   - **Dashboard: buttons in your browser** and **Dashboard: open on the Demo tab**.
   - **Signer: create the trading wallet**, **Signer: wallet address and balance**, and
     **Signer: withdraw everything to your main wallet** (it asks for the address).
   - Terminal versions of the loop, approve, reject, status and the daily summary, for the
     offline demo and for market data.
4. The **Testing** panel finds and runs the pytest suite.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SOLANA_RPC_URL` | `https://api.mainnet-beta.solana.com` | JSON-RPC for token data, and for the signer's balances, simulations and sends. Use your own endpoint for dry run and live. A Helius URL works as is; its API key stays in the URL and is redacted from errors. |
| `JUPITER_BASE_URL` | `https://lite-api.jup.ag/swap/v1` | Jupiter quote and swap API. Point it at `https://api.jup.ag/swap/v1` if you use a Jupiter API key. |
| `JUPITER_API_KEY` | *(empty)* | Optional, sent as `x-api-key`. |
| `DEXSCREENER_BASE_URL` | `https://api.dexscreener.com` | Public discovery feeds and pair lookups (no key). |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | *(empty)* | OpenAI-compatible endpoint, for example `https://api.openai.com/v1`. Needed for `decider: ai`; the other roles run on coded rules without it. |
| `DESK_DB_PATH` | `data/desk.sqlite3` | SQLite file shared by the desk and the signer. |
| `DESK_POLICY_PATH` | `config/policy.yaml` | Policy file, read by both. |

There is no wallet key variable, by design. The desk exits with `refusing to start` if one
is set. The signer's own settings, including where the key file lives, are in
`config/signer.yaml`, which the desk never reads.

## Web dashboard

```bash
python main.py web                 # opens http://127.0.0.1:8765
python main.py web --autorun 60    # and starts the loop right away, a cycle every 60 s
python main.py --demo web          # starts on the Demo tab (synthetic tokens, always paper)
```

- **Live / Demo tabs**, each with its own database. Live uses market data and the policy's
  `trading_mode`; Demo is always paper.
- A banner with the trading mode, and a **wallet** card: the signer's status, address,
  balance, and a setup checklist while the signer is not running.
- **Run 1 cycle**, or **Start auto-run** every N seconds and **Stop** (after the cycle in
  progress).
- **Orders**: every order the signer received, its status, and a Solscan link per
  transaction. **Approve / Reject** buttons for staged leads when `auto_approve` is off.
- Equity, open positions, recent leads with the reason each was rejected, the run summary,
  the audit log, **Halt trading now**, and **Clear halt** (it asks why it is safe to resume).

With `trading_mode` other than `paper`, the dashboard starts the signer
(`python signer.py run`) and stops it when it closes; the signer also stops by itself if the
dashboard disappears. It is also started in `paper` mode while live positions are still open,
so they can be sold. Use `--no-signer` to run the signer yourself in another terminal.

Because the page can trade, it answers only you. It listens on 127.0.0.1, refuses requests
from any other host name or website, needs a random token only the page holds, and cannot be
embedded in another site. Use `--port` to pick another port and `--no-browser` to just print
the address.

## Run the loop from the terminal

```bash
python main.py run --cycles 3 --interval 60    # three cycles, a minute apart, then a summary
python main.py run                             # until Ctrl-C, then a summary
python signer.py run                           # dry_run / live: the signer, in a second terminal
```

Each cycle:

1. **Head** expires stale quotes and orders, applies the halt rules, and skips Search while
   halted.
2. **Search** pulls DexScreener's latest token profiles and boosts for Solana, keeps the
   deepest pair where the token is the base token, and filters on liquidity and pair age.
   **Head** assigns lead IDs to the survivors.
3. **Risk** fetches mint data and top holders for each unscored lead, stores them on the row,
   and passes or fails it from the row.
4. **Sniper**, for Risk=pass leads only, sizes from policy, fetches a Jupiter buy quote and a
   sell-side check quote, stores them, and stages the lead as `awaiting_approval`.
5. **Approver** (with `auto_approve`) picks which staged leads to buy, up to the free slots.
   Otherwise the list waits for you.
6. **Exit** re-checks open positions every `exit.check_interval_minutes` and exits on a
   liquidity drop, an exit-impact spike, an authority change, or the time stop.
7. Every step appends to the `events` table. The run summary counts candidates scanned,
   rejected by each stage (with reasons), awaiting approval, bought, and orders still pending.

### Offline demo

`--demo` swaps in synthetic tokens from `fixtures/demo_market.json`, a separate database
(`data/demo.sqlite3`), and paper mode. The tokens are built to hit each rejection path, and
every row they produce is tagged `fixture/...` so it can't be mistaken for market data.

```bash
python main.py --demo run --cycles 1 --interval 0
python main.py --demo approve LEAD-1
```

## Approve or reject by hand

With `auto_approve: false`, decisions are yes/no, by lead ID only:

```bash
python main.py approve LEAD-123
python main.py reject LEAD-123
```

In paper mode an approval opens a paper position at the stored quote. In `dry_run` and `live`
it places a buy order for the signer, which needs to be running. Approvals are refused while
halted, when the lead is not `awaiting_approval`, when the quote is older than
`approval_ttl_minutes`, or when a limit is reached.

Other commands:

```bash
python main.py status                          # halt flag, equity, approvals, open positions, latest leads
python main.py summary [--date 2026-10-03]     # Head's daily summary (also saved under data/summaries/)
python main.py events [--lead LEAD-123]        # the audit log
python main.py clear-halt --reason "checked the failed sends"
```

`clear-halt` is for humans only. It re-arms the daily loss limit from current equity and
resets the day's failed-send count.

## Signer commands

```bash
python signer.py init       # create the trading wallet; refuses to overwrite an existing one
python signer.py status     # address, SOL balance, trading mode
python signer.py run        # execute the desk's orders until Ctrl+C (the dashboard does this for you)
python signer.py withdraw --to <address> --sol 0.5    # or --all; type the address's first 4 characters to confirm
python signer.py import     # use an existing key instead of init (hidden input; a dedicated wallet only)
```

## Data model

- `leads`: `lead_id, mint, pair, discovered_at, liquidity_usd, age_minutes, authorities_json,
  top10_holder_pct, risk_status, risk_notes, quote_json, human_decision, status`, plus
  `source`, `market_json`, `reject_reason`, `recheck_json`, and `tx_sig` (the buy's
  transaction in live mode).
- `positions`: `lead_id, mint, paper_entry, paper_exit, unrealized, exit_reason`, plus `mode`
  (paper, dry_run, live), size, token amount, open/close times, realized P&L, and the open
  sell order.
- `orders`: `order_id, lead_id, mint, side, mode, amount, status, tx_sig`, the fill amounts,
  fee, and the signer's reason for any refusal or failure.
- `events`: `timestamp, lead_id, role, action, payload`. Append-only, enforced by triggers.
- `candidates`: every mint Search scanned. `desk_state`: halt flag, baselines, failed-send
  counts, and the signer's heartbeat, wallet address and balance.

Lead statuses: `new` → `risk_passed` → `awaiting_approval` → `ordered` (dry_run, live) →
`filled` → `closed`. Any stage can close a lead as `rejected`; a staged quote or an order can
become `expired`; a mint mismatch makes the lead `dead`.

Order statuses: `pending` → `working` → `sending` (live) → `filled`; a dry run ends
`simulated`; `refused` means the signer's own checks said no; `failed` means it could not be
built, simulated, sent or confirmed; `expired` means no signer picked it up in time.

### Handoff format

Every role handoff is one line, stored in the event payload and printed:

```
LEAD-{id} | {mint} | {stage} | {result} | {evidence}
LEAD-7 | <mint> | risk | pass | liquidity_usd=38000 age_minutes=95.0 mint_authority=null freeze_authority=null top10_holder_pct=null
```

## Risk policy

`config/policy.yaml` is validated at startup: unknown keys, missing keys, out-of-range numbers
and disabled authority checks are refused. The signer re-reads it for every order.

| Setting | Default | Enforced by |
|---|---|---|
| `trading_mode` | `dry_run` | everything: `paper`, `dry_run`, or `live` |
| `auto_approve`, `decider` | `true`, `ai` | Approver: `ai` (LLM picks, numbers only) or `rules` (deepest liquidity first) |
| `max_position_pct`, `max_trade_sol` | 3%, 0.05 SOL | Sniper sizes, approval and signer re-check |
| `max_open_positions`, `max_buys_per_day` | 4, 20 | Approver, approval, signer (in-flight buys count) |
| `daily_loss_halt_pct` | 20% | Head halts; equity counts open positions at their exit quotes |
| `max_failed_sends` | 3 per UTC day | counted as they happen; reaching it halts the desk |
| `order_timeout_seconds` | 120 | Head expires orders no signer picked up |
| `min_liquidity_usd`, `min_token_age_minutes` | $15,000, 5 min | Search filters, Risk re-checks |
| mint authority active, freeze authority set, blocked Token-2022 extensions | refused | Risk, and the signer again before a buy |
| `max_top10_holder_pct` | 30% | Risk, when holder data exists |
| `max_price_impact_pct`, `slippage_bps` | 3%, 1% | Sniper, and the signer's fresh quote and simulation |
| `exit.*` | 30% liquidity drop, 6% exit impact, 240 min time stop | Exit |

`config/signer.yaml` holds the signer's own settings: the key file's path, the most a
transaction may pay in priority fees, the fee reserve a trade may cost beyond its size, the
oldest order it will execute, and the programs a swap may call.

## LLM roles

Each role's system prompt is `prompts/global_ban.md` followed by `prompts/<role>.md`. When an
LLM is configured, a role sends that prompt plus a JSON message of stored numbers and policy
thresholds, then parses handoff lines back:

- **Approver** answers `buy` or `skip` per lead. Only `buy` buys; anything else is a skip.
- **Search** may drop a lead. **Exit** may add an exit, only if the evidence names a trigger.
- **Risk** and **Sniper** need an explicit pass. A veto, a missing verdict, a call error, or a
  discarded reply fails the lead.
- **Head** may add a narrative under the deterministic daily figures.
- A line naming the wrong mint kills that lead.

## Tests

```bash
pytest
```

The original required cases are `test_halt.py`, `test_mint_guard.py`, `test_risk_gate.py`
and `test_approval.py`. Trading is covered by `test_execution.py` (orders, fills, failures,
expiry, live exits, equity per mode), `test_approver.py` (who picks, budgets, no LLM means no
trade), and `test_signer.py`, which runs the real signer against an in-memory Solana and
Jupiter with real signed transactions: dry-run and live buys, live sells and closing the
token account, each refusal (stale order, halt, over the cap, mode change, mint authority,
price impact, foreign program, extra signer, draining SOL or wrapped SOL, touching another
token), failed and lost transactions, crash recovery, withdrawals, and the one-signer lock.
Other tests cover the policy loader, the key ban, prompts, handoff parsing, Exit triggers, the
HTTP clients against mocked responses, a full demo cycle, and the dashboard.

## Limitations

- Live trading has only met an in-memory chain in tests. Real RPC nodes, Jupiter responses and
  network conditions can differ; dry run exercises everything except the send.
- Dry-run sells can't be simulated (the wallet doesn't hold the tokens), so dry-run exits are
  booked at the exit quote, like paper. Paper and dry-run results ignore real slippage,
  failed transactions and fees on exits.
- Exits only happen while the dashboard or `main.py run` is running. Positions are not
  watched while it is closed.
- Coded checks catch common traps (live mint or freeze authority, hostile Token-2022
  features, thin liquidity, no sell route, concentrated holders), not every scam. Liquidity
  can vanish between the quote and the swap; a token can go to zero.
- Token age is the DexScreener pair age, a lower bound. Discovery uses DexScreener's
  latest-profile and boost feeds, which are not a complete list of new pairs.
- Top-10 holder share comes from the 20 largest token accounts, excluding known pool
  authorities; other vaults or exchange wallets can still inflate it, which errs toward
  rejection.
