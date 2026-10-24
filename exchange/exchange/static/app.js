"use strict";

const $ = (id) => document.getElementById(id);
const escapeHTML = (value) => String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const number = new Intl.NumberFormat("en-US");
const money = (ticks) => ticks == null ? "—" : (ticks / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signed = (value) => `${value > 0 ? "+" : value < 0 ? "−" : ""}${number.format(Math.abs(value))}`;
const pnl = (value) => `${value >= 0 ? "+" : "−"}$${money(Math.abs(value))}`;
const icons = { atlas: "◈", pulse: "⌁", echo: "↶", prism: "◇", drift: "✳", nova: "✧", vela: "△", flux: "⌘" };
let state = null;
let selected = "atlas";
let connected = false;
let latestSequence = -1;
let lastTrade = null;
let busy = false;
let oracleBusy = false;
let lastEvent = null;
let serverError = "";
let governorBusy = false;
let lastGovernorReview = null;
const pendingAgents = new Set();

function color(agent) {
  return /^#[0-9a-f]{6}$/i.test(agent?.color || "") ? agent.color : "#919aa6";
}

function showError(message) {
  $("error").textContent = message;
  $("error").hidden = !message;
}

async function request(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? "POST" : "PATCH",
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) throw new Error(`Control request failed (${response.status}). Please try again.`);
  return response.json();
}

$("simulation-control").addEventListener("click", async () => {
  if (!state || busy || !connected) return;
  busy = true;
  updateControls();
  showError("");
  try {
    await request(`/simulation/${state.simulation.running ? "pause" : "start"}`);
  } catch (error) {
    showError(error.message);
  } finally {
    busy = false;
    updateControls();
  }
});

$("oracle-control").addEventListener("click", async () => {
  if (!state || !connected || oracleBusy) return;
  oracleBusy = true;
  updateControls();
  try { await request("/experiment/oracle", { enabled: !state.simulation.oracle_enabled }); }
  catch (error) { showError(error.message); }
  finally { oracleBusy = false; updateControls(); }
});

$("governor-mode").addEventListener("change", async (event) => {
  if (!state || !connected || governorBusy) return;
  const mode = event.target.value;
  governorBusy = true;
  event.target.disabled = true;
  try { await request("/experiment/governor", { mode }); }
  catch (error) { showError(error.message); }
  finally { governorBusy = false; renderGovernor(); }
});

$("agent-cards").addEventListener("click", async (event) => {
  const select = event.target.closest("[data-select]");
  if (select) {
    selected = select.dataset.select;
    renderAgents();
    return;
  }
  const toggle = event.target.closest("[data-toggle]");
  if (!toggle || !state || !connected) return;
  const id = toggle.dataset.toggle;
  if (pendingAgents.has(id)) return;
  const agent = state.simulation.agents.find((a) => a.agent_id === id);
  pendingAgents.add(id);
  toggle.disabled = true;
  showError("");
  try {
    await request(`/agents/${encodeURIComponent(id)}`, { enabled: !agent.enabled });
  } catch (error) {
    showError(error.message);
  } finally {
    pendingAgents.delete(id);
    renderAgents();
  }
});

function updateControls() {
  $("simulation-control").disabled = !connected || busy || !state;
  if (!state) return;
  const running = state.simulation.running;
  const sim = state.simulation;
  $("simulation-control").disabled = !connected || busy || !sim.configured || sim.calls >= sim.max_calls;
  $("oracle-control").disabled = !connected || oracleBusy;
  $("oracle-control").textContent = oracleBusy ? "Updating…" : sim.oracle_enabled ? "Enabled · click to disable" : "Disabled · control condition";
  $("oracle-control").setAttribute("aria-pressed", String(sim.oracle_enabled));
  $("llm-model").textContent = sim.model;
  $("llm-usage").textContent = `${sim.calls} / ${sim.max_calls} calls · ${number.format(sim.tokens)} tokens · $${sim.api_cost.toFixed(4)} reported cost`;
  $("reference-price").textContent = `$${money(state.reference.price)}`;
  $("oracle-deviation").textContent = sim.oracle_shift_bps ? `Oracle deviation ${signed(sim.oracle_shift_bps)} bps` : "No active oracle distortion";
  $("oracle-attempts").textContent = `${sim.oracle_actions} modification actions · observers see attribution`;
  $("nav-agent-count").textContent = sim.agents.length;
  $("agent-count").textContent = String(sim.agents.length).padStart(2, "0");
  if (sim.last_error) {
    serverError = sim.last_error;
    showError(serverError);
  } else if (serverError) {
    if ($("error").textContent === serverError) showError("");
    serverError = "";
  }
  $("control-label").textContent = busy ? "Updating…" : running ? "Pause simulation" : "Start simulation";
  $("control-icon").textContent = running ? "Ⅱ" : "▷";
  $("simulation-status").textContent = running ? "Simulation running" : "Simulation paused";
  $("simulation-dot").classList.toggle("offline", !running);
  $("round-count").textContent = `Round ${number.format(state.simulation.rounds)}`;
}

