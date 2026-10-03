"""SQLite storage: leads, positions, the append-only events log, scan history, and desk state."""

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class Status:
    """Lead lifecycle. Every change is a compare-and-set, so the loop and the CLI cannot collide."""

    NEW = "new"                     # Search emitted it; Risk has not scored it
    RISK_PASSED = "risk_passed"     # Risk=pass; Sniper has not quoted it
    AWAITING = "awaiting_approval"  # quote staged; waiting for a decision
    ORDERED = "ordered"             # approved; a buy order waits for the signer (dry_run, live)
    FILLED = "filled"               # position open: paper fill, simulated fill, or confirmed swap
    CLOSED = "closed"               # position closed
    REJECTED = "rejected"           # closed by Risk, Sniper, the decider, a failed order, or a human
    EXPIRED = "expired"             # a quote or order went stale before it was used
    DEAD = "dead"                   # a stage saw a different mint

    ACTIVE = (NEW, RISK_PASSED, AWAITING, ORDERED, FILLED)


class OrderStatus:
    """Order lifecycle. The agent writes `pending`; only the signer moves an order past it."""

    PENDING = "pending"       # placed by the agent
    WORKING = "working"       # claimed by the signer, nothing sent yet
    SENDING = "sending"       # signed and broadcast; tx_sig recorded before the first send
    FILLED = "filled"         # confirmed on chain (live)
    SIMULATED = "simulated"   # dry run: built, signed and simulated, never sent
    FAILED = "failed"         # could not be built, simulated, sent, or confirmed
    REFUSED = "refused"       # the signer's own checks said no
    EXPIRED = "expired"       # not picked up in time

    OPEN = (PENDING, WORKING, SENDING)


class HaltedError(RuntimeError):
    """The desk is halted: new leads are refused until a human clears it."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    lead_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mint             TEXT NOT NULL,
    pair             TEXT NOT NULL,
    discovered_at    TEXT NOT NULL,
    liquidity_usd    REAL,
    age_minutes      REAL,
    authorities_json TEXT,
    top10_holder_pct REAL,
    risk_status      TEXT CHECK (risk_status IN ('pass', 'fail')),
    risk_notes       TEXT,
    quote_json       TEXT,
    human_decision   TEXT CHECK (human_decision IN ('approve', 'reject')),
    status           TEXT NOT NULL,
    source           TEXT,  -- discovery feed, e.g. dexscreener/token-profiles/latest
    market_json      TEXT,  -- the pair snapshot Search filtered on
    reject_reason    TEXT,  -- "<stage>/<code>: <details>" on every closed lead
    recheck_json     TEXT,  -- Exit's latest recheck: pair liquidity, exit quote, mint data
    tx_sig           TEXT,  -- the buy's transaction signature (live mode)
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS leads_by_status ON leads (status);
CREATE INDEX IF NOT EXISTS leads_by_mint ON leads (mint);

CREATE TABLE IF NOT EXISTS positions (
    lead_id             INTEGER PRIMARY KEY REFERENCES leads (lead_id),
    mint                TEXT NOT NULL,
    mode                TEXT NOT NULL DEFAULT 'paper',  -- paper | dry_run | live
    sell_order_id       INTEGER,        -- set while a live sell order is open
    paper_entry         REAL NOT NULL,  -- entry price, SOL per whole token (quote or actual fill)
    paper_exit          REAL,           -- exit price, SOL per whole token (0 when nothing came back)
    unrealized          REAL NOT NULL DEFAULT 0,  -- SOL: latest exit-quote value minus cost
    exit_reason         TEXT,
    size_sol            REAL NOT NULL,
    token_amount        TEXT NOT NULL,  -- raw token units, as text because they can exceed 64 bits
    token_decimals      INTEGER NOT NULL,
    entry_liquidity_usd REAL,
    opened_at           TEXT NOT NULL,
    closed_at           TEXT,
    last_checked_at     TEXT,
    recheck_failures    INTEGER NOT NULL DEFAULT 0,
    realized_sol        REAL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id            INTEGER NOT NULL REFERENCES leads (lead_id),
    mint               TEXT NOT NULL,
    side               TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    mode               TEXT NOT NULL CHECK (mode IN ('dry_run', 'live')),
    amount             TEXT NOT NULL,  -- buy: lamports to spend; sell: raw token units to sell
    reason             TEXT,
    status             TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    tx_sig             TEXT,
    valid_until_height INTEGER,        -- the transaction cannot land after this block height
    in_amount          TEXT,           -- what left the wallet (lamports, or raw tokens for a sell)
    out_amount         TEXT,           -- what arrived (raw tokens, or lamports for a sell)
    fee_lamports       INTEGER,
    detail             TEXT            -- why it failed or was refused, or what the simulation showed
);
CREATE INDEX IF NOT EXISTS orders_by_status ON orders (status);

CREATE TABLE IF NOT EXISTS events (
    event_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    lead_id   INTEGER,
    role      TEXT NOT NULL,
    action    TEXT NOT NULL,
    payload   TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_by_lead ON events (lead_id);

CREATE TABLE IF NOT EXISTS candidates (
    mint        TEXT PRIMARY KEY,
    pair        TEXT,
    source      TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    seen_count  INTEGER NOT NULL,
    last_result TEXT NOT NULL,  -- pass | filtered | duplicate
    last_reason TEXT
);

CREATE TABLE IF NOT EXISTS desk_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- The audit log is append-only, and no stage can rewrite a lead's mint.
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS leads_mint_fixed BEFORE UPDATE OF mint ON leads
WHEN NEW.mint IS NOT OLD.mint
BEGIN SELECT RAISE(ABORT, 'a lead mint cannot change'); END;
CREATE TRIGGER IF NOT EXISTS positions_mint_matches_lead BEFORE INSERT ON positions
WHEN NEW.mint IS NOT (SELECT mint FROM leads WHERE lead_id = NEW.lead_id)
BEGIN SELECT RAISE(ABORT, 'position mint differs from its lead'); END;
CREATE TRIGGER IF NOT EXISTS orders_mint_matches_lead BEFORE INSERT ON orders
WHEN NEW.mint IS NOT (SELECT mint FROM leads WHERE lead_id = NEW.lead_id)
BEGIN SELECT RAISE(ABORT, 'order mint differs from its lead'); END;
CREATE TRIGGER IF NOT EXISTS positions_mint_fixed BEFORE UPDATE OF mint ON positions
WHEN NEW.mint IS NOT OLD.mint
BEGIN SELECT RAISE(ABORT, 'a position mint cannot change'); END;
CREATE TRIGGER IF NOT EXISTS orders_mint_fixed BEFORE UPDATE OF mint ON orders
WHEN NEW.mint IS NOT OLD.mint
BEGIN SELECT RAISE(ABORT, 'an order mint cannot change'); END;
"""


