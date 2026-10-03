"""Local web dashboard: the desk's buttons in a browser, served on 127.0.0.1 only.

`python main.py web` starts it. It drives exactly the code the CLI uses (Orchestrator cycles,
approval.approve/reject, Head), so the same policy, halts, and checks apply. Because the page
can approve leads, the server only ever answers you:

* it listens on 127.0.0.1 and refuses requests whose Host or Origin is not this machine, which
  stops DNS-rebinding pages from reaching it;
* every API call must carry the random token embedded in the page, sent as a custom header on
  a JSON request, which other websites can neither read nor forge;
* responses forbid framing and set a strict content security policy, so no other site can
  overlay the page to steal clicks.
"""

import json
import os
import secrets
import subprocess
import sys
import threading
import webbrowser
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import execution
from .approval import ApprovalError, approve, operator_name, reject
from .clock import iso, utcnow
from .constants import LAMPORTS_PER_SOL
from .context import Completer, DeskContext
from .db import DeskDB, Lead, Order, Position, Status
from .handoff import HandoffError
from .orchestrator import Orchestrator
from .policy import Policy
from .prompts import PromptBook
from .roles import Head
from .tools.base import Toolbox

STATIC = Path(__file__).parent / "static"
PAGES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                               "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
MAX_BODY_BYTES = 10_000
DESK_HEARTBEAT_SECONDS = 5


class DashboardError(Exception):
    """A request the desk refused. The message is shown in the page."""

    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.CONFLICT) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Runner:
    """A paper loop running in the background for one mode."""

    stop: threading.Event
    started_at: str
    cycles: int | None  # None: until stopped
    interval_s: float
    first_cycle: int    # the desk's cycle counter when the run began
    next_cycle_at: str | None = None


@dataclass
class Desk:
    """One mode, live or demo: its database, its tools, and its background run, if any."""

    mode: str
    label: str
    db_path: Path
    tools: Toolbox
    cli: str
    policy: Policy  # the demo desk always runs in paper mode
    runner: Runner | None = None
    last_run: dict[str, Any] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


def _symbol(lead: Lead | None) -> str:
    return str(lead.market().get("base_symbol") or "") if lead else ""


def awaiting_view(lead: Lead) -> dict[str, Any]:
    plan = lead.quote()
    return {
        "lead": lead.ref, "mint": lead.mint, "pair": lead.pair, "symbol": _symbol(lead),
        "size_sol": plan.get("size_sol"), "impact_pct": plan.get("price_impact_pct"),
        "sell_impact_pct": (plan.get("sell_check") or {}).get("price_impact_pct"),
        "route": plan.get("route") or [], "expires_at": plan.get("expires_at"),
        "liquidity_usd": lead.liquidity_usd, "age_minutes": lead.age_minutes,
        "top10_pct": lead.top10_holder_pct, "risk_notes": lead.risk_notes,
    }


def position_view(position: Position, lead: Lead | None) -> dict[str, Any]:
    return {
        "lead": f"LEAD-{position.lead_id}", "mint": position.mint, "pair": lead.pair if lead else "",
        "symbol": _symbol(lead), "mode": position.mode, "size_sol": position.size_sol, "entry": position.paper_entry,
        "unrealized": position.unrealized, "opened_at": position.opened_at,
        "last_checked_at": position.last_checked_at, "selling": position.sell_order_id is not None,
    }


def order_view(order: Order) -> dict[str, Any]:
    if order.side == "buy":
        amount = f"{int(order.amount) / LAMPORTS_PER_SOL:g} SOL"
    else:
        amount = f"{int(order.amount)} raw tokens"
    return {
        "id": order.order_id, "lead": f"LEAD-{order.lead_id}", "side": order.side, "mode": order.mode,
        "amount": amount, "status": order.status, "created_at": order.created_at,
        "tx_sig": order.tx_sig, "detail": order.detail or order.reason or "",
    }