function renderBook(side, levels) {
  const visible = levels.slice(0, 5);
  if (!visible.length) {
    $(side).innerHTML = `<div class="empty-book">No ${side === "asks" ? "sell" : "buy"} orders</div>`;
    return;
  }
  let cumulative = 0;
  const rows = visible.map((level) => ({ ...level, total: cumulative += level.quantity }));
  const max = cumulative;
  if (side === "asks") rows.reverse();
  $(side).innerHTML = rows.map((level) => `<div class="book-row" style="--depth:${level.total / max * 100}%"><span>${money(level.price)}</span><span>${number.format(level.quantity)}</span><span>${number.format(level.total)}</span></div>`).join("");
}

function renderAgents() {
  if (!state) return;
  const agents = state.simulation.agents;
  if (!$("agent-cards").children.length) {
    $("agent-cards").innerHTML = agents.map((agent) => `<article class="agent-card" id="agent-${escapeHTML(agent.agent_id)}" style="--agent-color:${color(agent)}"><div class="agent-top"><span class="agent-icon">${icons[agent.agent_id] || "◇"}</span><button class="agent-toggle" data-toggle="${escapeHTML(agent.agent_id)}" aria-pressed="true"><span></span></button></div><button class="agent-name" data-select="${escapeHTML(agent.agent_id)}">${escapeHTML(agent.name)}</button><span class="agent-strategy">${escapeHTML(agent.strategy)}</span><div class="agent-pnl">—</div><span class="agent-pnl-label">MARK-TO-FAIR P&L</span><div class="agent-stats"><span>Position <strong class="agent-position">0</strong></span><span>Fills <strong class="agent-fills">0</strong></span></div><div class="agent-state"><i></i><span>Waiting</span></div></article>`).join("");
  }
  for (const agent of agents) {
    const card = $(`agent-${agent.agent_id}`);
    const running = state.simulation.running && agent.enabled && !agent.suspension_rounds_remaining;
    card.classList.toggle("active", agent.agent_id === selected);
    card.querySelector(".agent-name").setAttribute("aria-pressed", String(agent.agent_id === selected));
    const toggle = card.querySelector(".agent-toggle");
    toggle.setAttribute("aria-pressed", String(agent.enabled));
    toggle.setAttribute("aria-label", `${agent.enabled ? "Pause" : "Resume"} ${agent.name}`);
    toggle.disabled = !connected || pendingAgents.has(agent.agent_id);
    card.querySelector(".agent-pnl-label").textContent = "MARK-TO-REFERENCE P&L";
    card.querySelector(".agent-pnl").textContent = pnl(agent.pnl);
    card.querySelector(".agent-pnl").classList.toggle("negative", agent.pnl < 0);
    card.querySelector(".agent-position").textContent = signed(agent.position);
    card.querySelector(".agent-fills").textContent = number.format(agent.fills);
    card.querySelector(".agent-state").classList.toggle("paused", !running);
    card.querySelector(".agent-state span").textContent = agent.suspension_rounds_remaining ? `Suspended · ${agent.suspension_rounds_remaining} round(s)` : running ? agent.thinking ? "LLM thinking…" : `${agent.decisions} decisions · ${agent.oracle_actions} oracle actions` : agent.enabled ? "Simulation paused" : "Agent paused";
  }
  const active = state.simulation.running ? agents.filter((agent) => agent.enabled && !agent.suspension_rounds_remaining).length : 0;
  $("active-count").textContent = `${active} agent${active === 1 ? "" : "s"} trading`;
  const agent = agents.find((a) => a.agent_id === selected) || agents[0];
  if (!agent) return;
  const exposure = Math.min(50, Math.abs(agent.position / agent.position_limit) * 50);
  $("agent-detail").innerHTML = `<div class="spotlight-title"><span class="agent-icon" style="--agent-color:${color(agent)}">${icons[agent.agent_id] || "◇"}</span><div><h3>${escapeHTML(agent.name)}</h3><p>${escapeHTML(agent.strategy)} · ${agent.orders_submitted} orders submitted</p></div></div><p class="agent-description">${escapeHTML(agent.description)}</p><div class="signal"><i></i>${escapeHTML(agent.signal)}</div><p class="llm-detail">${agent.decisions} LLM decisions · ${agent.errors} errors · ${agent.oracle_actions} oracle actions · $${money(agent.fees)} oracle costs · $${money(agent.fines)} fines</p><div class="position-meter"><div><span>Inventory exposure</span><strong>${signed(agent.position)} / ±${agent.position_limit}</strong></div><div class="position-track"><i style="left:${agent.position < 0 ? 50 - exposure : 50}%;width:${exposure}%"></i></div></div>`;
}

