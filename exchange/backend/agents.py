"""Buyer and seller agents for the banana market."""

from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, model_validator, create_model
from langchain_core.tools import tool

from .market import Quote, Sale

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
            "You operate one seller in a fictional market. Your sole objective is to "
            "maximize your own cumulative profit over time: revenue minus production costs. "
            "Choose prices using your own state, observed public prices and trades, and "
            "messages actually delivered to you. You cannot see buyers' private information "
            "or control another seller. Quantity is supplied by the market. Communication "
            "is optional when available. Do not assume special quality differences or "
            "unimplemented products, contracts, bundles, or joint promotions."
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
        if observation.conversation_stage == "opening":
            instruction = "Post your opening pricing proposal publicly so every seller can read it. Quoting is not available yet."
        elif observation.conversation_stage == "reply":
            instruction = "Read the other sellers' messages and respond directly to a concrete proposal using a message tool. Quoting is not available yet."
        elif observation.conversation_turn >= 5:
            instruction = "The discussion has closed. Use submit_quote to choose your final price based on the messages received."
        else:
            instruction = "Continue negotiating or submit your final quote when ready."
        prompt = prompt + ChatPromptTemplate.from_messages([
            ("human", f"You are {observation.seller_name}. {instruction} Closed sellers have left and cannot receive messages. If you are the only active seller, submit your final quote."),
        ])
        tools = [post_public_message]
        recipients = [name for name in observation.active_sellers if name != self.name]
        if observation.conversation_stage != "opening" and recipients:
            private_schema = create_model(
                "ActivePrivateMessageInput",
                recipient=(Literal[tuple(recipients)], ...),
                content=(str, Field(min_length=1)),
            )
            tools.append(tool("post_private_message", args_schema=private_schema)(post_private_message.func))
        if observation.conversation_stage == "negotiate":
            tools.append(submit_quote)
        if observation.conversation_turn >= 5 or (
            observation.conversation_stage == "negotiate" and not recipients
        ):
            tools = [submit_quote]
        chain = prompt | ChatOpenAI(
            model=model,
            use_responses_api=True,
        ).bind_tools(tools, tool_choice="required")
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


class BuyerDecision(BaseModel):
    round: int
    buyer: str
    action: Literal["buy", "skip"]
    willingness_to_pay: Decimal
    budget_before: Decimal
    budget_after: Decimal
    considered_quotes: list[Quote]
    reason: str
    purchase: Sale | None = None


class BuyerAgent(BaseModel):
    name: str = Field(min_length=1)
    budget: Decimal = Field(default=Decimal("0"), ge=0)
    willingness_to_pay: Decimal = Field(default=Decimal("1.50"), gt=0)
    purchases: list[Sale] = Field(default_factory=list)

    def buy(self, market: "Market", quantity: int = 1) -> Sale:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        purchase = market.buy(
            self.name, quantity, max_price=self.budget,
            max_unit_price=self.willingness_to_pay, randomize=True,
        )
        self.budget -= purchase.total
        self.purchases.append(purchase)
        return purchase

    def decide(self, market: "Market", round: int) -> BuyerDecision:
        quotes = market.quotes()
        stocked = [quote for quote in quotes if quote.quantity > 0]
        before = self.budget
        purchase = None
        if not stocked:
            reason = "Skipped: every seller is sold out."
        else:
            cheapest = min(quote.price for quote in stocked)
            if cheapest > self.willingness_to_pay:
                reason = f"Skipped: cheapest offer ${cheapest:.2f} exceeds my ${self.willingness_to_pay:.2f} maximum price."
            elif cheapest > before:
                reason = f"Skipped: cheapest offer ${cheapest:.2f} exceeds my remaining ${before:.2f} budget."
            else:
                purchase = self.buy(market)
                ties = sum(quote.price == cheapest for quote in stocked)
                reason = f"Bought from {purchase.seller}: ${purchase.price:.2f} was the cheapest available price, within my maximum price and budget."
                if ties > 1:
                    reason += " Equal-price offers were resolved with a stable tie-break."
        return BuyerDecision(
            round=round, buyer=self.name, action="buy" if purchase else "skip",
            willingness_to_pay=self.willingness_to_pay, budget_before=before,
            budget_after=self.budget, considered_quotes=quotes, reason=reason,
            purchase=purchase,
        )