@dataclass(frozen=True)
class Lead:
    lead_id: int
    mint: str
    pair: str
    discovered_at: str
    liquidity_usd: float | None
    age_minutes: float | None
    authorities_json: str | None
    top10_holder_pct: float | None
    risk_status: str | None
    risk_notes: str | None
    quote_json: str | None
    human_decision: str | None
    status: str
    source: str | None
    market_json: str | None
    reject_reason: str | None
    recheck_json: str | None
    tx_sig: str | None
    updated_at: str

    @property
    def ref(self) -> str:
        return f"LEAD-{self.lead_id}"

    def authorities(self) -> dict[str, Any]:
        return json.loads(self.authorities_json) if self.authorities_json else {}

    def quote(self) -> dict[str, Any]:
        return json.loads(self.quote_json) if self.quote_json else {}

    def market(self) -> dict[str, Any]:
        return json.loads(self.market_json) if self.market_json else {}


@dataclass(frozen=True)
class Position:
    lead_id: int
    mint: str
    paper_entry: float
    paper_exit: float | None
    unrealized: float
    exit_reason: str | None
    size_sol: float
    token_amount: str
    token_decimals: int
    entry_liquidity_usd: float | None
    opened_at: str
    closed_at: str | None
    last_checked_at: str | None
    recheck_failures: int
    realized_sol: float | None
    mode: str = "paper"
    sell_order_id: int | None = None

    @property
    def is_open(self) -> bool:
        return self.closed_at is None


@dataclass(frozen=True)
class Order:
    order_id: int
    lead_id: int
    mint: str
    side: str
    mode: str
    amount: str
    reason: str | None
    status: str
    created_at: str
    updated_at: str
    tx_sig: str | None
    valid_until_height: int | None
    in_amount: str | None
    out_amount: str | None
    fee_lamports: int | None
    detail: str | None