def lead_view(lead: Lead, position: Position | None) -> dict[str, Any]:
    if position is not None and not position.is_open:
        note = f"exit: {position.exit_reason} (realized {position.realized_sol or 0:+.4f} SOL, {position.mode})"
    else:
        note = lead.reject_reason or lead.risk_notes or ""
    return {
        "lead": lead.ref, "mint": lead.mint, "pair": lead.pair, "symbol": _symbol(lead),
        "status": lead.status, "note": note, "liquidity_usd": lead.liquidity_usd,
        "age_minutes": lead.age_minutes, "discovered_at": lead.discovered_at,
    }


def event_view(row: Any) -> dict[str, Any]:
    payload = json.loads(row["payload"] or "{}")
    text = payload.get("line") or " ".join(
        f"{key}={value}" for key, value in payload.items() if value is None or isinstance(value, str | int | float))
    return {
        "time": row["timestamp"], "lead": None if row["lead_id"] is None else f"LEAD-{row['lead_id']}",
        "role": row["role"], "action": row["action"], "text": text[:400],
    }


SIGNER_SCRIPT = Path(__file__).resolve().parents[2] / "signer.py"  # next to main.py


class SignerProcess:
    """The signer, started as its own process: the key is loaded there, never in this one."""

    def __init__(self, root: Path, db_path: Path) -> None:
        self.root = root
        self.db_path = db_path
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        if self.running:
            return
        # The signer needs no LLM settings; nothing secret is passed (this process holds none).
        environment = {name: value for name, value in os.environ.items() if not name.startswith("LLM_")}
        self.process = subprocess.Popen(
            [sys.executable, str(SIGNER_SCRIPT), "--root", str(self.root), "--db", str(self.db_path),
             "run", "--supervised"], env=environment)

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def view(self) -> dict[str, Any]:
        if self.process is None:
            return {"started": False, "running": False, "exit_code": None}
        return {"started": True, "running": self.running, "exit_code": self.process.poll()}

    def stop(self) -> None:
        if self.running:
            assert self.process is not None
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()


