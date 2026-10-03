"use strict";

// The server rejects API calls that do not carry this page's token.
const TOKEN = document.querySelector('meta[name="desk-token"]').content;
const $ = (id) => document.getElementById(id);

let mode = document.body.dataset.initialMode === "demo" ? "demo" : "live";
let state = null;
let clockOffset = 0;
let pollTimer = null;
let busy = false;
let rendered = {};

// Every value from the desk is inserted as text, never as HTML: token names come from outside.
function el(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value == null || value === false) continue;
    if (name === "class") node.className = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child != null && child !== false) node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

const missing = (value) => value == null || Number.isNaN(Number(value));
const fixed = (value, digits) => (missing(value) ? "–" : Number(value).toFixed(digits));
const sol = (value) => (missing(value) ? "–" : `${fixed(value, 4)} SOL`);
const signedSol = (value) => (missing(value) ? "–" : `${value > 0 ? "+" : ""}${fixed(value, 4)} SOL`);
const pct = (value) => (missing(value) ? "–" : `${fixed(value, 2)}%`);
const usd = (value) => (missing(value) ? "–" : `$${Math.round(value).toLocaleString("en-US")}`);
const tone = (value) => (value > 0 ? "pos" : value < 0 ? "neg" : "");
const timeOf = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour12: false }) : "–");
const MODE_LABEL = { paper: "Paper", dry_run: "Dry run", live: "Live" };
const APPROVE_HINT = {
  paper: "Approve opens a paper position at the stored quote. Nothing is signed or sent.",
  dry_run: "Approve gives the signer a buy order; it builds, signs and simulates it on Solana. Nothing is sent.",
  live: "Approve gives the signer a real buy order for the size shown.",
};
const DISCLAIMER = {
  paper: "Paper mode: nothing is signed or sent. Paper fills use quoted prices and ignore fees, latency, and failed sends; nothing here shows the strategy is profitable.",
  dry_run: "Dry run: real swaps are built, signed and simulated on Solana but never sent. Simulated fills are not evidence that the strategy is profitable.",
  live: "Live: trades spend real SOL from the trading wallet. Past fills are not evidence that the strategy is profitable.",
};

function age(minutes) {
  if (missing(minutes)) return "–";
  if (minutes < 90) return `${Math.round(minutes)}m`;
  if (minutes < 2880) return `${(minutes / 60).toFixed(1)}h`;
  return `${(minutes / 1440).toFixed(1)}d`;
}

