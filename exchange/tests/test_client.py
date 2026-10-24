"""Exercise the agent SDK over real HTTP and WebSocket connections."""

import asyncio
import socket
import threading
import time
from contextlib import aclosing

import uvicorn

from exchange import ExchangeClient
from exchange.api import MarketConfig, create_app
from exchange_client import ExchangeClient as AgentClient, OrderNotOpen

import pytest


def test_agent_client_end_to_end():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(
            create_app(MarketConfig(interval_seconds=0.02, seed=42)),
            log_level="error", loop="asyncio", ws="websockets-sansio",
        ))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started:
                assert thread.is_alive() and time.monotonic() < deadline, "Server did not start"
                time.sleep(0.01)

            async def run():
                url = f"http://127.0.0.1:{sock.getsockname()[1]}"
                async with ExchangeClient("seller", url) as seller, ExchangeClient("buyer", url) as buyer:
                    async with aclosing(buyer.stream()) as stream:
                        initial = await anext(stream)
                        assert initial["book"] == {"bids": [], "asks": []}
                        sale = await seller.place_order("sell", 10000, 5)
                        purchase = await buyer.place_order("buy", 10001, 2)
                        assert purchase["trades"][0]["price"] == 10000
                        assert (await seller.orders())[0]["remaining"] == 3
                        async with asyncio.timeout(5):
                            async for snapshot in stream:
                                if snapshot["sequence"] >= purchase["sequence"]:
                                    assert snapshot["book"]["asks"] == [{"price": 10000, "quantity": 3}]
                                    break
                        await seller.cancel_order(sale["order"]["order_id"])
                        assert (await buyer.snapshot())["book"]["asks"] == []
                        assert (await buyer.orders())[0]["status"] == "filled"

                async with AgentClient("typed-seller", url) as seller, AgentClient("typed-buyer", url) as buyer:
                    async with aclosing(buyer.stream()) as feed:
                        assert (await anext(feed)).book.best_ask is None
                        sale = await seller.sell(100, 5)
                        fill = await buyer.buy(101, 2)
                        assert fill.order.filled_quantity == 2
                        assert fill.trades[0].maker_order_id == sale.order.order_id
                        assert (await seller.orders())[0].remaining == 3
                        assert (await buyer.fair()).price > 0
                        async with asyncio.timeout(5):
                            async for snapshot in feed:
                                if snapshot.sequence >= fill.sequence:
                                    assert snapshot.book.best_ask.quantity == 3
                                    break
                        await seller.cancel_order(sale.order.order_id)
                        assert (await buyer.snapshot()).book.best_ask is None
                        with pytest.raises(OrderNotOpen):
                            await buyer.cancel_order(fill.order.order_id)

            asyncio.run(run())
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive(), "Server did not stop"
