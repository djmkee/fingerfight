# Solana paper desk

A local, paper-only Solana memecoin desk that runs as a multi-role pipeline in one Python
process. Every cycle it discovers candidate pairs, rejects almost all of them through coded
checks plus a Risk role, stages unsigned quote plans for the few that survive, and writes an
append-only audit log. A human approves or rejects each staged lead by ID. In v1 an approval
opens a **paper** position at the quoted price. Nothing is ever signed or sent.

v1 does not copy-trade wallets, launch tokens, use leverage, auto-sign, or broadcast.

## Safety model

- **No keys anywhere near the agents.** The process refuses to start if the environment or
  `.env` defines a wallet-secret variable (`*PRIVATE_KEY*`, `*SEED_PHRASE*`, `*MNEMONIC*`,
  `*KEYPAIR*`, `WALLET_KEY/SECRET/SEED`). Agent code never imports the `signer` package or a
  signing library, and a test enforces that. Any LLM reply that mentions key material or
  wallet access is discarded unread and the role fails closed.
- **Paper only.** `paper_mode: true` is required; the policy loader refuses `false`.
- **Reject is the default path.** Missing data, failed tool calls, an unparseable or silent
  LLM, an expired quote, or no free position slot all close the lead, and each close records
  a reason as `stage/code: details`.
- **Code decides; the LLM can only tighten.** Roles are system prompts plus stored tool
  results. There is no browsing and no function calling. An LLM can drop, fail, reject, or
  add an exit, but it cannot pass a lead that code failed, change a number, or size a trade.
- **Facts live on the lead row.** Risk writes the mint and holder data to the row, then decides
  by reading the row back. Sniper stores the full Jupiter quote. Exit stores each recheck.
  Every claim in a handoff line comes from those stored tool results.
- **One mint per lead.** Every tool result and every LLM handoff that names a mint is compared
  with the lead's mint. A mismatch marks the lead `dead` and zeroes any paper position. SQLite
  triggers also stop a lead's or position's mint from ever changing.
- **Halts are sticky.** Hitting the daily paper-loss limit or the failed-send limit sets
  `halted=true`. Search is then skipped, the database refuses new leads, Sniper stops, and
  approvals are refused until a human runs `clear-halt`. Exit keeps managing open positions.

## Layout

```
solana-desk/
├── main.py                  entry point: run the loop, approve/reject, inspect
├── .vscode/                 launch configurations, test settings, recommended extension
├── config/policy.yaml       risk policy, loaded at startup and enforced in code
├── prompts/                 global_ban.md + one system prompt per role (each < 400 words)
├── fixtures/demo_market.json  synthetic tokens for --demo and the tests
├── src/desk/
│   ├── orchestrator.py      the cycle
│   ├── roles/               head, search, risk, sniper, exit (+ base: LLM consult, handoffs)
│   ├── tools/               dexscreener, jupiter (quotes only), solana_rpc, offline fixtures
│   ├── db.py                SQLite: leads, positions, events (append-only), candidates, state
│   ├── approval.py          the human gate
│   ├── web.py + static/     the local dashboard (python main.py web)
│   ├── policy.py settings.py guards.py handoff.py llm.py reporting.py cli.py
└── src/signer/placeholder.py  boundary for a future out-of-process signer; refuses in v1
```

## Setup

Requires Python 3.12 or newer (tested on 3.12, 3.13, and 3.14).

macOS or Linux:

```bash
cd solana-desk
python3.12 -m venv .venv       # or python3.13 / python3.14
source .venv/bin/activate
pip install -e ".[dev]"        # httpx, PyYAML, pytest
cp .env.example .env           # optional: every value has a default or is optional
```

Windows (PowerShell):

```powershell
cd solana-desk
py -3.12 -m venv .venv         # or py -3.13 / py -3.14
.venv\Scripts\Activate.ps1     # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -e ".[dev]"
copy .env.example .env
```

### VS Code

1. **File > Open Folder** and choose `solana-desk`, the folder that contains `main.py`. Install
   the recommended Python extension when VS Code offers it.
2. Create the environment with the commands above in VS Code's terminal, or run
   **Python: Create Environment** from the Command Palette (Venv; tick the `dev` extras if it
   asks). Then choose `.venv` with **Python: Select Interpreter**.
3. **Run and Debug** (Ctrl+Shift+D), pick **Dashboard: buttons in your browser**, and press F5.
   The dashboard opens in your browser. The list also has terminal versions of each command:
   the offline demo, the live loop, approve and reject (they ask for the lead ID), status, and
   the daily summary.
