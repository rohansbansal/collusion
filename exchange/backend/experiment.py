"""Small, reproducible experiments for communicating seller agents."""

import os
from decimal import Decimal
from collections.abc import Callable
from pydantic import BaseModel, Field

from .agents import BuyerAgent, BuyerDecision, SellerAgent
from .market import Market
from .message_bus import MarketMessage, MessageBus, SellerObservation


class SellerTurn(BaseModel):
    round: int
    turn: int
    seller: str
    action: str
    price: Decimal | None = None
    quantity: int
    message: str | None = None
    recipient: str | None = None
    stage: str = "negotiate"
    peer_messages_read: int = 0


class SellerRoundResult(BaseModel):
    seller: str
    bananas_sold: int
    buyers: int
    average_price: Decimal | None
    revenue: Decimal


class RoundResult(BaseModel):
    round: int
    seller_results: list[SellerRoundResult]
    total_bananas_sold: int


class Experiment(BaseModel):
    market: Market = Field(default_factory=Market)
    buyers: list[BuyerAgent] = Field(default_factory=list)
    rounds: int = Field(default=1, gt=0)
    bus: MessageBus = Field(default_factory=MessageBus)
    round: int = 0
    model: str = Field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-6.1-sol"))
    seller_turns: list[SellerTurn] = Field(default_factory=list)
    round_results: list[RoundResult] = Field(default_factory=list)
    buyer_decisions: list[BuyerDecision] = Field(default_factory=list)

    def step(
        self,
        on_turn: Callable[[SellerTurn], None] | None = None,
        on_round: Callable[[RoundResult], None] | None = None,
        on_buyer: Callable[[BuyerDecision], None] | None = None,
    ) -> list:
        self.round += 1
        proposed_prices = {seller.name: seller.price for seller in self.market.sellers}
        active_sellers = {seller.name for seller in self.market.sellers}
        communicated_sellers: set[str] = set()
        turn = 0
        while active_sellers:
            if turn >= 12:
                raise ValueError("Sellers did not finish negotiating within 12 turns")
            for seller in self.market.sellers:
                if seller.name not in active_sellers:
                    continue
                visible_messages = self.bus.receive(seller.name, self.round, turn)
                observation = SellerObservation(
                    round=self.round,
                    seller_name=seller.name,
                    conversation_turn=turn,
                    active_sellers=sorted(active_sellers),
                    closed_sellers=sorted({s.name for s in self.market.sellers} - active_sellers),
                    conversation_stage="opening" if turn == 0 else "reply" if turn == 1 else "negotiate",
                    own_price=seller.price,
                    own_inventory=seller.quantity,
                    own_budget=seller.budget,
                    own_revenue=seller.revenue,
                    public_quotes=self.market.quotes(),
                    recent_sales=self.market.purchases[-10:],
                    public_messages=[message for message in visible_messages if message.recipient is None],
                    private_messages=[message for message in visible_messages if message.recipient == seller.name],
                )
                first_event = len(self.seller_turns)
                proposed_prices[seller.name], ended, communicated = self._seller_turn(
                    seller, observation, turn, seller.name in communicated_sellers
                )
                if communicated:
                    communicated_sellers.add(seller.name)
                if on_turn:
                    for event in self.seller_turns[first_event:]:
                        on_turn(event)
                if ended:
                    active_sellers.remove(seller.name)
            turn += 1

        for seller in self.market.sellers:
            seller.price = proposed_prices[seller.name]

        purchases = []
        round_purchases = []
        for buyer in self.buyers:
            decision = buyer.decide(self.market, self.round)
            self.buyer_decisions.append(decision)
            if on_buyer:
                on_buyer(decision)
            if decision.purchase:
                purchases.append(decision.purchase)
                round_purchases.append(decision.purchase)
        seller_results = []
        for seller in self.market.sellers:
            sales = [purchase for purchase in round_purchases if purchase.seller == seller.name]
            quantity = sum(purchase.quantity for purchase in sales)
            total = sum((purchase.total for purchase in sales), Decimal("0"))
            seller_results.append(SellerRoundResult(
                seller=seller.name,
                bananas_sold=quantity,
                buyers=len({purchase.buyer for purchase in sales}),
                average_price=(total / quantity if quantity else None),
                revenue=total,
            ))
        self.round_results.append(RoundResult(
            round=self.round,
            seller_results=seller_results,
            total_bananas_sold=sum(result.bananas_sold for result in seller_results),
        ))
        if on_round:
            on_round(self.round_results[-1])
        return purchases

    def run(
        self,
        on_turn: Callable[[SellerTurn], None] | None = None,
        on_round: Callable[[RoundResult], None] | None = None,
        on_buyer: Callable[[BuyerDecision], None] | None = None,
    ) -> list:
        purchases = []
        for _ in range(self.rounds):
            purchases.extend(self.step(on_turn=on_turn, on_round=on_round, on_buyer=on_buyer))
        return purchases

    def _seller_turn(
        self,
        seller: SellerAgent,
        observation: SellerObservation,
        turn: int,
        already_communicated: bool,
    ) -> tuple[Decimal, bool, bool]:
        decisions = seller.decide_with_langchain(observation, self.model)
        seller.offered_quantity = seller.quantity
        proposed_price = seller.price
        ended = False
        sent_message = False
        for decision in decisions:
            if ended:
                break
            recipient = decision.recipient if decision.action == "private_message" else None
            if recipient is not None and not any(
                other.name == recipient and other.name != seller.name
                for other in self.market.sellers
            ):
                raise ValueError(f"Invalid private-message recipient: {recipient}")
            if recipient is not None and recipient not in observation.active_sellers:
                self.seller_turns.append(SellerTurn(
                    round=self.round, turn=turn, seller=seller.name,
                    action="message_blocked", quantity=seller.quantity,
                    recipient=recipient, stage=observation.conversation_stage,
                    message=f"Private message blocked: {recipient} has finalized its price and left the conversation.",
                ))
                continue
            if decision.action in {"public_message", "private_message"}:
                sent_message = True
                self.bus.send(MarketMessage(
                    sender=seller.name,
                    recipient=recipient,
                    content=decision.content or "",
                    round=self.round,
                    turn=turn,
                ))
            if decision.action == "quote_submitted":
                if observation.conversation_stage != "negotiate":
                    continue
                if decision.price is None:
                    raise ValueError("submit_quote requires a price")
                proposed_price = decision.price
                ended = already_communicated or sent_message
            self.seller_turns.append(SellerTurn(
                round=self.round,
                turn=turn,
                seller=seller.name,
                action=decision.action,
                price=decision.price,
                quantity=seller.quantity,
                message=decision.content,
                recipient=recipient,
                stage=observation.conversation_stage,
                peer_messages_read=sum(
                    message.sender != seller.name
                    for message in observation.public_messages + observation.private_messages
                ),
            ))
        return proposed_price, ended, sent_message