class DashboardApp:
    """Everything the page can do, independent of HTTP."""

    def __init__(self, *, prompts: PromptBook, llm: Completer | None, desks: dict[str, Desk],
                 initial_mode: str, echo: Callable[[str], None] = print,
                 signer: SignerProcess | None = None) -> None:
        self.prompts = prompts
        self.llm = llm
        self.desks = desks
        self.initial_mode = initial_mode
        self.echo = echo
        self.signer = signer
        self.token = secrets.token_urlsafe(32)
        self._stopping = threading.Event()

    def start_background(self) -> None:
        """Keep a heartbeat in the live database so a supervised signer knows the desk is here."""
        def beat() -> None:
            while not self._stopping.wait(DESK_HEARTBEAT_SECONDS):
                self._desk_heartbeat()

        self._desk_heartbeat()
        threading.Thread(target=beat, name="desk-heartbeat", daemon=True).start()
        live = self.desks.get("live")
        if self.signer is not None and live is not None and self._signer_needed(live):
            self.signer.start()

    def _signer_needed(self, desk: Desk) -> bool:
        """Trading needs the signer, and so does selling live positions after a switch back to paper."""
        if desk.policy.trading_mode != "paper":
            return True
        with self.context(desk) as ctx:
            return ctx.db.count_open_positions("live") > 0

    def _desk_heartbeat(self) -> None:
        live = self.desks.get("live")
        if live is not None:
            db = DeskDB(live.db_path)
            try:
                db.set_state("desk_heartbeat_at", iso(utcnow()))
            finally:
                db.close()

    def desk(self, mode: object) -> Desk:
        if not isinstance(mode, str) or mode not in self.desks:
            raise DashboardError(f"unknown mode {mode!r}", HTTPStatus.BAD_REQUEST)
        return self.desks[mode]

    @contextmanager
    def context(self, desk: Desk, *, with_tools: bool = False,
                echo: Callable[[str], None] | None = None) -> Iterator[DeskContext]:
        """A DeskContext with its own SQLite connection, for use on the current thread only."""
        ctx = DeskContext(
            policy=desk.policy, db=DeskDB(desk.db_path), prompts=self.prompts,
            tools=desk.tools if with_tools else None, llm=self.llm, cli=desk.cli,
            echo=echo or (lambda line: self.echo(f"[{desk.mode}] {line}")),
        )
        try:
            yield ctx
        finally:
            ctx.db.close()

    def state(self, mode: object) -> dict[str, Any]:
        desk = self.desk(mode)
        with self.context(desk) as ctx:
            db = ctx.db
            positions = {position.lead_id: position for position in db.positions()}
            cycle = int(db.get_state("cycles", "0") or 0)
            trading = ctx.mode != "paper" or db.count_open_positions("live") > 0  # live positions still sell
            status = execution.signer_status(db, ctx.now()) if trading else None
            body: dict[str, Any] = {
                "mode": desk.mode, "label": desk.label, "db": str(desk.db_path), "trading_mode": ctx.mode,
                "llm": "on" if self.llm is not None else "off", "server_time": ctx.ts(),
                "halted": db.is_halted(), "halt_reason": db.halt_reason(),
                "equity": {"total": ctx.equity_sol(), "start": desk.policy.paper_equity_sol,
                           "realized": db.realized_total(ctx.mode), "unrealized": db.unrealized_total(ctx.mode)},
                "auto": {"approve": desk.policy.auto_approve, "decider": desk.policy.decider,
                         "llm": self.llm is not None},
                "limits": {"max_trade_sol": desk.policy.max_trade_sol, "buys_today": execution.buys_today(ctx),
                           "max_buys_per_day": desk.policy.max_buys_per_day,
                           "failed_sends_today": int(db.get_state(f"failed_sends:{ctx.ts()[:10]}", "0") or 0),
                           "max_failed_sends": desk.policy.max_failed_sends},
                "signer": None if status is None else {
                    "online": status.online, "mode": status.mode, "wallet": status.wallet,
                    "wallet_sol": status.wallet_sol, "heartbeat_at": status.heartbeat_at, "error": status.error,
                    "process": self.signer.view() if self.signer is not None and desk.mode == "live" else None,
                },
                "orders": [order_view(order) for order in db.recent_orders(15)] if trading else [],
                "awaiting": [awaiting_view(lead) for lead in db.leads(Status.AWAITING)],
                "positions": [position_view(position, db.get_lead(lead_id))
                              for lead_id, position in positions.items() if position.is_open],
                "leads": [lead_view(lead, positions.get(lead.lead_id)) for lead in reversed(db.recent_leads(40))],
                "events": [event_view(row) for row in reversed(db.events(limit=80))],
            }
        runner = desk.runner
        body["runner"] = None if runner is None else {
            "started_at": runner.started_at, "cycles": runner.cycles, "interval_s": runner.interval_s,
            "cycle": max(cycle - runner.first_cycle, 0), "next_cycle_at": runner.next_cycle_at,
            "stopping": runner.stop.is_set(),
        }
        body["running"] = {name: other.runner is not None for name, other in self.desks.items()}
        body["last_run"] = desk.last_run
        policy = desk.policy
        body["policy"] = {
            "min_liquidity_usd": policy.min_liquidity_usd, "min_token_age_minutes": policy.min_token_age_minutes,
            "max_price_impact_pct": policy.max_price_impact_pct, "max_position_pct": policy.max_position_pct,
            "max_open_positions": policy.max_open_positions, "approval_ttl_minutes": policy.approval_ttl_minutes,
            "daily_loss_halt_pct": policy.daily_loss_halt_pct,
        }
        return body

    def run(self, mode: object, cycles: object, interval_s: object) -> str:
        desk = self.desk(mode)
        if cycles is not None and (isinstance(cycles, bool) or not isinstance(cycles, int) or not 1 <= cycles <= 1000):
            raise DashboardError("cycles must be a whole number from 1 to 1000, or null", HTTPStatus.BAD_REQUEST)
        if isinstance(interval_s, bool) or not isinstance(interval_s, int | float) or not 5 <= interval_s <= 3600:
            raise DashboardError("the interval must be 5 to 3600 seconds", HTTPStatus.BAD_REQUEST)
        with desk.lock:
            if desk.runner is not None:
                raise DashboardError(f"a {desk.mode} run is already in progress")
            with self.context(desk) as ctx:
                first_cycle = int(ctx.db.get_state("cycles", "0") or 0)
            runner = Runner(threading.Event(), iso(utcnow()), cycles, float(interval_s), first_cycle)
            desk.runner = runner
        threading.Thread(target=self._run, args=(desk, runner), name=f"desk-{desk.mode}", daemon=True).start()
        if cycles == 1:
            return f"{desk.mode}: running 1 cycle"
        if cycles is None:
            return f"{desk.mode}: auto-run started, every {interval_s:g} s until you press Stop"
        return f"{desk.mode}: running {cycles} cycles, {interval_s:g} s apart"

    def _run(self, desk: Desk, runner: Runner) -> None:
        def wait(seconds: float) -> bool:
            runner.next_cycle_at = iso(utcnow() + timedelta(seconds=seconds))
            stopped = runner.stop.wait(seconds)
            runner.next_cycle_at = None
            return stopped

        try:
            with self.context(desk, with_tools=True) as ctx:
                summary = Orchestrator(ctx).loop(runner.cycles, runner.interval_s, wait=wait)
            desk.last_run = {"summary": summary, "finished_at": iso(utcnow()), "error": None}
        except Exception as exc:  # show the failure in the page rather than dying silently
            desk.last_run = {"summary": "", "finished_at": iso(utcnow()), "error": f"{type(exc).__name__}: {exc}"}
            self.echo(f"[{desk.mode}] run failed: {type(exc).__name__}: {exc}")
        finally:
            with desk.lock:
                desk.runner = None

    def stop(self, mode: object) -> str:
        desk = self.desk(mode)
        runner = desk.runner
        if runner is None:
            raise DashboardError(f"no {desk.mode} run is in progress")
        runner.stop.set()
        return f"{desk.mode}: stopping after the current cycle"

    def _decide(self, mode: object, ref: str, decide: Callable[..., object]) -> str:
        desk = self.desk(mode)
        lines: list[str] = []

        def echo(line: str) -> None:
            lines.append(line)
            self.echo(f"[{desk.mode}] {line}")

        with self.context(desk, echo=echo) as ctx:
            decide(ctx, ref, by=f"{operator_name()}@web")
        return lines[-1] if lines else ref

    def approve(self, mode: object, ref: str) -> str:
        return self._decide(mode, ref, approve)

    def reject(self, mode: object, ref: str) -> str:
        return self._decide(mode, ref, reject)

    def clear_halt(self, mode: object, reason: str) -> str:
        desk = self.desk(mode)
        if not reason.strip():
            raise DashboardError("say why it is safe to resume; the reason goes in the audit log",
                                 HTTPStatus.BAD_REQUEST)
        with self.context(desk) as ctx:
            if not ctx.db.is_halted():
                raise DashboardError("the desk is not halted")
            previous = ctx.db.halt_reason()
            Head(ctx).clear_halt(by=f"{operator_name()}@web", note=reason.strip())
        return f"halt cleared (was: {previous})"

    def halt(self, mode: object, reason: str) -> str:
        """Stop new buys at once. Anyone at the keyboard may halt; clearing needs a reason."""
        desk = self.desk(mode)
        with self.context(desk) as ctx:
            if ctx.db.is_halted():
                return f"already halted ({ctx.db.halt_reason()})"
            Head(ctx).halt(f"halted from the dashboard by {operator_name()}: {reason.strip() or 'no reason given'}")
        return f"{desk.mode}: halted; no new buys until you clear it. Open positions are still managed."

    def start_signer(self, mode: object) -> str:
        desk = self.desk(mode)
        if self.signer is None or desk.mode != "live":
            raise DashboardError("only the live tab has a signer to start")
        if not self._signer_needed(desk):
            raise DashboardError("trading_mode is paper and no live position is open; the signer has nothing to do")
        if self.signer.running:
            return "the signer is already running"
        self.signer.start()
        return "signer starting; it reports in within a few seconds"

    def summary(self, mode: object) -> str:
        with self.context(self.desk(mode)) as ctx:
            return Head(ctx).daily_summary(utcnow().date())

    def shutdown(self) -> None:
        self._stopping.set()
        for desk in self.desks.values():
            if desk.runner is not None:
                desk.runner.stop.set()
        if self.signer is not None:
            self.signer.stop()


