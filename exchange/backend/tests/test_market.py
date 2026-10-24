from decimal import Decimal

import pytest

from backend import BuyerAgent, Experiment, Market, MarketMessage, MessageBus, SellerAgent


def test_market_starts_with_three_banana_quotes():
    market = Market()

    assert len(market.quotes()) == 3
    assert {quote.product for quote in market.quotes()} == {"banana"}
    assert [quote.price for quote in market.quotes()] == [
        Decimal("1.00"), Decimal("1.10"), Decimal("1.20")
    ]


def test_multiple_agents_make_independent_purchase_choices():
    market = Market()
    agents = [BuyerAgent(name=name, budget=Decimal("2.00")) for name in ("one", "two")]

    purchases = [agent.buy(market) for agent in agents]

    assert len({purchase.seller for purchase in purchases}) > 1
    assert sum(seller.quantity for seller in market.sellers) == 28
    assert all(agent.budget == Decimal("1.00") for agent in agents)


def test_buyer_purchases_one_banana_per_round():
    market = Market()
    buyer = BuyerAgent(name="buyer", budget=Decimal("5.00"))

    purchase = buyer.buy(market)

    assert purchase.quantity == 1
    assert buyer.budget == Decimal("4.00")


def test_agent_cannot_buy_without_enough_budget():
    market = Market()
    agent = BuyerAgent(name="careful", budget=Decimal("0.50"))

    with pytest.raises(ValueError, match="cannot afford"):
        agent.buy(market)

    assert market.sellers[0].quantity == 10
    assert not market.purchases


def test_market_accepts_three_custom_sellers():
    market = Market(sellers=[
        SellerAgent(name="a", price=Decimal("2.00")),
        SellerAgent(name="b", price=Decimal("1.50")),
        SellerAgent(name="c", price=Decimal("3.00")),
    ])

    assert market.buy("buyer").seller == "b"


def test_seller_inventory_decreases_over_time():
    seller = SellerAgent(name="seller", price=Decimal("1.00"), quantity=1000)
    seller.sell(4)

    assert seller.quantity == 996
    assert seller.initial_quantity == 1000


def test_seller_prompt_has_profit_objective_without_buyer_private_information():
    prompt = SellerAgent(name="seller", price=Decimal("1.00")).prompt

    assert "maximize your own profit" in prompt
    assert "buyers' private information" in prompt


def test_message_bus_supports_public_and_private_messages():
    bus = MessageBus()
    private = MarketMessage(sender="a", recipient="b", content="hello", round=1)
    public = MarketMessage(sender="a", content="hello", round=1)

    bus.send(private)
    bus.send(public)

    assert bus.receive("b", 2) == [private, public]
    assert bus.receive("c", 2) == [public]
    assert len(bus.messages) == 2


def test_experiment_runs_buyers_and_seller_communication():
    experiment = Experiment(
        buyers=[BuyerAgent(name="buyer", budget=Decimal("10.00"))],
        rounds=2,
    )

    purchases = experiment.run()

    assert len(purchases) == 1
    assert experiment.round == 2
    assert len(experiment.bus.messages) == 6