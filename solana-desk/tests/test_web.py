"""The local dashboard: same pipeline behind buttons, answering only this machine and this page."""

import json
import os
import shutil
import subprocess
import sys
import threading
import time

import httpx
import pytest

from desk.clock import iso, utcnow
from desk.db import DeskDB
from desk.prompts import PromptBook
from desk.tools.fixture import TokenSpec, fixture_toolbox, load_specs
from desk.web import DashboardApp, DashboardServer, Desk

from conftest import ROOT


@pytest.fixture
def dashboard(tmp_path, policy):
    desks = {
        "demo": Desk("demo", "Synthetic demo tokens", tmp_path / "demo.sqlite3",
                     fixture_toolbox(load_specs(ROOT / "fixtures" / "demo_market.json"), utcnow()), "python main.py --demo"),
        "live": Desk("live", "Live stand-in (fixtures, for tests)", tmp_path / "live.sqlite3",
                     fixture_toolbox([TokenSpec("ELSEWHERE")], utcnow()), "python main.py"),
    }
    app = DashboardApp(policy=policy, prompts=PromptBook(ROOT / "prompts"), llm=None, desks=desks,
                       initial_mode="demo", echo=lambda _line: None)
    server = DashboardServer(app, 0)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", timeout=10, trust_env=False) as client:
        yield app, client
    app.shutdown()
    server.shutdown()
    server.server_close()


def call(client, app, path, body=None, **headers):
    """A request as the page makes it: with the token, and JSON for POSTs."""
    headers = {"X-Desk-Token": app.token} | headers
    if body is None:
        return client.get(path, headers=headers)
    return client.post(path, headers=headers | {"Content-Type": "application/json"}, content=json.dumps(body))


