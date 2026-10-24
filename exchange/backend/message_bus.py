"""Seller communication models and in-memory message delivery."""

from decimal import Decimal
from pydantic import BaseModel, Field

from .market import Quote, Sale

class MarketMessage(BaseModel):
    sender: str
    recipient: str | None = None
    content: str
    round: int
    turn: int = 0


class PrivateMessageBoard(BaseModel):
    owner: str
    messages: list[MarketMessage] = Field(default_factory=list)


class SellerObservation(BaseModel):
    round: int
    own_price: Decimal
    own_inventory: int
    own_budget: Decimal
    own_revenue: Decimal
    public_quotes: list[Quote]
    recent_sales: list[Sale]
    public_messages: list[MarketMessage] = Field(default_factory=list)
    private_messages: list[MarketMessage] = Field(default_factory=list)


class MessageBus(BaseModel):
    messages: list[MarketMessage] = Field(default_factory=list)

    def send(self, message: MarketMessage) -> None:
        self.messages.append(message)

    def receive(self, agent: str, round: int, turn: int = 0) -> list[MarketMessage]:
        return [
            message for message in self.messages
            if (message.round < round or (message.round == round and message.turn < turn)) and (
                message.recipient == agent or message.recipient is None
            )
        ]