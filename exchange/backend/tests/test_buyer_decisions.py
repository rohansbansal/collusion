from decimal import Decimal
from backend.agents import BuyerAgent, SellerAgent
from backend.api import ExperimentRequest, make_buyers
from backend.market import Market


def test_cheaper_offer_wins_even_if_another_is_affordable():
    market = Market()
    buyer = BuyerAgent(name='buyer', budget=Decimal('10'), willingness_to_pay=Decimal('2'))
    decision = buyer.decide(market, 1)
    assert decision.purchase.seller == 'seller-1'
    assert decision.budget_after == Decimal('9')
    assert len(decision.considered_quotes) == 3
    assert decision.considered_quotes[0].quantity == 1000
    assert market.sellers[0].quantity == 999


def test_cash_and_willingness_to_pay_are_separate_and_skips_do_not_trade():
    market = Market()
    expensive = BuyerAgent(name='sensitive', budget=Decimal('10'), willingness_to_pay=Decimal('.90')).decide(market, 1)
    poor = BuyerAgent(name='poor', budget=Decimal('.50'), willingness_to_pay=Decimal('2')).decide(market, 1)
    empty = BuyerAgent(name='empty', budget=Decimal('0')).decide(market, 1)
    assert all(d.action == 'skip' for d in (expensive, poor, empty))
    assert 'maximum price' in expensive.reason
    assert 'budget' in poor.reason
    assert all(d.budget_before == d.budget_after for d in (expensive, poor, empty))
    assert not market.purchases


def test_demand_falls_when_all_prices_exceed_some_personal_limits():
    def demand(price):
        market = Market(sellers=[SellerAgent(name=f'seller-{n}', price=Decimal(price)) for n in range(3)])
        return sum(buyer.decide(market, 1).action == 'buy' for buyer in make_buyers(ExperimentRequest(buyers=3)))
    assert demand('.80') == 3
    assert demand('1.00') == 2
    assert demand('2.20') == 0


def test_sold_out_cheapest_offer_is_excluded():
    market = Market()
    market.sellers[0].quantity = 0
    buyer = BuyerAgent(name='buyer', budget=Decimal('10'))
    assert buyer.decide(market, 1).purchase.seller == 'seller-2'
    for seller in market.sellers:
        seller.quantity = 0
    assert buyer.decide(market, 2).action == 'skip'
    assert buyer.budget == Decimal('8.90')


def test_budget_carries_over_between_rounds():
    buyer = BuyerAgent(name='buyer', budget=Decimal('1'))
    market = Market()
    assert buyer.decide(market, 1).action == 'buy'
    assert buyer.decide(market, 2).action == 'skip'
    assert buyer.budget == 0
