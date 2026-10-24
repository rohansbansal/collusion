"""Run a small local banana-market example."""

from decimal import Decimal

from backend import BuyerAgent, Market


def main():
    market = Market()
    agents = [BuyerAgent(name=f"buyer-{number}", budget=Decimal("10.00")) for number in range(1, 4)]
    for quote in market.quotes():
        print(f"{quote.seller}: {quote.quantity} bananas at ${quote.price}")
    for agent in agents:
        print(agent.buy(market))


if __name__ == "__main__":
    main()