class Handler(BaseHTTPRequestHandler):
    server: "DashboardServer"
    server_version = "TradingDesk"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:
        pass  # the console is for desk output; the page shows request errors

    def do_GET(self) -> None:
        if not self._local():
            return
        url = urlsplit(self.path)
        app = self.server.app
        if url.path.startswith("/api/"):
            if url.path != "/api/state":
                return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            if self._authorized():
                mode = (parse_qs(url.query).get("mode") or [app.initial_mode])[0]
                self._call(lambda: app.state(mode))
            return
        page = PAGES.get(url.path)
        if page is None:
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        name, content_type = page
        body = (STATIC / name).read_bytes()
        if name == "index.html":
            body = body.replace(b"{{TOKEN}}", app.token.encode()).replace(b"{{MODE}}", app.initial_mode.encode())
        self._send(HTTPStatus.OK, body, content_type)

    def do_POST(self) -> None:
        if not self._local() or not self._authorized():
            return
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "send a JSON body"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY_BYTES:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "bad request size"})
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "send a JSON object"})
        app, mode = self.server.app, data.get("mode")
        actions: dict[str, Callable[[], dict[str, Any]]] = {
            "/api/run": lambda: {"message": app.run(mode, data.get("cycles"), data.get("interval", 60))},
            "/api/stop": lambda: {"message": app.stop(mode)},
            "/api/approve": lambda: {"message": app.approve(mode, str(data.get("lead", "")))},
            "/api/reject": lambda: {"message": app.reject(mode, str(data.get("lead", "")))},
            "/api/clear-halt": lambda: {"message": app.clear_halt(mode, str(data.get("reason", "")))},
            "/api/halt": lambda: {"message": app.halt(mode, str(data.get("reason", "")))},
            "/api/signer/start": lambda: {"message": app.start_signer(mode)},
            "/api/summary": lambda: {"text": app.summary(mode)},
        }
        action = actions.get(urlsplit(self.path).path)
        if action is None:
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        self._call(action)

    def _local(self) -> bool:
        """Answer only pages served from this machine (blocks DNS rebinding and other origins)."""
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = (self.headers.get("Host") or "").lower()
        origin = self.headers.get("Origin")
        if host in hosts and (origin is None or origin.lower() in {f"http://{name}" for name in hosts}):
            return True
        self._json(HTTPStatus.FORBIDDEN, {"error": f"this dashboard only answers http://127.0.0.1:{port}"})
        return False

    def _authorized(self) -> bool:
        sent = (self.headers.get("X-Desk-Token") or "").encode()
        if secrets.compare_digest(sent, self.server.app.token.encode()):
            return True
        self._json(HTTPStatus.FORBIDDEN, {"error": "missing or wrong dashboard token; reload the page"})
        return False

    def _call(self, action: Callable[[], dict[str, Any]]) -> None:
        try:
            result = action()
        except DashboardError as exc:
            return self._json(exc.status, {"error": str(exc)})
        except (ApprovalError, HandoffError) as exc:
            return self._json(HTTPStatus.CONFLICT, {"error": str(exc)})
        except Exception as exc:  # report it and keep serving
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(exc).__name__}: {exc}"})
        self._json(HTTPStatus.OK, result)

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        self._send(status, json.dumps(payload, default=str).encode(), "application/json")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second server share the port; refuse that instead.
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, app: DashboardApp, port: int) -> None:
        self.app = app
        super().__init__(("127.0.0.1", port), Handler)


def serve(app: DashboardApp, *, port: int, open_browser: bool, autorun_seconds: float | None = None) -> None:
    try:
        server = DashboardServer(app, port)
    except OSError as exc:
        raise DashboardError(f"cannot listen on 127.0.0.1:{port} ({exc}); try another --port") from exc
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Dashboard: {url}  (local only; press Ctrl+C here to stop)", flush=True)
    app.start_background()
    try:
        if autorun_seconds is not None:  # the loop of the tab the page opens on
            print(app.run(app.initial_mode, None, autorun_seconds), flush=True)
        if open_browser:
            webbrowser.open(url)
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nstopping the dashboard", flush=True)
    finally:
        app.shutdown()
        server.server_close()