function countdown(iso) {
  const ms = Date.parse(iso) - (Date.now() + clockOffset);
  if (Number.isNaN(ms)) return "–";
  if (ms <= 0) return "now";
  const seconds = Math.ceil(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

async function api(path, body) {
  const init = { headers: { "X-Desk-Token": TOKEN }, cache: "no-store" };
  if (body !== undefined) {
    init.method = "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify({ mode, ...body });
  }
  const response = await fetch(path, init);
  let data = {};
  try {
    data = await response.json();
  } catch (_) {
    // keep the empty object; the status says what went wrong
  }
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

async function refresh() {
  clearTimeout(pollTimer);
  try {
    state = await api(`/api/state?mode=${encodeURIComponent(mode)}`);
    clockOffset = Date.parse(state.server_time) - Date.now();
    $("conn").hidden = true;
    render();
  } catch (error) {
    $("conn").hidden = false;
    $("conn").textContent = `Can't reach the desk (${error.message}). Is "python main.py web" still running?`;
  }
  pollTimer = setTimeout(refresh, state && state.runner ? 2000 : 5000);
}

function toast(message, kind) {
  const box = $("toast");
  box.textContent = message;
  box.className = `toast ${kind}`;
  box.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { box.hidden = true; }, kind === "error" ? 10000 : 6000);
}

async function act(path, body) {
  if (busy) return null;
  busy = true;
  setButtonsDisabled(true);
  try {
    const result = await api(path, body);
    if (result.message) toast(result.message, "ok");
    return result;
  } catch (error) {
    toast(error.message, "error");
    return null;
  } finally {
    busy = false;
    setButtonsDisabled(false);
    refresh();
  }
}

function setButtonsDisabled(disabled) {
  for (const button of document.querySelectorAll("button.approve, button.reject, #clear-halt, #daily")) {
    button.disabled = disabled;
  }
  if (state) drawRun();
}

async function decide(kind, lead) {
  const name = lead.symbol ? `${lead.symbol} (${lead.mint})` : lead.mint;
  const question = kind === "approve"
    ? `Approve ${lead.lead}, ${name}?\n\nThis opens a PAPER position of ${lead.size_sol} SOL at the stored quote `
      + `(price impact ${pct(lead.impact_pct)}). Nothing is signed or sent.`
    : `Reject ${lead.lead}, ${name}?`;
  if (window.confirm(question)) await act(`/api/${kind}`, { lead: lead.lead });
}

function copyText(text) {
  navigator.clipboard.writeText(text).then(
    () => toast("Mint address copied", "ok"),
    () => toast("Could not copy; select the address in the tooltip instead", "error"),
  );
}

function shortAddress(address) {
  return `${address.slice(0, 4)}…${address.slice(-4)}`;
}

function tokenCell(item) {
  const cell = el("div", { class: "token" },
    el("span", { class: "symbol" }, item.symbol || "?"),
    el("span", { class: "mint mono", title: item.mint }, `${item.mint.slice(0, 4)}…${item.mint.slice(-4)}`),
    el("button", { class: "link", type: "button", title: "Copy the full mint address", onclick: () => copyText(item.mint) }, "copy"));
  if (mode === "live") {
    cell.append(el("a", { href: `https://solscan.io/token/${encodeURIComponent(item.mint)}`, target: "_blank", rel: "noopener noreferrer" }, "solscan"));
    if (item.pair) {
      cell.append(el("a", { href: `https://dexscreener.com/solana/${encodeURIComponent(item.pair)}`, target: "_blank", rel: "noopener noreferrer" }, "chart"));
    }
  }
  return cell;
}

// Redraw a section only when its data changed, so buttons are not replaced under the pointer.
function section(key, data, draw) {
  const signature = JSON.stringify(data);
  if (rendered[key] === signature) return;
  rendered[key] = signature;
  draw(data);
}

function fillTable(name, rows) {
  $(`${name}-body`).replaceChildren(...rows);
  $(`${name}-table`).hidden = rows.length === 0;
  $(`${name}-empty`).hidden = rows.length > 0;
}

function render() {
  for (const tab of document.querySelectorAll(".tab")) {
    const active = tab.dataset.mode === mode;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.querySelector(".dot").hidden = !state.running[tab.dataset.mode];
  }
  section("source", [state.mode, state.label, state.llm, state.db, state.policy], drawSource);
  section("tradingMode", [state.trading_mode, state.limits], drawTradingMode);
  section("halt", [state.halted, state.halt_reason], drawHalt);
  section("equity", [state.equity, state.trading_mode], drawEquity);
  section("wallet", [state.signer, state.trading_mode], drawWallet);
  section("auto", [state.auto, state.trading_mode], drawAuto);
  drawRun();
  section("orders", [state.orders, state.limits, state.trading_mode], drawOrders);
  section("awaiting", state.awaiting, drawAwaiting);
  section("positions", state.positions, drawPositions);
  section("leads", state.leads, drawLeads);
  section("events", state.events, drawEvents);
  section("lastRun", state.last_run, drawLastRun);
  tick();
}

function drawSource([currentMode, label, llm, db, policy]) {
  $("source").replaceChildren(
    el("span", { class: `badge ${currentMode}` }, currentMode === "live" ? "LIVE DATA" : "DEMO DATA"),
    `${label} · LLM ${llm === "on" ? "on" : "off (coded rules only)"}`);
  $("db").textContent = db;
  $("policy").textContent = `Policy: liquidity ≥ ${usd(policy.min_liquidity_usd)} · age ≥ ${policy.min_token_age_minutes}m · `
    + `price impact ≤ ${policy.max_price_impact_pct}% · size ${policy.max_position_pct}% of equity · `
    + `max ${policy.max_open_positions} open positions · quotes expire after ${policy.approval_ttl_minutes} min · `
    + `halt at ${policy.daily_loss_halt_pct}% daily loss`;
}

function drawHalt([halted, reason]) {
  $("halt").hidden = !halted;
  $("halt-reason").textContent = reason || "";
}

function drawEquity([equity, tradingMode]) {
  $("equity").textContent = equity.total == null ? "unknown" : sol(equity.total);
  const first = tradingMode === "paper"
    ? el("div", {}, el("span", {}, "Paper bankroll"), el("span", {}, sol(equity.start)))
    : el("div", {}, el("span", {}, "Basis"), el("span", {}, tradingMode === "live" ? "wallet + positions" : "wallet + simulated"));
  $("equity-detail").replaceChildren(first,
    el("div", {}, el("span", {}, "Realized"), el("span", { class: tone(equity.realized) }, signedSol(equity.realized))),
    el("div", {}, el("span", {}, "Unrealized"), el("span", { class: tone(equity.unrealized) }, signedSol(equity.unrealized))));
  if (equity.total == null) {
    $("equity-detail").append(el("p", { class: "warn-text" }, "Waiting for the signer to report the wallet balance."));
  }
}

function drawTradingMode([tradingMode, limits]) {
  const badge = $("mode-badge");
  badge.textContent = MODE_LABEL[tradingMode] || tradingMode;
  badge.className = `pill mode-${tradingMode}`;
  $("approve-hint").textContent = APPROVE_HINT[tradingMode] || "";
  $("disclaimer").textContent = DISCLAIMER[tradingMode] || "";
  const banner = $("mode-banner");
  banner.hidden = tradingMode === "paper";
  banner.className = `banner ${tradingMode}`;
  if (tradingMode === "dry_run") {
    banner.replaceChildren(el("strong", {}, "Dry run. "),
      "Real quotes; real swap transactions are built, signed and simulated on Solana, but nothing is sent. ",
      el("p", {}, "When the log looks right, set ", el("code", {}, "trading_mode: live"), " in ",
        el("code", {}, "config/policy.yaml"), " and restart the dashboard."));
  } else if (tradingMode === "live") {
    banner.replaceChildren(el("strong", {}, "Live. "),
      `Trades spend real SOL from the trading wallet, at most ${limits.max_trade_sol} SOL each. `,
      el("p", {}, "Halt trading now stops new buys at once; open positions are still sold on their exit triggers."));
  }
}

function drawWallet([signer, tradingMode]) {
  const card = $("wallet-card");
  card.hidden = signer == null;
  $("top-row").classList.toggle("no-wallet", signer == null);
  if (signer == null) return;
  const line = (label, value, cls = "") => el("div", { class: "wallet-line" }, el("span", { class: "muted" }, label),
    el("span", { class: `value ${cls}` }, value));
  const rows = [];
  if (signer.wallet) {
    const address = el("span", {}, el("span", { class: "mono", title: signer.wallet }, shortAddress(signer.wallet)), " ",
      el("button", { class: "link", type: "button", onclick: () => copyText(signer.wallet) }, "copy"), " ",
      el("a", { href: `https://solscan.io/account/${encodeURIComponent(signer.wallet)}`, target: "_blank", rel: "noopener noreferrer" }, "solscan"));
    rows.push(line("Address", address), line("Balance", signer.wallet_sol == null ? "–" : sol(signer.wallet_sol)));
  }
  const process = signer.process || {};
  let health;
  if (signer.online) health = ["online, " + (MODE_LABEL[signer.mode] || signer.mode || "?"), "good-text"];
  else if (process.started && !process.running) health = [`stopped (exit code ${process.exit_code}); see the terminal`, "bad-text"];
  else if (process.running) health = ["starting…", "warn-text"];
  else health = ["not running", "bad-text"];
  rows.push(line("Signer", health[0], health[1]));
  if (signer.online && signer.mode !== tradingMode) {
    rows.push(el("p", { class: "bad-text" }, `The signer runs in ${signer.mode} mode but the policy says ${tradingMode}. Restart the dashboard.`));
  }
  if (signer.error) rows.push(el("p", { class: "bad-text" }, signer.error));
  if (!signer.online) {
    rows.push(el("ol", { class: "setup" },
      el("li", {}, "Install: ", el("code", {}, 'pip install -e ".[live]"')),
      el("li", {}, "Create the wallet: ", el("code", {}, "python signer.py init")),
      el("li", {}, "Send it a small amount of SOL from your main wallet."),
      el("li", {}, "Then ", el("button", { class: "link", type: "button", onclick: () => act("/api/signer/start", {}) }, "start the signer"),
        " or restart the dashboard.")));
  }
  $("wallet").replaceChildren(...rows);
}

function drawAuto([auto, tradingMode]) {
  const box = $("auto-status");
  if (!auto.approve) {
    box.replaceChildren("Auto-approve is off: approve or reject staged trades below.");
    return;
  }
  const who = auto.decider === "ai" ? "the AI picks among leads that passed every check" : "every lead that passes every check is bought";
  const parts = [`Auto-approve is on: ${who}, within your limits (${MODE_LABEL[tradingMode] || tradingMode}).`];
  if (auto.decider === "ai" && !auto.llm) {
    parts.push(" ", el("span", { class: "bad-text" }, "No LLM is configured, so nothing will be bought: set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL in .env, or set decider: rules."));
  }
  box.replaceChildren(...parts);
}

function drawOrders([orders, limits, tradingMode]) {
  $("orders-card").hidden = tradingMode === "paper";
  if (tradingMode === "paper") return;
  $("limits").textContent = `Buys today ${limits.buys_today}/${limits.max_buys_per_day} · failed sends today `
    + `${limits.failed_sends_today}/${limits.max_failed_sends} · max ${limits.max_trade_sol} SOL per trade`;
  fillTable("orders", orders.map((order) => el("tr", {},
    el("td", { class: "num" }, timeOf(order.created_at)),
    el("td", { class: "mono" }, order.lead),
    el("td", {}, order.side),
    el("td", { class: "num" }, order.amount),
    el("td", {}, el("span", { class: `pill status-${order.status}` }, order.status)),
    el("td", {}, order.tx_sig
      ? el("a", { href: `https://solscan.io/tx/${encodeURIComponent(order.tx_sig)}`, target: "_blank", rel: "noopener noreferrer", class: "mono" }, shortAddress(order.tx_sig))
      : "–"),
    el("td", { class: "note", title: order.detail }, order.detail))));
}

function drawRun() {
  const runner = state.runner;
  $("run-once").disabled = Boolean(runner) || busy;
  $("run-loop").disabled = Boolean(runner) || busy;
  $("interval").disabled = Boolean(runner);
  $("stop").disabled = !runner || runner.stopping || busy;
  $("halt-now").disabled = state.halted || busy;
  const status = $("run-status");
  if (!runner) {
    const last = state.last_run;
    status.replaceChildren(last
      ? `Idle. Last run finished at ${timeOf(last.finished_at)}${last.error ? ` with an error: ${last.error}` : "."}`
      : "Idle. Run a cycle to scan, score and stage leads.");
    return;
  }
  const what = runner.cycles === 1 ? "1 cycle"
    : runner.cycles == null ? `auto-run every ${runner.interval_s} s` : `${runner.cycles} cycles`;
  const parts = [`Running ${what}, cycle ${Math.max(runner.cycle, 1)}, started ${timeOf(runner.started_at)}.`];
  if (runner.stopping) parts.push(" Stopping after this cycle.");
  else if (runner.next_cycle_at) parts.push(" Next cycle in ", el("span", { "data-deadline": runner.next_cycle_at }, ""), ".");
  status.replaceChildren(...parts);
}

function drawAwaiting(leads) {
  $("awaiting-count").textContent = leads.length;
  fillTable("awaiting", leads.map((lead) => el("tr", {},
    el("td", { class: "mono" }, lead.lead),
    el("td", {}, tokenCell(lead)),
    el("td", { class: "num" }, sol(lead.size_sol)),
    el("td", { class: "num" }, pct(lead.impact_pct)),
    el("td", { class: "num" }, pct(lead.sell_impact_pct)),
    el("td", { class: "num" }, usd(lead.liquidity_usd)),
    el("td", { class: "num" }, age(lead.age_minutes)),
    el("td", { class: "num" }, lead.top10_pct == null ? "no data" : pct(lead.top10_pct)),
    el("td", { class: "num", "data-deadline": lead.expires_at }, ""),
    el("td", { class: "actions" },
      el("button", { class: "approve", type: "button", disabled: busy, onclick: () => decide("approve", lead) }, "Approve"),
      el("button", { class: "reject", type: "button", disabled: busy, onclick: () => decide("reject", lead) }, "Reject")))));
}

function drawPositions(positions) {
  $("positions-count").textContent = positions.length;
  fillTable("positions", positions.map((position) => el("tr", {},
    el("td", { class: "mono" }, position.lead),
    el("td", {}, tokenCell(position)),
    el("td", {}, el("span", { class: `pill mode-${position.mode}` }, MODE_LABEL[position.mode] || position.mode),
      position.selling ? el("span", { class: "muted" }, " selling…") : null),
    el("td", { class: "num" }, sol(position.size_sol)),
    el("td", { class: "num" }, missing(position.entry) ? "–" : Number(position.entry).toExponential(3)),
    el("td", { class: `num ${tone(position.unrealized)}` }, signedSol(position.unrealized)),
    el("td", { class: "num" }, timeOf(position.opened_at)),
    el("td", { class: "num" }, position.last_checked_at ? timeOf(position.last_checked_at) : "pending"))));
}

function drawLeads(leads) {
  fillTable("leads", leads.map((lead) => el("tr", {},
    el("td", { class: "mono" }, lead.lead),
    el("td", {}, tokenCell(lead)),
    el("td", {}, el("span", { class: `pill status-${lead.status}` }, lead.status.replace("_", " "))),
    el("td", { class: "num" }, usd(lead.liquidity_usd)),
    el("td", { class: "num" }, age(lead.age_minutes)),
    el("td", { class: "note", title: lead.note }, lead.note))));
}

function drawEvents(events) {
  // Handoff lines already say lead, mint, stage and result; other events get "action LEAD-n: details".
  const describe = (event) => (event.text.startsWith("LEAD-") ? event.text
    : `${event.action}${event.lead ? ` ${event.lead}` : ""}${event.text ? `: ${event.text}` : ""}`);
  $("events").replaceChildren(...events.map((event) => el("li", {},
    el("span", { class: "who" }, timeOf(event.time)),
    el("span", { class: "who role" }, event.role),
    el("span", { class: "what" }, describe(event)))));
}

function drawLastRun(lastRun) {
  const box = $("last-run");
  if (!lastRun) box.textContent = "No run yet in this session.";
  else box.textContent = lastRun.error ? `Run failed: ${lastRun.error}` : lastRun.summary;
}

function tick() {
  for (const node of document.querySelectorAll("[data-deadline]")) {
    node.textContent = countdown(node.dataset.deadline);
  }
}

function intervalSeconds() {
  const value = Math.round(Number($("interval").value));
  return Number.isFinite(value) ? Math.min(Math.max(value, 5), 3600) : 60;
}

for (const tab of document.querySelectorAll(".tab")) {
  tab.addEventListener("click", () => {
    if (tab.dataset.mode === mode) return;
    mode = tab.dataset.mode;
    rendered = {};
    refresh();
  });
}
$("run-once").addEventListener("click", () => act("/api/run", { cycles: 1, interval: intervalSeconds() }));
$("run-loop").addEventListener("click", () => act("/api/run", { cycles: null, interval: intervalSeconds() }));
$("stop").addEventListener("click", () => act("/api/stop", {}));
$("halt-now").addEventListener("click", () => {
  if (window.confirm("Halt trading now?\n\nNo new leads, quotes or buys until you clear it. Open positions are still managed and sold on their exit triggers.")) {
    act("/api/halt", { reason: "pressed Halt trading now" });
  }
});
$("clear-halt").addEventListener("click", () => {
  const reason = $("halt-note").value.trim();
  if (!reason) {
    toast("Write why it is safe to resume; it goes in the audit log.", "error");
    return;
  }
  act("/api/clear-halt", { reason }).then((result) => { if (result) $("halt-note").value = ""; });
});
$("daily").addEventListener("click", async () => {
  const result = await act("/api/summary", {});
  if (result && result.text) {
    $("report-text").textContent = result.text;
    $("report-dialog").showModal();
  }
});
$("report-close").addEventListener("click", () => $("report-dialog").close());
setInterval(tick, 1000);
refresh();