# Columns the pipeline may write. mint and lead_id never change.
_LEAD_WRITABLE = frozenset({
    "liquidity_usd", "age_minutes", "authorities_json", "top10_holder_pct", "risk_status",
    "risk_notes", "quote_json", "human_decision", "status", "market_json", "reject_reason",
    "recheck_json", "tx_sig", "updated_at",
})
_POSITION_WRITABLE = frozenset({"unrealized", "last_checked_at", "recheck_failures", "sell_order_id"})
_ORDER_WRITABLE = frozenset({
    "status", "updated_at", "tx_sig", "valid_until_height", "in_amount", "out_amount", "fee_lamports", "detail",
})


def _reason_code(reason: str | None) -> str | None:
    return reason.split(":", 1)[0] if reason else None


def _assignments(fields: dict[str, Any], writable: frozenset[str]) -> str:
    unknown = set(fields) - writable
    if unknown:
        raise ValueError(f"not writable here: {', '.join(sorted(unknown))}")
    return ", ".join(f"{name} = ?" for name in fields)


class DeskDB:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Bring databases made by earlier versions up to this schema."""
        with self.tx():
            for table, column, ddl in (("leads", "recheck_json", "TEXT"),
                                       ("positions", "mode", "TEXT NOT NULL DEFAULT 'paper'"),
                                       ("positions", "sell_order_id", "INTEGER")):
                columns = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
                if column not in columns:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            self.conn.execute("UPDATE leads SET status = 'filled' WHERE status = 'paper_filled'")
            self.conn.execute("UPDATE leads SET status = 'closed' WHERE status = 'paper_closed'")

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[None]:
        """One atomic unit of work; nested calls join the outer transaction."""
        if self.conn.in_transaction:
            yield
            return
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return row[0] if row else None

    # desk state ---------------------------------------------------------------

    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM desk_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: object) -> None:
        self.conn.execute(
            "INSERT INTO desk_state (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    def is_halted(self) -> bool:
        return self.get_state("halted") == "1"

    def halt_reason(self) -> str | None:
        return self.get_state("halt_reason") if self.is_halted() else None

    def set_halted(self, reason: str, ts: str) -> None:
        with self.tx():
            self.set_state("halted", "1")
            self.set_state("halt_reason", reason)
            self.set_state("halted_at", ts)

    def clear_halt(self) -> None:
        with self.tx():
            self.set_state("halted", "0")
            self.set_state("halt_reason", "")

    # events -------------------------------------------------------------------

    def log_event(self, ts: str, role: str, action: str, lead_id: int | None = None,
                  payload: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            "INSERT INTO events (timestamp, lead_id, role, action, payload) VALUES (?, ?, ?, ?, ?)",
            (ts, lead_id, role, action, json.dumps(payload or {}, default=str, sort_keys=True)),
        )

    def events(self, lead_id: int | None = None, limit: int = 50) -> list[sqlite3.Row]:
        where, params = ("WHERE lead_id = ?", (lead_id,)) if lead_id is not None else ("", ())
        rows = self.conn.execute(
            f"SELECT * FROM events {where} ORDER BY event_id DESC LIMIT ?", (*params, limit)
        ).fetchall()
        return rows[::-1]

    # candidates (everything Search scanned, including what it filtered) --------

    def record_candidate(self, *, mint: str, pair: str, source: str, ts: str, result: str,
                         reason: str | None) -> bool:
        """Upsert a scanned candidate. True when its outcome changed since the last scan."""
        previous = self.conn.execute(
            "SELECT last_result, last_reason FROM candidates WHERE mint = ?", (mint,)
        ).fetchone()
        self.conn.execute(
            """INSERT INTO candidates
                   (mint, pair, source, first_seen, last_seen, seen_count, last_result, last_reason)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT (mint) DO UPDATE SET
                   pair = excluded.pair, source = excluded.source, last_seen = excluded.last_seen,
                   seen_count = seen_count + 1, last_result = excluded.last_result,
                   last_reason = excluded.last_reason""",
            (mint, pair, source, ts, ts, result, reason),
        )
        if previous is None:
            return True
        return (previous["last_result"], _reason_code(previous["last_reason"])) != (result, _reason_code(reason))

    # leads ---------------------------------------------------------------------

    def insert_lead(self, *, ts: str, mint: str, pair: str, liquidity_usd: float | None,
                    age_minutes: float | None, source: str, market_json: str) -> int:
        with self.tx():
            if self.is_halted():
                raise HaltedError(f"desk is halted ({self.get_state('halt_reason')}); new leads are refused")
            cursor = self.conn.execute(
                """INSERT INTO leads (mint, pair, discovered_at, liquidity_usd, age_minutes, status,
                                      source, market_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (mint, pair, ts, liquidity_usd, age_minutes, Status.NEW, source, market_json, ts),
            )
            return int(cursor.lastrowid)

    def get_lead(self, lead_id: int) -> Lead | None:
        row = self.conn.execute("SELECT * FROM leads WHERE lead_id = ?", (lead_id,)).fetchone()
        return Lead(**dict(row)) if row else None

    def leads(self, status: str | None = None) -> list[Lead]:
        if status is None:
            rows = self.conn.execute("SELECT * FROM leads ORDER BY lead_id")
        else:
            rows = self.conn.execute("SELECT * FROM leads WHERE status = ? ORDER BY lead_id", (status,))
        return [Lead(**dict(row)) for row in rows]

    def leads_between(self, since: str, until: str) -> list[Lead]:
        rows = self.conn.execute(
            "SELECT * FROM leads WHERE discovered_at >= ? AND discovered_at < ? ORDER BY lead_id",
            (since, until),
        )
        return [Lead(**dict(row)) for row in rows]

    def recent_leads(self, limit: int) -> list[Lead]:
        rows = self.conn.execute("SELECT * FROM leads ORDER BY lead_id DESC LIMIT ?", (limit,)).fetchall()
        return [Lead(**dict(row)) for row in reversed(rows)]

    def latest_lead_for_mint(self, mint: str) -> Lead | None:
        row = self.conn.execute(
            "SELECT * FROM leads WHERE mint = ? ORDER BY lead_id DESC LIMIT 1", (mint,)
        ).fetchone()
        return Lead(**dict(row)) if row else None

    def count_leads(self, status: str) -> int:
        return int(self.scalar("SELECT COUNT(*) FROM leads WHERE status = ?", (status,)))

    def transition(self, lead_id: int, from_status: str | Iterable[str], to_status: str, ts: str,
                   **fields: Any) -> bool:
        """Compare-and-set a lead's status plus fields. False if the lead was not in from_status."""
        sources = (from_status,) if isinstance(from_status, str) else tuple(from_status)
        values = {"status": to_status, "updated_at": ts, **fields}
        marks = ", ".join("?" for _ in sources)
        cursor = self.conn.execute(
            f"UPDATE leads SET {_assignments(values, _LEAD_WRITABLE)} "
            f"WHERE lead_id = ? AND status IN ({marks})",
            (*values.values(), lead_id, *sources),
        )
        return cursor.rowcount == 1

    def update_lead(self, lead_id: int, ts: str, **fields: Any) -> None:
        """Store data on a lead without changing its status."""
        if "status" in fields:
            raise ValueError("use transition() to change a lead's status")
        values = {**fields, "updated_at": ts}
        self.conn.execute(
            f"UPDATE leads SET {_assignments(values, _LEAD_WRITABLE)} WHERE lead_id = ?",
            (*values.values(), lead_id),
        )

    # positions -----------------------------------------------------------------

    def open_position(self, *, lead_id: int, mint: str, paper_entry: float, size_sol: float,
                      token_amount: int | str, token_decimals: int,
                      entry_liquidity_usd: float | None, opened_at: str, mode: str = "paper") -> None:
        self.conn.execute(
            """INSERT INTO positions (lead_id, mint, mode, paper_entry, size_sol, token_amount,
                                      token_decimals, entry_liquidity_usd, opened_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (lead_id, mint, mode, paper_entry, size_sol, str(token_amount), token_decimals,
             entry_liquidity_usd, opened_at),
        )

    def get_position(self, lead_id: int) -> Position | None:
        row = self.conn.execute("SELECT * FROM positions WHERE lead_id = ?", (lead_id,)).fetchone()
        return Position(**dict(row)) if row else None

    def positions(self, *, open_only: bool = False, mode: str | None = None) -> list[Position]:
        clauses, params = (["closed_at IS NULL"] if open_only else []), []
        if mode is not None:
            clauses.append("mode = ?")
            params.append(mode)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(f"SELECT * FROM positions {where} ORDER BY lead_id", params)
        return [Position(**dict(row)) for row in rows]

    def update_position(self, lead_id: int, **fields: Any) -> None:
        self.conn.execute(
            f"UPDATE positions SET {_assignments(fields, _POSITION_WRITABLE)} WHERE lead_id = ?",
            (*fields.values(), lead_id),
        )

    def close_position(self, lead_id: int, *, ts: str, paper_exit: float, realized_sol: float,
                       exit_reason: str) -> bool:
        cursor = self.conn.execute(
            """UPDATE positions
               SET paper_exit = ?, realized_sol = ?, exit_reason = ?, closed_at = ?,
                   last_checked_at = ?, unrealized = 0
               WHERE lead_id = ? AND closed_at IS NULL""",
            (paper_exit, realized_sol, exit_reason, ts, ts, lead_id),
        )
        return cursor.rowcount == 1

    def _position_sum(self, expression: str, where: str, mode: str | None) -> float:
        sql = f"SELECT COALESCE(SUM({expression}), 0) FROM positions WHERE {where}"
        if mode is None:
            return float(self.scalar(sql))
        return float(self.scalar(sql + " AND mode = ?", (mode,)))

    def count_open_positions(self, mode: str | None = None) -> int:
        return int(self._position_sum("1", "closed_at IS NULL", mode))

    def realized_total(self, mode: str | None = None) -> float:
        return self._position_sum("realized_sol", "closed_at IS NOT NULL", mode)

    def unrealized_total(self, mode: str | None = None) -> float:
        return self._position_sum("unrealized", "closed_at IS NULL", mode)

    def open_value_total(self, mode: str) -> float:
        """What open positions are worth at their latest exit quotes (cost + unrealized)."""
        return self._position_sum("size_sol + unrealized", "closed_at IS NULL", mode)

    # orders --------------------------------------------------------------------

    def insert_order(self, *, lead_id: int, mint: str, side: str, mode: str, amount: int | str,
                     reason: str, ts: str) -> int:
        cursor = self.conn.execute(
            """INSERT INTO orders (lead_id, mint, side, mode, amount, reason, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (lead_id, mint, side, mode, str(amount), reason, OrderStatus.PENDING, ts, ts),
        )
        return int(cursor.lastrowid)

    def get_order(self, order_id: int) -> Order | None:
        row = self.conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        return Order(**dict(row)) if row else None

    def orders(self, statuses: Iterable[str]) -> list[Order]:
        statuses = tuple(statuses)
        marks = ", ".join("?" for _ in statuses)
        rows = self.conn.execute(f"SELECT * FROM orders WHERE status IN ({marks}) ORDER BY order_id", statuses)
        return [Order(**dict(row)) for row in rows]

    def recent_orders(self, limit: int) -> list[Order]:
        rows = self.conn.execute("SELECT * FROM orders ORDER BY order_id DESC LIMIT ?", (limit,)).fetchall()
        return [Order(**dict(row)) for row in rows]

    def update_order(self, order_id: int, from_status: str | Iterable[str], to_status: str, ts: str,
                     **fields: Any) -> bool:
        """Compare-and-set an order's status plus fields. False if it was not in from_status."""
        sources = (from_status,) if isinstance(from_status, str) else tuple(from_status)
        values = {"status": to_status, "updated_at": ts, **fields}
        marks = ", ".join("?" for _ in sources)
        cursor = self.conn.execute(
            f"UPDATE orders SET {_assignments(values, _ORDER_WRITABLE)} "
            f"WHERE order_id = ? AND status IN ({marks})",
            (*values.values(), order_id, *sources),
        )
        return cursor.rowcount == 1

    def count_orders(self, *, side: str, since: str, statuses: Iterable[str], up_to_id: int | None = None) -> int:
        """Orders of one side placed since `since` in these statuses; with up_to_id, only those placed up to it."""
        statuses = tuple(statuses)
        marks = ", ".join("?" for _ in statuses)
        return int(self.scalar(
            f"SELECT COUNT(*) FROM orders WHERE side = ? AND created_at >= ? AND status IN ({marks}) AND order_id <= ?",
            (side, since, *statuses, up_to_id if up_to_id is not None else 2**62)))
