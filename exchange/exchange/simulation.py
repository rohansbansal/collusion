"""Five bounded, rule-based trading agents sharing the real matching engine."""

import asyncio
import random
from collections import deque
from dataclasses import dataclass
from statistics import fmean
from typing import TYPE_CHECKING

from .engine import OrderRequest

if TYPE_CHECKING:
    from .api import Market


@dataclass
class Quote:
    side: str
    price: int
    quantity: int


@dataclass
class Agent:
    agent_id: str
    name: str
    strategy: str
    description: str
    color: str
    enabled: bool = True
    signal: str = "Waiting for market"
    orders_submitted: int = 0
    position_limit: int = 50

    def decide(self, fair, history, book, position, rng) -> tuple[list[Quote], str]:
        spread = max(4, round(fair * 0.001))
        bid = book["bids"][0]["price"] if book["bids"] else None
        ask = book["asks"][0]["price"] if book["asks"] else None

        def order(side, price, size=3):
            return [Quote(side, max(1, min(10**12, round(price))), size)]

        if self.strategy == "Market maker":
            center = fair - position * spread / 12
            return [Quote("buy", max(1, round(center - spread)), 6),
                    Quote("sell", min(10**12, max(2, round(center + spread))), 6)], "Quoting both sides · inventory skew"

        if self.strategy == "Momentum":
            if len(history) < 5:
                return [], "Collecting price history"
            change = fair - history[-5]
            if abs(change) < spread / 2:
                return [], "No directional signal"
            side = "buy" if change > 0 else "sell"
            price = (ask if ask is not None else fair + spread) if side == "buy" else (bid if bid is not None else fair - spread)
            return order(side, price), "Following upward momentum" if side == "buy" else "Following downward momentum"

        if self.strategy == "Mean reversion":
            if len(history) < 8:
                return [], "Collecting price history"
            anchor = fmean(list(history)[-24:])
            if abs(fair - anchor) < spread:
                return [], "Near rolling mean · waiting"
            side = "buy" if fair < anchor else "sell"
            price = (ask if ask is not None else fair) if side == "buy" else (bid if bid is not None else fair)
            return order(side, price, 2), "Buying below rolling mean" if side == "buy" else "Selling above rolling mean"

        if self.strategy == "Fair value":
            edge = max(2, spread / 3)
            if ask is not None and ask < fair - edge:
                return order("buy", ask, 4), "Buying ask below external fair"
            if bid is not None and bid > fair + edge:
                return order("sell", bid, 4), "Selling bid above external fair"
            return [Quote("buy", max(1, round(fair - spread * 1.5)), 4),
                    Quote("sell", min(10**12, round(fair + spread * 1.5)), 4)], "Resting orders around external fair"

        side = rng.choice(("buy", "sell"))
        if rng.random() < 0.75:
            price = (ask if ask is not None else fair + spread) if side == "buy" else (bid if bid is not None else fair - spread)
        else:
            price = fair + rng.uniform(-2, 2) * spread
        return order(side, price, rng.randint(1, 4)), f"Random {side} · liquidity demand"


class Simulation:
    interval_seconds = 0.8

    def __init__(self, market: "Market", seed: int | None = None):
        self.market = market
        # A separate generator keeps agent decisions independent of the fair path.
        self.rng = random.Random(seed)
        self.running = False
        self.rounds = 0
        self.history: deque[float] = deque(maxlen=120)
        self.agents = [
            Agent("atlas", "Atlas", "Market maker", "Provides two-sided liquidity and skews quotes to reduce inventory.", "#607af0"),
            Agent("pulse", "Pulse", "Momentum", "Follows the recent direction of the external fair price.", "#af7ce0"),
            Agent("echo", "Echo", "Mean reversion", "Trades back toward a rolling mean of the fair price.", "#e8a14b"),
            Agent("prism", "Prism", "Fair value", "Takes mispriced liquidity and rests quotes around external fair.", "#279e95"),
            Agent("drift", "Drift", "Noise trader", "Generates randomized buy and sell flow with varied order sizes.", "#df7e94"),
        ]

    def cancel_orders(self, agent_id: str):
        for order in list(self.market.engine.active.values()):
            if order.agent_id == agent_id:
                self.market.engine.cancel(order.order_id, agent_id)

    def set_running(self, running: bool):
        self.running = running
        if not running:
            for agent in self.agents:
                self.cancel_orders(agent.agent_id)
                agent.signal = "Simulation paused"

    def set_enabled(self, agent_id: str, enabled: bool):
        agent = next((a for a in self.agents if a.agent_id == agent_id), None)
        if agent is None:
            raise KeyError(agent_id)
        agent.enabled = enabled
        if not enabled:
            self.cancel_orders(agent_id)
            agent.signal = "Agent paused"

    def step(self):
        """One round; caller holds the market lock, just like external order entry."""
        if not self.running:
            return
        self.rounds += 1
        fair = self.market.fair()["price"]
        for agent in self.agents:
            if not agent.enabled:
                continue
            # Replace rather than accumulate stale orders on every decision.
            self.cancel_orders(agent.agent_id)
            account = self.market.engine.accounts.get(agent.agent_id, {})
            position = account.get("position", 0)
            quotes, agent.signal = agent.decide(fair, self.history, self.market.engine.book(), position, self.rng)
            for quote in quotes:
                # Reserve capacity for resting orders on the same side. This caps
                # worst-case exposure even if every outstanding order gets filled.
                position = self.market.engine.accounts.get(agent.agent_id, {}).get("position", 0)
                reserved = sum(o.remaining for o in self.market.engine.active.values()
                               if o.agent_id == agent.agent_id and o.side == quote.side)
                capacity = agent.position_limit - (position if quote.side == "buy" else -position) - reserved
                quantity = min(quote.quantity, capacity)
                if quantity <= 0:
                    agent.signal = "Position limit · reducing exposure only"
                    continue
                self.market.engine.place(OrderRequest(
                    agent_id=agent.agent_id, side=quote.side,
                    price=max(1, min(10**12, quote.price)), quantity=quantity,
                ))
                agent.orders_submitted += 1

    def snapshot(self) -> dict:
        fair = self.market.fair()["price"]
        rows = []
        for agent in self.agents:
            account = self.market.engine.accounts.get(agent.agent_id, {})
            position = account.get("position", 0)
            cash = account.get("cash", 0)
            rows.append({
                **vars(agent), "position": position, "cash": cash,
                "pnl": cash + position * fair, "volume": account.get("volume", 0),
                "fills": account.get("fills", 0),
                "open_orders": sum(o.agent_id == agent.agent_id for o in self.market.engine.active.values()),
            })
        return {"running": self.running, "rounds": self.rounds, "agents": rows}

    async def run(self):
        while True:
            await asyncio.sleep(self.interval_seconds)
            async with self.market.lock:
                if self.running:
                    self.step()
                    self.market.publish()
