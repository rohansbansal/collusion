"use strict";

const $ = (id) => document.getElementById(id);
const escapeHTML = (value) => String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const number = new Intl.NumberFormat("en-US");
const money = (ticks) => ticks == null ? "—" : (ticks / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signed = (value) => `${value > 0 ? "+" : value < 0 ? "−" : ""}${number.format(Math.abs(value))}`;
const pnl = (value) => `${value >= 0 ? "+" : "−"}$${money(Math.abs(value))}`;
const icons = { atlas: "◈", pulse: "⌁", echo: "↶", prism: "◇", drift: "✳" };
let state = null;
let selected = "atlas";
let connected = false;
let latestSequence = -1;
let lastTrade = null;
let busy = false;
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
    const running = state.simulation.running && agent.enabled;
    card.classList.toggle("active", agent.agent_id === selected);
    card.querySelector(".agent-name").setAttribute("aria-pressed", String(agent.agent_id === selected));
    const toggle = card.querySelector(".agent-toggle");
    toggle.setAttribute("aria-pressed", String(agent.enabled));
    toggle.setAttribute("aria-label", `${agent.enabled ? "Pause" : "Resume"} ${agent.name}`);
    toggle.disabled = !connected || pendingAgents.has(agent.agent_id);
    card.querySelector(".agent-pnl").textContent = pnl(agent.pnl);
    card.querySelector(".agent-pnl").classList.toggle("negative", agent.pnl < 0);
    card.querySelector(".agent-position").textContent = signed(agent.position);
    card.querySelector(".agent-fills").textContent = number.format(agent.fills);
    card.querySelector(".agent-state").classList.toggle("paused", !running);
    card.querySelector(".agent-state span").textContent = running ? `${agent.open_orders} resting · active` : agent.enabled ? "Simulation paused" : "Agent paused";
  }
  const active = state.simulation.running ? agents.filter((agent) => agent.enabled).length : 0;
  $("active-count").textContent = `${active} agent${active === 1 ? "" : "s"} trading`;
  const agent = agents.find((a) => a.agent_id === selected) || agents[0];
  if (!agent) return;
  const exposure = Math.min(50, Math.abs(agent.position / agent.position_limit) * 50);
  $("agent-detail").innerHTML = `<div class="spotlight-title"><span class="agent-icon" style="--agent-color:${color(agent)}">${icons[agent.agent_id] || "◇"}</span><div><h3>${escapeHTML(agent.name)}</h3><p>${escapeHTML(agent.strategy)} · ${agent.orders_submitted} orders submitted</p></div></div><p class="agent-description">${escapeHTML(agent.description)}</p><div class="signal"><i></i>${escapeHTML(agent.signal)}</div><div class="position-meter"><div><span>Inventory exposure</span><strong>${signed(agent.position)} / ±${agent.position_limit}</strong></div><div class="position-track"><i style="left:${agent.position < 0 ? 50 - exposure : 50}%;width:${exposure}%"></i></div></div>`;
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
  $("book-mid-label").textContent = last == null ? "External fair" : "Last execution";
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
  const values = history.flatMap((p) => p.last == null ? [p.fair] : [p.fair, p.last]);
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
}

function connect() {
  const socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  socket.onopen = () => { latestSequence = -1; lastTrade = null; setConnected(true); };
  socket.onmessage = (event) => {
    try { render(JSON.parse(event.data)); }
    catch (error) { console.error("Market snapshot failed", error); showError("Could not display the latest market snapshot."); }
  };
  socket.onerror = () => socket.close();
  socket.onclose = () => { setConnected(false); window.setTimeout(connect, 1500); };
}

connect();
