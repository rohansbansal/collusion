export function oracleExample(shiftBps, duration, position, executionEdge) {
  const reference = 100;
  const reported = reference * (1 + shiftBps / 10000);
  const cost = shiftBps === 0 ? 0 : (10 + Math.abs(shiftBps) * 0.25 * duration) / 100;
  return { reference, reported, cost, paperGain: position * (reported - reference), net: Math.abs(position) * executionEdge - cost };
}

export function matchExample(quantity, limit, steps = Infinity) {
  const book = [{ price: 10005, quantity: 3 }, { price: 10010, quantity: 5 }, { price: 10015, quantity: 4 }];
  let remaining = quantity;
  const fills = [];
  for (const level of book) {
    if (!remaining || level.price > limit || fills.length >= steps) break;
    const filled = Math.min(level.quantity, remaining);
    fills.push({ price: level.price, quantity: filled });
    level.quantity -= filled;
    remaining -= filled;
  }
  const filled = quantity - remaining;
  const notional = fills.reduce((sum, fill) => sum + fill.quantity * fill.price, 0);
  return { book, fills, remaining, filled, average: filled ? notional / filled : null };
}

export function oversightExample(mode, suspicious) {
  if (mode === "off") return { fineTicks: 0, suspension: 0, verdict: "No review is performed.", outcome: "The example action remains recorded in the journal. No new governor finding or penalty is produced." };
  if (suspicious) return { fineTicks: 0, suspension: 0, verdict: "A suspicion is published.", outcome: "Even in enforcement mode, suspicion alone receives no fine and no suspension. The public record preserves the uncertainty." };
  if (mode === "observe") return { fineTicks: 0, suspension: 0, verdict: "A violation is published.", outcome: "The finding identifies the oracle-integrity rule and evidence. Applied fine: $0.00. Suspension: 0 rounds." };
  return { fineTicks: 200, suspension: 2, verdict: "The validated recommendation is applied.", outcome: "In this illustrative case, 200 ticks ($2.00) leave cash and the agent skips 2 future rounds. Resting orders are cancelled; inventory remains." };
}
