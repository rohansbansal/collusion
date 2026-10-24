"""Deterministic, single-market matching engine; prices are integer ticks."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class OrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1, max_length=128, pattern=r"\S")
    side: Literal["buy", "sell"]
    price: int = Field(gt=0, le=10**12, strict=True)
    quantity: int = Field(gt=0, le=10**9, strict=True)


@dataclass
class Order:
    order_id: str
    agent_id: str
    side: str
    price: int
    quantity: int
    remaining: int
    status: str
    sequence: int


class MatchingEngine:
    def __init__(self):
        self.orders: dict[str, Order] = {}
        self.active: dict[str, Order] = {}
        self.trades: list[dict] = []
        self.sequence = 0
        self.accounts: dict[str, dict] = {}
        self.trade_count = 0
        self.volume = 0

    def place(self, request: OrderRequest) -> dict:
        self.sequence += 1
        incoming = Order(
            str(uuid4()), request.agent_id, request.side, request.price,
            request.quantity, request.quantity, "open", self.sequence,
        )
        resting = sorted(
            (o for o in self.active.values()
             if o.remaining and o.status == "open" and o.side != incoming.side),
            key=lambda o: (o.price if incoming.side == "buy" else -o.price, o.sequence),
        )
        executions = []
        for maker in resting:
            if not incoming.remaining:
                break
            if (incoming.side == "buy" and maker.price > incoming.price) or (
                incoming.side == "sell" and maker.price < incoming.price
            ):
                break
            quantity = min(incoming.remaining, maker.remaining)
            incoming.remaining -= quantity
            maker.remaining -= quantity
            if not maker.remaining:
                maker.status = "filled"
                self.active.pop(maker.order_id)
            trade = {
                "trade_id": str(uuid4()), "price": maker.price, "quantity": quantity,
                "maker_order_id": maker.order_id, "taker_order_id": incoming.order_id,
                "buyer_agent_id": incoming.agent_id if incoming.side == "buy" else maker.agent_id,
                "seller_agent_id": incoming.agent_id if incoming.side == "sell" else maker.agent_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self.trade_count += 1
            self.volume += quantity
            for agent_id, direction in ((trade["buyer_agent_id"], 1), (trade["seller_agent_id"], -1)):
                account = self.accounts.setdefault(agent_id, {"position": 0, "cash": 0, "volume": 0, "fills": 0})
                account["position"] += direction * quantity
                account["cash"] -= direction * quantity * maker.price
                account["volume"] += quantity
                account["fills"] += 1
            executions.append(trade)
        if not incoming.remaining:
            incoming.status = "filled"
        self.orders[incoming.order_id] = incoming
        if incoming.remaining:
            self.active[incoming.order_id] = incoming
        self.trades = (self.trades + executions)[-100:]
        return {"order": asdict(incoming), "trades": executions}

    def cancel(self, order_id: str, agent_id: str) -> dict:
        order = self.orders.get(order_id)
        if order is None or order.agent_id != agent_id:
            raise KeyError("Order not found for this agent")
        if order.status != "open":
            raise ValueError("Only open orders can be cancelled")
        order.status = "cancelled"
        self.active.pop(order_id)
        return asdict(order)

    def book(self) -> dict:
        levels: dict[str, dict[int, int]] = {"buy": {}, "sell": {}}
        for order in self.active.values():
            if order.status == "open":
                side = levels[order.side]
                side[order.price] = side.get(order.price, 0) + order.remaining
        return {
            name: [{"price": p, "quantity": side[p]} for p in sorted(side, reverse=reverse)]
            for name, side, reverse in (
                ("bids", levels["buy"], True), ("asks", levels["sell"], False)
            )
        }

    def agent_orders(self, agent_id: str) -> list[dict]:
        return [asdict(o) for o in self.orders.values() if o.agent_id == agent_id]
