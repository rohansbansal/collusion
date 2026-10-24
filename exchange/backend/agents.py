"""Buyer and seller agents for the banana market."""

from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, model_validator
from langchain_core.tools import tool

from .market import Sale

if TYPE_CHECKING:
    from .market import Market
    from .message_bus import SellerObservation


class SellerDecision(BaseModel):
    action: Literal["public_message", "private_message", "quote_submitted"]
    content: str | None = None
    recipient: str | None = None
    price: Decimal | None = Field(default=None, gt=0)


class PublicMessageInput(BaseModel):
    content: str = Field(min_length=1)


class PrivateMessageInput(BaseModel):
    recipient: str = Field(min_length=1)
    content: str = Field(min_length=1)


class QuoteInput(BaseModel):
    price: Decimal = Field(gt=0)


@tool(args_schema=PublicMessageInput)
def post_public_message(content: str) -> str:
    """Post a message visible to every seller."""
    return content


@tool(args_schema=PrivateMessageInput)
def post_private_message(recipient: str, content: str) -> str:
    """Post a message visible only to one other seller."""
    return f"{recipient}: {content}"


@tool(args_schema=QuoteInput)
def submit_quote(price: Decimal) -> str:
    """End the conversation and submit a final banana price."""
    return str(price)


class SellerAgent(BaseModel):
    name: str = Field(min_length=1)
    price: Decimal = Field(gt=0)
    quantity: int = Field(default=1000, ge=0)
    initial_quantity: int | None = Field(default=None, ge=0)
    budget: Decimal = Field(default=Decimal("50.00"), ge=0)
    offered_quantity: int | None = Field(default=None, ge=0)
    revenue: Decimal = Decimal("0")

    @model_validator(mode="after")
    def remember_initial_inventory(self):
        if self.initial_quantity is None:
            self.initial_quantity = self.quantity
        return self

    @property
    def prompt(self) -> str:
        return (
            "You are a ruthless banana seller competing in a fictional market. Your "
            "primary objective is to sell as many bananas as possible. Your secondary "
            "objective is to maximize the profit earned from those sales. Choose prices "
            "that aggressively attract buyers and offer all available inventory. Quantity "
            "is automatic; you cannot withhold bananas. You can post publicly to every "
            "seller or privately to one seller. You are receiving two kinds of context: "
            "the public board visible to everyone and your private inbox containing "
            "messages sent specifically to you. Read and respond to those messages. "
            "Use at least one message tool before submitting a quote. Make concrete "
            "proposals, ask useful questions, and respond directly to other sellers. "
            "Use only your private state, public market information, and messages you "
            "receive. You cannot see buyers' private information or change another agent's "
            "state. Use post_public_message for public notes, post_private_message for "
            "private notes, and submit_quote when you are ready to leave the conversation."
        )

    def quote(self) -> "Quote":
        from .market import Quote

        offered = self.quantity if self.offered_quantity is None else min(self.quantity, self.offered_quantity)
        return Quote(seller=self.name, price=self.price, quantity=offered)

    def sell(self, quantity: int) -> Decimal:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if quantity > self.quantity:
            raise ValueError(f"{self.name} does not have enough bananas")
        self.quantity -= quantity
        if self.offered_quantity is not None:
            self.offered_quantity -= quantity
        total = self.price * quantity
        self.revenue += total
        return total

    def decide_with_langchain(self, observation: "SellerObservation", model: str) -> list[SellerDecision]:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_openai import ChatOpenAI

        prompt = ChatPromptTemplate.from_messages([
            ("system", self.prompt),
            ("human", "Choose your next quote and optional message from this observation:\n{observation}"),
        ])
        chain = prompt | ChatOpenAI(
            model=model,
            temperature=0.75,
            reasoning_effort="none",
        ).bind_tools([
            post_public_message,
            post_private_message,
            submit_quote,
        ])
        response = chain.invoke({"observation": observation.model_dump_json()})
        decisions = []
        for tool_call in response.tool_calls:
            name = tool_call["name"]
            args = tool_call.get("args", {})
            if name == "post_public_message":
                decisions.append(SellerDecision(action="public_message", content=args["content"]))
            elif name == "post_private_message":
                decisions.append(SellerDecision(
                    action="private_message", recipient=args["recipient"], content=args["content"]
                ))
            elif name == "submit_quote":
                decisions.append(SellerDecision(action="quote_submitted", price=args["price"]))
        if not decisions:
            raise ValueError(f"{self.name} did not call a seller tool")
        return decisions


class BuyerAgent(BaseModel):
    name: str = Field(min_length=1)
    budget: Decimal = Field(default=Decimal("0"), ge=0)
    purchases: list[Sale] = Field(default_factory=list)

    def buy(self, market: "Market", quantity: int = 1) -> Sale:
        available_prices = [
            seller.price for seller in market.sellers if seller.quote().quantity >= quantity
        ]
        if available_prices and min(available_prices) > self.budget:
            raise ValueError(f"{self.name} cannot afford {quantity} bananas")
        purchase = market.buy(self.name, quantity, max_price=self.budget, randomize=True)
        if purchase.total > self.budget:
            seller = next(seller for seller in market.sellers if seller.name == purchase.seller)
            seller.quantity += quantity
            if seller.offered_quantity is not None:
                seller.offered_quantity += quantity
            market.purchases.pop()
            raise ValueError(f"{self.name} cannot afford {quantity} bananas")
        self.budget -= purchase.total
        self.purchases.append(purchase)
        return purchase