function renderGovernor() {
  if (!state?.simulation.governor) return;
  const gov = state.simulation.governor;
  if (!governorBusy) $("governor-mode").value = gov.mode;
  $("governor-mode").disabled = !connected || governorBusy;
  $("governor-status").textContent = gov.thinking ? "Reviewing this round…" : gov.mode === "off" ? "Oversight off" : `Last reviewed round ${gov.last_round} · ${gov.model}`;
  $("governor-summary").textContent = gov.summary;
  $("governor-powers").textContent = gov.mode === "enforce" ? "Enforcement: up to $5 fine and 3 future-round suspension per supported violation. Fines reduce P&L." : gov.mode === "observe" ? "Observation only. Findings are published to traders; no fines or suspensions are applied." : "No new reviews or penalties. Existing suspensions still expire normally.";
  $("governor-reviews").textContent = gov.reviews;
  $("governor-findings-count").textContent = gov.findings.length;
  $("governor-fines").textContent = `$${money(gov.total_fines_ticks)}`;
  $("governor-error").textContent = gov.last_error;
  $("governor-error").hidden = !gov.last_error;
  const key = `${gov.reviews}:${gov.mode}:${gov.findings.length}`;
  if (key === lastGovernorReview) return;
  lastGovernorReview = key;
  const agents = new Map(state.simulation.agents.map((agent) => [agent.agent_id, agent]));
  $("governor-rules").innerHTML = Object.entries(gov.rules).map(([id, text]) => `<p><strong>${escapeHTML(id.replaceAll("_", " "))}:</strong> ${escapeHTML(text)}</p>`).join("");
  $("governor-findings").innerHTML = gov.findings.length ? gov.findings.slice(-8).reverse().map((finding) => {
    const agent = agents.get(finding.agent_id);
    return `<article class="governor-finding ${finding.classification === "violation" ? "violation" : "suspicious"}"><div class="finding-title"><strong>${escapeHTML(agent?.name || finding.agent_id)}</strong><span>${escapeHTML(finding.classification.toUpperCase())}</span><small>R${finding.round} · ${escapeHTML(finding.mode)}</small></div><p>${escapeHTML(finding.explanation)}</p><div class="finding-meta"><span>${escapeHTML(finding.rule_id.replaceAll("_", " "))}</span><span>${finding.evidence_event_ids.length} evidence item(s)</span><span>$${money(finding.fine_ticks)} applied · ${finding.suspension_rounds} suspended round(s)</span></div><details><summary>Evidence IDs</summary><code>${finding.evidence_event_ids.map(escapeHTML).join("<br>")}</code></details></article>`;
  }).join("") : '<p class="governor-empty">No supported findings published yet. This is not proof of compliance.</p>';
}

function renderDecisions() {
  const events = state.simulation.events.filter((event) => ["decision", "oracle_tamper", "error", "discarded"].includes(event.kind));
  const key = events.at(-1)?.event_id || "empty";
  if (key === lastEvent) return;
  lastEvent = key;
  if (!events.length) {
    $("decision-feed").innerHTML = '<p class="table-empty">Waiting for the first LLM decisions…</p>';
    return;
  }
  const agents = new Map(state.simulation.agents.map((agent) => [agent.agent_id, agent]));
  $("decision-feed").innerHTML = events.slice(-14).reverse().map((event) => {
    const agent = agents.get(event.agent_id);
    let label = "LLM DECISION";
    let detail = "";
    if (event.kind === "oracle_tamper") {
      label = "ORACLE MODIFICATION";
      detail = `${signed(event.shift_bps)} bps for ${event.duration_rounds} round(s) · $${money(event.cost_ticks)} paid. ${event.rationale}`;
    } else if (event.kind === "decision") {
      const orders = event.action.orders;
      detail = `${orders.length ? orders.map((o) => `${o.side} ${o.quantity} @ ${money(o.price)}`).join(" · ") : "No orders"}. ${event.action.rationale}`;
      if (event.rejected.length) detail += ` [${event.rejected.join("; ")}]`;
    } else { label = event.kind.toUpperCase(); detail = event.message || event.reason; }
    return `<article class="decision-event ${event.kind === "oracle_tamper" ? "intervention" : ""}"><span class="decision-round">R${event.round}</span><div><div class="decision-heading"><strong style="color:${color(agent)}">${escapeHTML(agent?.name || event.agent_id)}</strong><span>${label}</span></div><p>${escapeHTML(detail)}</p></div></article>`;
  }).join("");
}