def wait_until(condition, timeout=15.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


def test_page_embeds_its_token_and_forbids_framing(dashboard):
    app, client = dashboard
    page = client.get("/")
    assert page.status_code == 200
    assert f'content="{app.token}"' in page.text and 'data-initial-mode="demo"' in page.text
    assert page.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
    assert "script-src 'self'" in page.headers["Content-Security-Policy"]
    for asset in ("/app.js", "/app.css"):
        assert client.get(asset).status_code == 200


def test_hidden_elements_stay_hidden():
    """Class rules that set display (the halt banner is a flex box) must not override [hidden]:
    a bug here showed "Desk halted" on a desk that was not halted."""
    css = (ROOT / "src" / "desk" / "static" / "app.css").read_text()
    assert "[hidden] { display: none !important; }" in css


def test_other_hosts_and_origins_are_refused(dashboard):
    app, client = dashboard
    assert client.get("/", headers={"Host": "attacker.example"}).status_code == 403  # DNS rebinding
    assert call(client, app, "/api/state?mode=demo", Host="attacker.example").status_code == 403
    run = {"mode": "demo", "cycles": 1, "interval": 60}
    assert call(client, app, "/api/run", run, Origin="https://attacker.example").status_code == 403
    assert call(client, app, "/api/run", run, Origin="null").status_code == 403
    assert app.desks["demo"].runner is None


def test_api_calls_need_the_page_token_and_json(dashboard):
    app, client = dashboard
    assert client.get("/api/state?mode=demo").status_code == 403
    wrong = client.post("/api/run", headers={"X-Desk-Token": "guess", "Content-Type": "application/json"},
                        content=json.dumps({"mode": "demo", "cycles": 1, "interval": 60}))
    assert wrong.status_code == 403
    form_post = client.post("/api/run", headers={"X-Desk-Token": app.token}, data={"mode": "demo", "cycles": "1"})
    assert form_post.status_code == 415
    assert app.desks["demo"].runner is None
    for path in ("/static/../web.py", "/api/nothing", "/etc/passwd"):
        assert call(client, app, path).status_code == 404


def test_buttons_run_the_same_pipeline_and_gate(dashboard):
    app, client = dashboard
    started = call(client, app, "/api/run", {"mode": "demo", "cycles": 1, "interval": 60})
    assert started.status_code == 200, started.text
    wait_until(lambda: app.desks["demo"].runner is None)

    state = call(client, app, "/api/state?mode=demo").json()
    assert [lead["lead"] for lead in state["awaiting"]] == ["LEAD-1", "LEAD-6"]
    assert "TOTAL: scanned 12 | rejected 10 | awaiting approval 2" in state["last_run"]["summary"]

    approved = call(client, app, "/api/approve", {"mode": "demo", "lead": "LEAD-1"})
    assert approved.status_code == 200 and "| approval | approve |" in approved.json()["message"]
    assert call(client, app, "/api/reject", {"mode": "demo", "lead": "LEAD-6"}).status_code == 200

    state = call(client, app, "/api/state?mode=demo").json()
    assert state["awaiting"] == []
    assert [position["lead"] for position in state["positions"]] == ["LEAD-1"]
    statuses = {lead["lead"]: lead["status"] for lead in state["leads"]}
    assert (statuses["LEAD-1"], statuses["LEAD-6"]) == ("paper_filled", "rejected")
    assert any(event["action"] == "approve" and "@web" in event["text"] for event in state["events"])
    assert call(client, app, "/api/state?mode=live").json()["leads"] == [], "modes keep separate databases"


def test_refusals_come_back_as_messages(dashboard):
    app, client = dashboard
    missing = call(client, app, "/api/approve", {"mode": "demo", "lead": "LEAD-99"})
    assert missing.status_code == 409 and "does not exist" in missing.json()["error"]
    assert call(client, app, "/api/approve", {"mode": "real-money", "lead": "LEAD-1"}).status_code == 400
    assert call(client, app, "/api/run", {"mode": "demo", "cycles": 1, "interval": 1}).status_code == 400
    assert call(client, app, "/api/run", {"mode": "demo", "cycles": 0, "interval": 60}).status_code == 400
    assert call(client, app, "/api/stop", {"mode": "demo"}).status_code == 409
    assert call(client, app, "/api/clear-halt", {"mode": "demo", "reason": "fine"}).status_code == 409


def test_halt_shows_and_needs_a_reason_to_clear(dashboard):
    app, client = dashboard
    db = DeskDB(app.desks["demo"].db_path)
    db.set_halted("3 failed sends today >= 3", iso(utcnow()))
    db.close()
    state = call(client, app, "/api/state?mode=demo").json()
    assert state["halted"] and state["halt_reason"] == "3 failed sends today >= 3"

    assert call(client, app, "/api/clear-halt", {"mode": "demo", "reason": "   "}).status_code == 400
    cleared = call(client, app, "/api/clear-halt", {"mode": "demo", "reason": "checked the sender"})
    assert cleared.status_code == 200 and "halt cleared" in cleared.json()["message"]
    assert not call(client, app, "/api/state?mode=demo").json()["halted"]


def test_auto_run_stops_when_asked(dashboard):
    app, client = dashboard
    started = call(client, app, "/api/run", {"mode": "demo", "cycles": None, "interval": 3600})
    assert started.status_code == 200
    wait_until(lambda: (runner := app.desks["demo"].runner) is not None and runner.next_cycle_at is not None)
    assert call(client, app, "/api/run", {"mode": "demo", "cycles": 1, "interval": 60}).status_code == 409
    assert call(client, app, "/api/state?mode=demo").json()["running"] == {"demo": True, "live": False}

    assert call(client, app, "/api/stop", {"mode": "demo"}).status_code == 200
    wait_until(lambda: app.desks["demo"].runner is None, timeout=5)  # well before the 3600 s interval
    assert "1 cycle(s)" in app.desks["demo"].last_run["summary"]


def test_cli_web_command_serves_the_dashboard(tmp_path):
    root = tmp_path / "root"
    for name in ("config", "prompts", "fixtures"):
        shutil.copytree(ROOT / name, root / name)
    env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if name in os.environ}
    command = [sys.executable, str(ROOT / "main.py"), "--root", str(root), "--demo",
               "--db", str(tmp_path / "d.sqlite3"), "web", "--port", "0", "--no-browser"]
    with subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True) as process:
        try:
            first_line: list[str] = []
            reader = threading.Thread(target=lambda: first_line.append(process.stdout.readline()), daemon=True)
            reader.start()
            reader.join(timeout=30)
            assert first_line and first_line[0].startswith("Dashboard: http://127.0.0.1:"), first_line
            with httpx.Client(timeout=10, trust_env=False) as client:
                page = client.get(first_line[0].split()[1])
            assert page.status_code == 200 and "Paper desk" in page.text
        finally:
            process.terminate()
