"""Validated wire models. Prices use the exchange's integer tick units."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Side = Literal["buy", "sell"]
OrderStatus = Literal["open", "filled", "cancelled"]


class Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class OrderRequest(Model):
    agent_id: str = Field(min_length=1, max_length=128, pattern=r"\S")
    side: Side
    price: int = Field(gt=0, le=10**12, strict=True)
    quantity: int = Field(gt=0, le=10**9, strict=True)


class Order(Model):
    order_id: str
    agent_id: str
    side: Side
    price: int
    quantity: int
    remaining: int
    status: OrderStatus
    sequence: int

    @property
    def filled_quantity(self) -> int:
        return self.quantity - self.remaining


class Trade(Model):
    trade_id: str
    price: int
    quantity: int
    maker_order_id: str
    taker_order_id: str
    buyer_agent_id: str
    seller_agent_id: str


class OrderResult(Model):
    order: Order
    trades: list[Trade]
    sequence: int


class CancelResult(Model):
    order: Order
    sequence: int


class BookLevel(Model):
    price: int
    quantity: int


class OrderBook(Model):
    bids: list[BookLevel]
    asks: list[BookLevel]

    @property
    def best_bid(self) -> BookLevel | None:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> BookLevel | None:
        return self.asks[0] if self.asks else None


class FairPrice(Model):
    price: float
    updated_at: datetime


class MarketSnapshot(Model):
    type: Literal["snapshot"]
    sequence: int
    timestamp: datetime
    book: OrderBook
    fair: FairPrice
    recent_trades: list[Trade]