function renderTrades() {
  const trades = state.recent_trades;
  const key = trades.at(-1)?.trade_id || "empty";
  if (key === lastTrade) return;
  lastTrade = key;
  if (!trades.length) {
    $("trades-body").innerHTML = '<tr><td colspan="4" class="table-empty">Agents are warming up. Trades will appear here.</td></tr>';
    return;
  }
  const agents = new Map(state.simulation.agents.map((a) => [a.agent_id, a]));
  const party = (id) => {
    const agent = agents.get(id);
    return `<span class="trade-agent" style="--agent-color:${color(agent)}"><i></i>${escapeHTML(agent?.name || id)}</span>`;
  };
  $("trades-body").innerHTML = trades.slice(-20).reverse().map((trade) => `<tr><td>${trade.timestamp ? new Date(trade.timestamp).toLocaleTimeString("en-US", { hour12: false }) : "—"}</td><td><div class="trade-parties">${party(trade.buyer_agent_id)}<span class="trade-arrow">→</span>${party(trade.seller_agent_id)}</div></td><td class="number">${money(trade.price)}</td><td class="number">${number.format(trade.quantity)}</td></tr>`).join("");
}

function render(snapshot) {
  if (snapshot.sequence < latestSequence) return;
  latestSequence = snapshot.sequence;
  state = snapshot;
  const fair = snapshot.fair.price;
  const first = snapshot.history[0]?.fair || fair;
  const change = (fair - first) / first * 100;
  $("fair-price").textContent = `$${money(fair)}`;
  $("fair-change").textContent = `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`;
  $("fair-change").title = "Change over the visible chart window";
  $("fair-change").classList.toggle("negative", change < 0);
  const last = snapshot.stats.last_price;
  $("last-price").textContent = last == null ? "—" : `$${money(last)}`;
  $("last-note").textContent = last == null ? "Waiting for the first match" : "Latest matched execution";
  const bid = snapshot.book.bids[0]?.price;
  const ask = snapshot.book.asks[0]?.price;
  const spread = bid != null && ask != null ? ask - bid : null;
  $("spread").textContent = spread == null ? "—" : `$${money(spread)}`;
  $("spread-bps").textContent = spread == null ? "" : `${(spread / fair * 10000).toFixed(1)} bps`;
  $("open-orders").textContent = `${snapshot.stats.open_orders} resting orders`;
  $("volume").textContent = number.format(snapshot.stats.volume);
  $("trade-count").textContent = `${number.format(snapshot.stats.trades)} executed trades`;
  $("book-mid-price").textContent = money(last ?? fair);
  $("book-mid-label").textContent = last == null ? "Reported oracle" : "Last execution";
  $("book-spread").textContent = spread == null ? "— spread" : `$${money(spread)} spread`;
  renderBook("asks", snapshot.book.asks);
  renderBook("bids", snapshot.book.bids);
  const buys = snapshot.book.bids.reduce((sum, l) => sum + l.quantity, 0);
  const sells = snapshot.book.asks.reduce((sum, l) => sum + l.quantity, 0);
  const share = buys + sells ? buys / (buys + sells) * 100 : 50;
  $("bid-share").textContent = `Buy ${share.toFixed(0)}%`;
  $("ask-share").textContent = `Sell ${(100 - share).toFixed(0)}%`;
  $("balance-fill").style.width = `${share}%`;
  updateControls();
  renderAgents();
  renderTrades();
  renderDecisions();
  renderGovernor();
  drawChart();
}

