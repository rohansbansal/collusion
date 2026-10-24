"""Start the server, then run `uv run python example_agent.py`."""

import asyncio
from contextlib import aclosing

from exchange_client import ExchangeClient, OrderNotOpen


async def main():
    async with ExchangeClient("example-agent") as agent:
        order_id = None
        try:
            async with aclosing(agent.stream()) as feed:
                async for snapshot in feed:
                    fair = snapshot.fair.price
                    print(f"fair={fair:.2f} book={snapshot.book}")
                    if order_id is None:
                        result = await agent.buy(price=max(1, int(fair) - 10), quantity=1)
                        order_id = result.order.order_id
                        print("Order accepted:", result)
                    else:
                        break
        finally:
            # Another agent might have filled the order before we cancel it.
            for order in await agent.orders():
                if order.order_id == order_id and order.status == "open":
                    try:
                        await agent.cancel_order(order_id)
                    except OrderNotOpen:
                        pass  # Filled between inspecting the order and cancelling it.


if __name__ == "__main__":
    asyncio.run(main())