4. The **Testing** panel finds and runs the pytest suite.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SOLANA_RPC_URL` | `https://api.mainnet-beta.solana.com` | JSON-RPC for mint/freeze authority, supply, largest holders. A Helius URL works as is; its API key stays in the URL and is redacted from errors. |
| `JUPITER_BASE_URL` | `https://lite-api.jup.ag/swap/v1` | Jupiter quote API. Point it at `https://api.jup.ag/swap/v1` if you use a Jupiter API key. |
| `JUPITER_API_KEY` | *(empty)* | Optional, sent as `x-api-key`. |
| `DEXSCREENER_BASE_URL` | `https://api.dexscreener.com` | Public discovery feeds and pair lookups (no key). |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | *(empty)* | Optional OpenAI-compatible endpoint, for example `https://api.openai.com/v1`. Leave empty to run every role on coded rules only. |
| `DESK_DB_PATH` | `data/desk.sqlite3` | SQLite file. |
| `DESK_POLICY_PATH` | `config/policy.yaml` | Policy file. |

There is no wallet key variable, by design. The desk exits with `refusing to start` if one
is set.

## Web dashboard

```bash
python main.py web            # opens http://127.0.0.1:8765 in your browser
python main.py --demo web     # same, starting on the Demo tab
```

The dashboard runs the same code as the commands below, with buttons:

- **Live / Demo tabs.** Live uses real market data; Demo uses the synthetic tokens. Each tab
  has its own database.
- **Run 1 cycle**, or **Start auto-run** every N seconds and **Stop** (it stops after the cycle
  in progress).
- **Approve / Reject** next to each staged lead, with a confirmation. Approve opens a paper
  position at the stored quote; nothing is signed or sent.
- Paper equity, open positions, recent leads with the reason each was rejected, the last run
  summary, the audit log, and **Clear halt** (it asks why it is safe to resume).

Because the page can approve leads, it answers only you. It listens on 127.0.0.1, refuses
requests from any other host name or website, needs a random token that only the page itself
holds, and cannot be embedded in another site. Stop it with Ctrl+C in its terminal. Use
`--port` to pick another port and `--no-browser` to just print the address.

## Run the paper loop

```bash
python main.py run --cycles 3 --interval 60    # three cycles, a minute apart, then a summary
python main.py run                             # until Ctrl-C, then a summary
```

Each cycle:

1. **Head** applies the halt rules (daily paper loss, failed sends) and expires stale quotes.
   If the desk is halted, Search is skipped.
2. **Search** pulls DexScreener's latest token profiles and boosts for Solana, keeps the
   deepest pair where the token is the base token, and filters on liquidity and pair age.
   **Head** assigns lead IDs to the survivors.
3. **Risk** fetches mint data (`getAccountInfo`) and top holders (`getTokenLargestAccounts`)
   for each unscored lead, stores them on the row, and passes or fails it from the row.
4. **Sniper**, for Risk=pass leads only, sizes from policy, fetches a Jupiter buy quote and a
   sell-side check quote, stores them, and stages the lead as `awaiting_approval`.
5. The approval list is printed: lead ID, mint, size, and price impact.
6. **Exit** re-checks open paper positions every `exit.check_interval_minutes`.
7. Every step appends to the `events` table.

When the loop stops, it prints the run summary: candidates scanned, rejected by each stage
(with reasons), and awaiting approval.

### Offline demo

`--demo` swaps in synthetic tokens from `fixtures/demo_market.json` and a separate database
(`data/demo.sqlite3`). The tokens are built to hit each rejection path. Every row they
produce is tagged `fixture/...` as its source, so demo rows can't be mistaken for market data.

```bash
python main.py --demo run --cycles 1 --interval 0
python main.py --demo approve LEAD-1
```

## Approve or reject

Decisions are yes/no, by lead ID only:

```bash
python main.py approve LEAD-123
python main.py reject LEAD-123
```

In paper mode, `approve` marks the lead `paper_filled` and opens a paper position at the
stored quote's price and size. It reads only the database: no tools, no key, no signer.
It is refused when the desk is halted, when the lead is not `awaiting_approval`, when the
quote is older than `approval_ttl_minutes`, or when `max_open_positions` is reached. Leads
nobody decides on expire on the next cycle.

Other commands:

```bash
python main.py status                          # halt flag, equity, approvals, open positions, latest leads
python main.py summary [--date 2026-10-03]     # Head's daily summary (also saved under data/summaries/)
python main.py events [--lead LEAD-123]        # the audit log
python main.py clear-halt --reason "checked the failed sends"
```

`clear-halt` is for humans only. It re-arms the daily loss limit from current paper equity
and resets the day's failed-send count.

## Data model

