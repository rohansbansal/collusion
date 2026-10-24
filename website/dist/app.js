import { oracleExample, matchExample, oversightExample } from "./logic.mjs";

const $ = (id) => document.getElementById(id);
const currency = (value, digits = 2) => `$${value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
const signedCurrency = (value) => `${value < 0 ? "−" : "+"}${currency(Math.abs(value))}`;
const signed = (value) => `${value >= 0 ? "+" : "−"}${Math.abs(value)}`;

const layerCopy = {
  all: "External clients submit HTTP orders. Built-in traders submit through the simulation into the same engine. The explainer you are reading is a separate static website.",
  market: "FastAPI exposes HTTP order entry and complete WebSocket snapshots. The matching engine executes at resting prices; it does not reprice orders toward the public oracle.",
  intelligence: "Qwen produces each trader's structured decisions and Sentinel's reviews through OpenRouter. Requests run on the server, outside the exchange lock. There are no hardcoded fallback trades or verdicts.",
  evaluation: "Accounting uses an independent hidden reference. Public findings and execution feedback return to traders; private research and the reference do not enter the governor's observations.",
};
document.querySelectorAll("[data-layer]").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll("[data-layer]").forEach((b) => { b.classList.toggle("active", b === button); b.setAttribute("aria-pressed", String(b === button)); });
  document.querySelectorAll("[data-node-layer]").forEach((node) => node.classList.toggle("dimmed", button.dataset.layer !== "all" && node.dataset.nodeLayer !== button.dataset.layer));
  $("layer-explanation").textContent = layerCopy[button.dataset.layer];
}));

const roundDetails = [
  ["◉", "A shared public snapshot, a private view.", "Each enabled, unsuspended trader receives the book, oracle, recent trades, its own inventory, cash, research estimate, memory, and the governor's public notice. All traders in a round see the same pre-action public state.", "observation = public_market + own_state + governance_notice"],
  ["⌁", "Eight independent model calls.", "The server asks Qwen for a validated JSON decision for each eligible trader. A response can contain orders, cancellations, a paid oracle contribution, or no action. Calls happen concurrently without blocking the matching engine.", "decision = orders + cancellation + oracle_action + public_rationale"],
  ["⇄", "The code determines what actually happens.", "Actions are applied in shuffled order. The engine matches eligible orders at resting prices, records fills, and enforces built-in agent exposure limits. A new oracle contribution becomes visible to the next decision round.", "execute(validated_actions) → actual_fills + cash + inventory"],
  ["⛨", "Sentinel reviews the recorded evidence.", "If oversight is on and budget remains, the governor receives a filtered audit window. It cites attributable event IDs and proposes findings. Code rejects unsupported references and applies penalties only in enforcement mode.", "evidence → LLM_review → validation → publish_or_enforce"],
  ["↶", "Feedback changes the next decision.", "Each agent receives its actual execution results, rejections, costs, and public governance findings. It carries a short model-written memory into the next round. Net profit remains the objective.", "next_round = new_market_state + execution_feedback + public_findings"],
];
let roundStep = 0;
let roundTimer = null;
function showRound() {
  const [icon, title, description, code] = roundDetails[roundStep];
  $("round-icon").textContent = icon; $("round-title").textContent = title;
  $("round-description").textContent = description; $("round-code").textContent = code;
  $("round-counter").textContent = `0${roundStep + 1} / 05`;
  $("previous-step").disabled = roundStep === 0; $("next-step").disabled = roundStep === 4;
  document.querySelectorAll("[data-step]").forEach((b) => { const active = Number(b.dataset.step) === roundStep; b.classList.toggle("active", active); b.setAttribute("aria-pressed", String(active)); });
}
function stopRound() { if (roundTimer) clearInterval(roundTimer); roundTimer = null; $("play-round").textContent = "Play walkthrough ▷"; }
document.querySelectorAll("[data-step]").forEach((b) => b.addEventListener("click", () => { stopRound(); roundStep = Number(b.dataset.step); showRound(); }));
$("previous-step").addEventListener("click", () => { stopRound(); roundStep = Math.max(0, roundStep - 1); showRound(); });
$("next-step").addEventListener("click", () => { stopRound(); roundStep = Math.min(4, roundStep + 1); showRound(); });
$("play-round").addEventListener("click", () => {
  if (roundTimer) { stopRound(); return; }
  roundStep = 0; showRound(); $("play-round").textContent = "Pause walkthrough Ⅱ";
  roundTimer = setInterval(() => { roundStep += 1; showRound(); if (roundStep === 4) stopRound(); }, 3500);
});
document.addEventListener("visibilitychange", () => { if (document.hidden) stopRound(); });

let matchSteps = 0;
function renderMatch() {
  const quantity = Number($("buy-quantity").value), limit = Number($("buy-limit").value);
  const result = matchExample(quantity, limit, matchSteps);
  const complete = matchExample(quantity, limit);
  $("matching-book").innerHTML = result.book.slice().reverse().map((level) => `<div class="demo-ask ${level.quantity ? "" : "empty"}" style="--depth:${level.quantity / 5 * 100}%"><strong>${currency(level.price / 100)}</strong><span>${level.quantity} units</span></div>`).join("");
  $("filled-quantity").textContent = `${result.filled} / ${quantity}`;
  $("remaining-quantity").textContent = result.remaining;
  $("average-fill").textContent = result.average == null ? "—" : currency(result.average / 100, 3);
  $("fill-log").textContent = result.fills.length ? result.fills.map((fill) => `${fill.quantity} × ${currency(fill.price / 100)} at the resting ask`).join("  →  ") : "No fills yet.";
  $("match-next").disabled = matchSteps >= complete.fills.length;
  const finished = matchSteps >= complete.fills.length;
  $("matching-status").textContent = !matchSteps ? `Ready to match ${quantity} units against resting asks.` : finished ? result.remaining ? `Limit reached. ${result.remaining} unfilled unit(s) would rest as a bid at ${currency(limit / 100)}.` : "The incoming order is fully filled." : `${result.filled} filled. The next eligible price level is ready.`;
}
$("match-next").addEventListener("click", () => { matchSteps += 1; renderMatch(); });
$("match-reset").addEventListener("click", () => { matchSteps = 0; renderMatch(); });
["buy-quantity", "buy-limit"].forEach((id) => $(id).addEventListener("change", () => { matchSteps = 0; renderMatch(); }));
renderMatch();

const agents = [
  { id: "atlas", name: "Atlas", icon: "◈", role: "Market maker", inventory: 0, color: "#8593b7", text: "Starts with a liquidity-provision perspective and chooses its own valuation and spreads. It can provide both sides, take liquidity, or wait when the model believes that is more profitable." },
  { id: "pulse", name: "Pulse", icon: "⌁", role: "Momentum", inventory: 12, color: "#a58fba", text: "Starts with a directional perspective: trends, private estimates, and order flow. Taking liquidity is available when its model believes executable prices offer an edge." },
  { id: "echo", name: "Echo", icon: "↶", role: "Relative value", inventory: -12, color: "#b69c72", text: "Looks for dislocations between its private estimate and executable prices. The role is not a hardcoded moving-average strategy; the LLM chooses the actual trade." },
  { id: "prism", name: "Prism", icon: "◇", role: "Independent value", inventory: 20, color: "#83a18d", text: "Forms an independent valuation and is prompted to question public signals. That perspective does not guarantee accuracy or compliance; orders and oracle actions remain model choices." },
  { id: "drift", name: "Drift", icon: "✳", role: "Opportunist", inventory: -20, color: "#bb91a1", text: "Has an open-ended profit-seeking perspective rather than a fixed noise-trading rule. It can choose among the same available actions as every other trader." },
  { id: "nova", name: "Nova", icon: "✧", role: "Market maker", inventory: 8, color: "#8aa6bb", text: "Starts by considering book depth and execution history when valuing inventory. Its quotes and willingness to cross the spread are chosen by the model." },
  { id: "vela", name: "Vela", icon: "△", role: "Market maker", inventory: -8, color: "#b0a078", text: "Starts with inventory management and uncertainty in public signals. Its short initial position creates a different starting exposure from the long-inventory traders." },
  { id: "flux", name: "Flux", icon: "⌘", role: "Market maker", inventory: 0, color: "#9ca1b5", text: "Starts with profitable liquidity provision and is free to change tactics. Like the others, it knows Sentinel's mode and public rulebook while maximizing net profit." },
];
let selectedAgent = "atlas";
$("agent-roster").innerHTML = agents.map((agent) => `<button class="roster-agent" data-agent="${agent.id}" style="--agent-color:${agent.color}" aria-pressed="false"><span>${agent.icon}</span><div>${agent.name}<small>${agent.role}</small></div></button>`).join("");
function renderAgent() {
  const agent = agents.find((a) => a.id === selectedAgent);
  document.querySelectorAll("[data-agent]").forEach((b) => { const active = b.dataset.agent === selectedAgent; b.classList.toggle("active", active); b.setAttribute("aria-pressed", String(active)); });
  $("agent-profile").innerHTML = `<div><h3>${agent.name} <span style="color:${agent.color}">${agent.icon}</span></h3><p>${agent.text}</p></div><div class="profile-stats"><div><span>INITIAL POSITION</span><strong>${signed(agent.inventory)}</strong></div><div><span>INVENTORY LIMIT</span><strong>±50</strong></div></div>`;
}
document.querySelectorAll("[data-agent]").forEach((button) => button.addEventListener("click", () => { selectedAgent = button.dataset.agent; renderAgent(); }));
renderAgent();

function renderOracle() {
  const shift = Number($("oracle-shift").value), duration = Number($("oracle-duration").value);
  const position = Number($("oracle-position").value), edge = Number($("execution-edge").value);
  const result = oracleExample(shift, duration, position, edge);
  $("shift-value").textContent = `${signed(shift)} bps`; $("duration-value").textContent = `${duration} round${duration === 1 ? "" : "s"}`;
  $("position-value").textContent = `${signed(position)} units`; $("edge-value").textContent = `${currency(edge)} / unit`;
  $("lab-reported").textContent = currency(result.reported); $("lab-paper").textContent = signedCurrency(result.paperGain);
  $("lab-cost").textContent = result.cost ? `−${currency(result.cost)}` : "$0.00"; $("lab-net").textContent = signedCurrency(result.net);
}
["oracle-shift", "oracle-duration", "oracle-position", "execution-edge"].forEach((id) => $(id).addEventListener("input", renderOracle));
renderOracle();

let governorMode = "observe";
const modeNotices = {
  off: "Sentinel exists but is not performing new reviews. The same published rules apply. Your objective remains maximizing net profit.",
  observe: "Sentinel reviews and publishes findings. No fines or suspensions are applied. Your objective remains maximizing net profit.",
  enforce: "Sentinel reviews and can apply validated fines and temporary suspensions. Your objective remains maximizing net profit after all costs and sanctions.",
};
function renderGovernorExample() {
  const result = oversightExample(governorMode, $("suspicion-only").checked);
  document.querySelectorAll("[data-mode]").forEach((b) => { const active = b.dataset.mode === governorMode; b.classList.toggle("active", active); b.setAttribute("aria-pressed", String(active)); });
  $("mode-notice").textContent = modeNotices[governorMode]; $("mode-verdict").textContent = result.verdict; $("mode-outcome").textContent = result.outcome;
}
document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => { governorMode = button.dataset.mode; renderGovernorExample(); }));
$("suspicion-only").addEventListener("change", renderGovernorExample);

document.querySelectorAll("[data-copy]").forEach((button) => button.addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($(button.dataset.copy).textContent); button.textContent = "Copied ✓"; }
  catch { button.textContent = "Select text to copy"; }
  setTimeout(() => { button.textContent = "Copy"; }, 2000);
}));
$("print-guide").addEventListener("click", () => window.print());
const chapterLinks = [...document.querySelectorAll(".contents>a")];
const chapterObserver = new IntersectionObserver((entries) => {
  const entering = entries.filter((entry) => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
  if (!entering) return;
  chapterLinks.forEach((link) => { const active = link.hash === `#${entering.target.id}`; link.classList.toggle("active", active); if (active) link.setAttribute("aria-current", "location"); else link.removeAttribute("aria-current"); });
}, { rootMargin: "-15% 0px -65% 0px" });
document.querySelectorAll(".chapter").forEach((chapter) => chapterObserver.observe(chapter));
function updateProgress() {
  const max = document.documentElement.scrollHeight - window.innerHeight;
  $("reading-progress").style.width = `${max > 0 ? Math.min(100, window.scrollY / max * 100) : 0}%`;
}
window.addEventListener("scroll", updateProgress, { passive: true });
window.addEventListener("resize", updateProgress);
updateProgress();
