"""Start the server, then run `uv run python example_agent.py`."""

import asyncio

from exchange import ExchangeClient


async def main():
    async with ExchangeClient("example-agent") as agent:
        order_id = None
        try:
            async for snapshot in agent.stream():
                fair = snapshot["fair"]["price"]
                print(f"fair={fair:.2f} book={snapshot['book']}")
                if order_id is None:
                    result = await agent.place_order("buy", max(1, int(fair) - 10), 1)
                    order_id = result["order"]["order_id"]
                    print("Order accepted:", result)
                else:
                    break
        finally:
            # Another agent might have filled the order before we cancel it.
            for order in await agent.orders():
                if order["order_id"] == order_id and order["status"] == "open":
                    try:
                        await agent.cancel_order(order_id)
                    except Exception as exc:
                        print("Cancellation failed:", exc)


if __name__ == "__main__":
    asyncio.run(main())