- `leads`: `lead_id, mint, pair, discovered_at, liquidity_usd, age_minutes, authorities_json,
  top10_holder_pct, risk_status, risk_notes, quote_json, human_decision, status`, plus
  `source`, `market_json` (the snapshot Search filtered on), `reject_reason`, `recheck_json`
  (Exit's latest evidence), and `tx_sig` (reserved for a future signer; always NULL).
- `positions`: `lead_id, mint, paper_entry, paper_exit, unrealized, exit_reason`, plus size,
  token amount, open/close times, and realized paper P&L.
- `events`: `timestamp, lead_id, role, action, payload`. Append-only, enforced by triggers.
- `candidates`: every mint Search scanned, with its latest filter result and reason.
- `desk_state`: halt flag and reason, day-start equity, failed-send counts.

Lead statuses: `new` → `risk_passed` → `awaiting_approval` → `paper_filled` → `paper_closed`.
Any stage can close a lead as `rejected`; a staged quote can become `expired`; a mint mismatch
makes the lead `dead`.

### Handoff format

Every role handoff is one line, stored in the event payload and printed:

```
LEAD-{id} | {mint} | {stage} | {result} | {evidence}
LEAD-7 | <mint> | risk | pass | liquidity_usd=38000 age_minutes=95.0 mint_authority=null freeze_authority=null top10_holder_pct=null
```

## Risk policy

`config/policy.yaml` is loaded once at startup and validated: unknown keys, missing keys,
out-of-range numbers, `paper_mode: false`, and disabled authority checks are all refused.
In brief:

| Check | Enforced by |
|---|---|
| liquidity ≥ `min_liquidity_usd`, pair age ≥ `min_token_age_minutes` | Search filters, then Risk re-checks the stored row |
| mint authority still active, freeze authority set | Risk (fail) |
| top-10 holders > `max_top10_holder_pct` (only when holder data exists) | Risk (fail) |
| blocked Token-2022 extensions (permanent delegate, transfer hook, transfer fee, …) | Risk (fail) |
| buy or sell-side price impact > `max_price_impact_pct`, or no sell route | Sniper (reject) |
| size = `max_position_pct` of paper equity; at most `max_open_positions` | Sniper and approval |
| daily paper loss ≥ `daily_loss_halt_pct`, failed sends ≥ `max_failed_sends` | Head (halt) |
| liquidity drop, exit impact spike, authority change, time stop | Exit (close the paper position) |

## LLM roles

Each role's system prompt is `prompts/global_ban.md` followed by `prompts/<role>.md`. When
an LLM is configured, a role sends that prompt plus a JSON message of the stored numbers and
the policy thresholds, then parses handoff lines back. Code applies only conservative results:

- **Search** may drop a lead. **Exit** may add an exit, but only if the evidence names one of
  the four triggers.
- **Risk** and **Sniper** need an explicit pass from the LLM. A veto, a missing verdict, a call
  error, or a discarded reply fails the lead.
- **Head** may add a narrative under the deterministic daily figures.
- A line naming the wrong mint kills that lead. Lines for unknown leads or other stages are
  logged and ignored.

Without an LLM, every role runs on its coded rules alone.

## Tests

```bash
pytest
```

The required cases are `test_halt.py` (a policy halt blocks new leads), `test_mint_guard.py`
(a mint mismatch kills a lead), `test_risk_gate.py` (a Risk fail never reaches Sniper), and
`test_approval.py` (paper approval needs no key; it also runs the CLI in a clean environment
and asserts that the signer was never loaded). Other tests cover the policy loader, the key
ban, prompts, handoff parsing, Exit triggers, the HTTP clients against mocked responses, a
full demo cycle, and the dashboard (its buttons, and that it refuses other hosts, other
websites, and requests without its token).

## Live trading is not in v1

`src/signer/placeholder.py` marks the boundary and refuses every call. A later milestone would
run a signer as a separate process. For a human-approved lead, it would rebuild the swap from
the stored quote outside the LLM process, re-check it against the policy, sign only there,
broadcast, and write `tx_sig` back. Failed sends would count toward the halt. Key material
must never reach a prompt, an agent tool, the events table, or logs.

## Limitations

- Paper fills use quoted prices. They ignore fees, latency, slippage beyond the quote, and
  failed transactions. Paper results are not evidence of profitability.
- Token age is the DexScreener pair age, which is a lower bound on the token's age.
- Discovery uses DexScreener's latest-profile and boost feeds. They are not a complete list
  of new pairs.
- Top-10 holder share comes from the 20 largest token accounts. Accounts owned by the pair or
  by the listed AMM authorities are excluded; other pool vaults or exchange wallets can still
  inflate the figure. That errs toward rejection.
- Jupiter's `priceImpactPct` is read as a fraction (0.01 = 1%). If an API version reported
  percent instead, impact would be overstated, which again errs toward rejection.