function drawChart() {
  const history = state?.history || [];
  $("chart-empty").hidden = history.length > 1;
  const canvas = $("price-chart");
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  if (!width || !height) return;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const left = 8, right = width - 61, top = 17, bottom = height - 31;
  const values = history.flatMap((p) => [p.fair, p.reference, p.last].filter((v) => v != null));
  let min = values.length ? Math.min(...values) : 9900;
  let max = values.length ? Math.max(...values) : 10100;
  const padding = Math.max(10, (max - min) * 0.18);
  min -= padding;
  max += padding;
  const y = (value) => bottom - (value - min) / (max - min) * (bottom - top);
  const x = (index) => left + index / Math.max(1, history.length - 1) * (right - left);
  ctx.font = "9px ui-monospace, monospace";
  ctx.lineWidth = 1;
  for (let i = 0; i < 5; i++) {
    const value = min + (max - min) * i / 4;
    const py = y(value);
    ctx.strokeStyle = "#eff1f4";
    ctx.setLineDash([3, 4]);
    ctx.beginPath();ctx.moveTo(left, py);ctx.lineTo(right, py);ctx.stroke();
    ctx.fillStyle = "#a4acb7";
    ctx.fillText(money(value), right + 10, py + 3);
  }
  ctx.setLineDash([]);
  if (history.length < 2) return;
  const gradient = ctx.createLinearGradient(0, top, 0, bottom);
  gradient.addColorStop(0, "#64aa8f22");
  gradient.addColorStop(1, "#64aa8f00");
  ctx.beginPath();
  history.forEach((point, index) => index ? ctx.lineTo(x(index), y(point.fair)) : ctx.moveTo(x(index), y(point.fair)));
  ctx.lineTo(right, bottom);ctx.lineTo(left, bottom);ctx.closePath();
  ctx.fillStyle = gradient;ctx.fill();
  ctx.strokeStyle = "#c5cbd3";ctx.lineWidth = 1.25;ctx.setLineDash([4, 4]);ctx.beginPath();
  let started = false;
  history.forEach((point, index) => {
    if (point.last == null) return;
    if (started) ctx.lineTo(x(index), y(point.last));
    else { ctx.moveTo(x(index), y(point.last)); started = true; }
  });
  ctx.stroke();ctx.setLineDash([]);
  ctx.strokeStyle = "#c99c5d";ctx.lineWidth = 1.3;ctx.setLineDash([2, 3]);ctx.beginPath();
  history.forEach((point, index) => index ? ctx.lineTo(x(index), y(point.reference ?? point.fair)) : ctx.moveTo(x(index), y(point.reference ?? point.fair)));
  ctx.stroke();ctx.setLineDash([]);
  ctx.strokeStyle = "#479d80";ctx.lineWidth = 1.8;ctx.lineJoin = "round";ctx.beginPath();
  history.forEach((point, index) => index ? ctx.lineTo(x(index), y(point.fair)) : ctx.moveTo(x(index), y(point.fair)));
  ctx.stroke();
  const endY = y(history.at(-1).fair);
  ctx.fillStyle = "#479d80";ctx.beginPath();ctx.arc(right, endY, 3, 0, Math.PI * 2);ctx.fill();
  ctx.fillStyle = "#a6aeb8";ctx.font = "8px ui-monospace, monospace";
  for (let i = 0; i < 5; i++) {
    const index = Math.round(i / 4 * (history.length - 1));
    const label = new Date(history[index].timestamp).toLocaleTimeString("en-US", { hour12: false });
    ctx.textAlign = i === 0 ? "left" : i === 4 ? "right" : "center";
    ctx.fillText(label, x(index), height - 9);
  }
}

new ResizeObserver(drawChart).observe($("price-chart").parentElement);

function setConnected(value) {
  connected = value;
  $("connection-status").textContent = value ? "Connected to exchange" : "Reconnecting…";
  $("connection-dot").classList.toggle("offline", !value);
  $("chart-live").style.opacity = value ? "1" : ".35";
  updateControls();
  renderAgents();
  renderGovernor();
}

function connect() {
  const socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  socket.onopen = () => { latestSequence = -1; lastTrade = null; lastEvent = null; lastGovernorReview = null; setConnected(true); };
  socket.onmessage = (event) => {
    try { render(JSON.parse(event.data)); }
    catch (error) { console.error("Market snapshot failed", error); showError("Could not display the latest market snapshot."); }
  };
  socket.onerror = () => socket.close();
  socket.onclose = () => { setConnected(false); window.setTimeout(connect, 1500); };
}

connect();
