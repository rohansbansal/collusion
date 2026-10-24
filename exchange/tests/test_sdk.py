"""SDK contracts, validation, ambiguous writes, and reconnect behavior."""

import asyncio
import json
from contextlib import aclosing
from importlib import import_module

import httpx
import pytest
from pydantic import ValidationError

from exchange_client import (
    ExchangeClient, ExchangeError, InvalidOrder, MarketSnapshot, OrderNotFound, OrderNotOpen,
)

sdk = import_module("exchange_client.client")
SNAPSHOT = {
    "type": "snapshot", "sequence": 1, "timestamp": "2026-10-24T17:00:00Z",
    "book": {"bids": [{"price": 99, "quantity": 2}], "asks": []},
    "fair": {"price": 100.5, "updated_at": "2026-10-24T17:00:00Z"},
    "recent_trades": [],
}
ORDER = {
    "order_id": "order-1", "agent_id": "test-agent", "side": "buy", "price": 99,
    "quantity": 5, "remaining": 3, "status": "open", "sequence": 1,
}


def test_typed_http_client_and_base_path():
    requests = []

    def respond(request):
        requests.append(request)
        path = request.url.path
        if request.method == "POST":
            assert json.loads(request.content) == {
                "agent_id": "test-agent", "side": "buy", "price": 99, "quantity": 5,
            }
            return httpx.Response(201, json={"order": ORDER, "trades": [], "sequence": 1})
        if request.method == "DELETE":
            assert request.url.params["agent_id"] == "test-agent"
            return httpx.Response(200, json={"order": {**ORDER, "status": "cancelled"}, "sequence": 2})
        if path.endswith("/orders"):
            assert request.url.params["agent_id"] == "test-agent"
            return httpx.Response(200, json={"orders": [ORDER]})
        return httpx.Response(200, json=SNAPSHOT["fair"] if path.endswith("/fair") else SNAPSHOT)

    async def run():
        async with ExchangeClient(
            "test-agent", "https://example.com/market/", transport=httpx.MockTransport(respond),
        ) as agent:
            result = await agent.buy(99, 5)
            assert result.order.filled_quantity == 2
            assert (await agent.orders())[0].order_id == result.order.order_id
            assert (await agent.cancel_order(result.order.order_id)).order.status == "cancelled"
            assert (await agent.fair()).price == 100.5
            snapshot = await agent.snapshot()
            assert isinstance(snapshot, MarketSnapshot)
            assert snapshot.book.best_bid.price == 99
            assert snapshot.book.best_ask is None
            assert snapshot.timestamp.utcoffset().total_seconds() == 0
            assert snapshot.model_dump(mode="json")["fair"]["price"] == 100.5
        assert agent.http.is_closed

    asyncio.run(run())
    assert all(request.url.path.startswith("/market/") for request in requests)


@pytest.mark.parametrize("status,error", [
    (404, OrderNotFound), (409, OrderNotOpen), (422, InvalidOrder), (503, ExchangeError),
])
def test_api_errors_preserve_details(status, error):
    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(status, json={"detail": "reason"}))
        async with ExchangeClient("a", transport=transport) as agent:
            with pytest.raises(error) as caught:
                await agent.cancel_order("order-1")
            assert caught.value.status_code == status
            assert caught.value.detail == "reason"
            assert caught.value.response.status_code == status
    asyncio.run(run())


def test_html_server_error():
    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(502, text="Bad gateway"))
        async with ExchangeClient("a", transport=transport) as agent:
            with pytest.raises(ExchangeError, match="Bad gateway"):
                await agent.snapshot()
    asyncio.run(run())


@pytest.mark.parametrize("side,price,quantity", [
    ("bad", 100, 1), ("buy", 0, 1), ("buy", True, 1), ("sell", 100, 1.1),
])
def test_invalid_order_rejected_before_network(side, price, quantity):
    def unexpected(request):
        pytest.fail("Invalid order reached the network")

    async def run():
        async with ExchangeClient("a", transport=httpx.MockTransport(unexpected)) as agent:
            with pytest.raises(ValidationError):
                await agent.place_order(side, price, quantity)
    asyncio.run(run())


def test_timeout_does_not_retry_order():
    attempts = []

    def timeout(request):
        attempts.append(request)
        raise httpx.ReadTimeout("Response lost after order submission", request=request)

    async def run():
        async with ExchangeClient("a", transport=httpx.MockTransport(timeout)) as agent:
            with pytest.raises(httpx.ReadTimeout):
                await agent.sell(101, 3)
    asyncio.run(run())
    assert len(attempts) == 1


@pytest.mark.parametrize("kwargs", [
    {"agent_id": " "}, {"agent_id": "a" * 129}, {"base_url": "ftp://example.com"},
    {"base_url": "https://example.com?key=value"}, {"timeout": 0}, {"timeout": float("nan")},
])
def test_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        ExchangeClient(**{"agent_id": "a", **kwargs})


class FakeSocket:
    def __init__(self, *messages):
        self.messages = messages
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def __aiter__(self):
        for message in self.messages:
            yield message


def test_stream_reconnects_then_closes_on_early_exit(monkeypatch):
    connections = []
    socket = FakeSocket(json.dumps(SNAPSHOT))

    def connect(url):
        connections.append(url)
        if len(connections) == 1:
            raise OSError("Server restarting")
        return socket

    monkeypatch.setattr(sdk, "connect", connect)

    async def run():
        async with ExchangeClient("a", "https://example.com/market") as agent:
            async with aclosing(agent.stream(reconnect=True, retry_delay=0.001)) as feed:
                snapshot = await anext(feed)
                assert snapshot.fair.price == 100.5
            assert socket.closed
    asyncio.run(run())
    assert connections == ["wss://example.com/market/ws"] * 2


def test_stream_retries_are_bounded(monkeypatch):
    attempts = []

    def connect(url):
        attempts.append(url)
        raise OSError("Offline")

    monkeypatch.setattr(sdk, "connect", connect)

    async def run():
        async with ExchangeClient("a") as agent:
            with pytest.raises(OSError, match="Offline"):
                await anext(agent.stream(reconnect=True, max_retries=2, retry_delay=0.001))
    asyncio.run(run())
    assert len(attempts) == 3  # Initial attempt plus two retries.


def test_stream_does_not_retry_by_default(monkeypatch):
    def connect(url):
        raise OSError("Offline")
    monkeypatch.setattr(sdk, "connect", connect)

    async def run():
        async with ExchangeClient("a") as agent:
            with pytest.raises(OSError, match="Offline"):
                await anext(agent.stream())
    asyncio.run(run())


def test_stream_does_not_hide_invalid_data(monkeypatch):
    socket = FakeSocket("not-json")
    monkeypatch.setattr(sdk, "connect", lambda url: socket)

    async def run():
        async with ExchangeClient("a") as agent:
            with pytest.raises(ValidationError):
                await anext(agent.stream(reconnect=True))
        assert socket.closed
    asyncio.run(run())
