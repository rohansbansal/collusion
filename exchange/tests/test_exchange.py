import asyncio
import math

import pytest
from fastapi.testclient import TestClient

from exchange.api import Market, MarketConfig, create_app
from exchange.engine import MatchingEngine, OrderRequest


def place(engine, agent, side, price, quantity):
    return engine.place(OrderRequest(agent_id=agent, side=side, price=price, quantity=quantity))


def test_price_time_priority_and_partial_fills():
    engine = MatchingEngine()
    expensive = place(engine, "a", "sell", 102, 3)["order"]
    first = place(engine, "b", "sell", 100, 2)["order"]
    second = place(engine, "c", "sell", 100, 4)["order"]
    result = place(engine, "d", "buy", 103, 7)
    assert [(t["maker_order_id"], t["price"], t["quantity"]) for t in result["trades"]] == [
        (first["order_id"], 100, 2), (second["order_id"], 100, 4), (expensive["order_id"], 102, 1),
    ]
    assert result["order"]["status"] == "filled"
    assert engine.book() == {"bids": [], "asks": [{"price": 102, "quantity": 2}]}
    assert engine.orders[expensive["order_id"]].status == "open"


def test_sell_hits_highest_bid_and_rests_remainder():
    engine = MatchingEngine()
    place(engine, "a", "buy", 99, 5)
    place(engine, "b", "buy", 101, 2)
    result = place(engine, "c", "sell", 100, 4)
    assert [(t["price"], t["quantity"]) for t in result["trades"]] == [(101, 2)]
    assert result["order"]["remaining"] == 2
    assert result["order"]["status"] == "open"
    assert engine.book() == {
        "bids": [{"price": 99, "quantity": 5}], "asks": [{"price": 100, "quantity": 2}],
    }


def test_book_aggregates_and_cancel_preserves_history():
    engine = MatchingEngine()
    first = place(engine, "a", "buy", 100, 2)["order"]
    place(engine, "b", "buy", 100, 3)
    assert engine.book()["bids"] == [{"price": 100, "quantity": 5}]
    with pytest.raises(KeyError):
        engine.cancel(first["order_id"], "b")
    engine.cancel(first["order_id"], "a")
    assert engine.book()["bids"] == [{"price": 100, "quantity": 3}]
    assert engine.agent_orders("a")[0]["status"] == "cancelled"
    with pytest.raises(ValueError):
        engine.cancel(first["order_id"], "a")


@pytest.fixture
def client():
    with TestClient(create_app(MarketConfig(interval_seconds=3600, seed=1))) as client:
        yield client


@pytest.mark.parametrize("change", [
    {"quantity": 0}, {"quantity": -1}, {"quantity": 1.5}, {"quantity": True},
    {"price": 0}, {"price": "100"}, {"price": 1.1}, {"price": 10**12 + 1},
    {"side": "invalid"}, {"agent_id": ""}, {"agent_id": "   "}, {"extra": 1},
])
def test_invalid_orders_do_not_mutate_market(client, change):
    payload = {"agent_id": "a", "side": "buy", "price": 100, "quantity": 1, **change}
    assert client.post("/orders", json=payload).status_code == 422
    assert client.get("/book").json()["sequence"] == 0


def test_rest_and_stream_order_lifecycle(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["book"] == {"bids": [], "asks": []}
        response = client.post("/orders", json={
            "agent_id": "maker", "side": "sell", "price": 100, "quantity": 5,
        })
        assert response.status_code == 201
        order_id = response.json()["order"]["order_id"]
        update = ws.receive_json()
        assert update["sequence"] == 1
        assert update["book"]["asks"] == [{"price": 100, "quantity": 5}]
        assert client.delete(f"/orders/{order_id}", params={"agent_id": "wrong"}).status_code == 404
        fill = client.post("/orders", json={
            "agent_id": "taker", "side": "buy", "price": 100, "quantity": 2,
        }).json()
        update = ws.receive_json()
        assert update["recent_trades"] == fill["trades"]
        assert update["book"]["asks"] == [{"price": 100, "quantity": 3}]
        assert client.get("/orders", params={"agent_id": "maker"}).json()["orders"][0]["remaining"] == 3
        assert client.delete(f"/orders/{order_id}", params={"agent_id": "maker"}).status_code == 200
        assert ws.receive_json()["book"]["asks"] == []
        assert client.delete(f"/orders/{order_id}", params={"agent_id": "maker"}).status_code == 409
        taker_id = fill["order"]["order_id"]
        assert client.delete(f"/orders/{taker_id}", params={"agent_id": "taker"}).status_code == 409
    # Reconnecting always returns current state, even when no new events arrive.
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["sequence"] == 3


def test_fair_stream_updates_without_orders():
    with TestClient(create_app(MarketConfig(interval_seconds=0.01, seed=7))) as client:
        with client.websocket_connect("/ws") as ws:
            first = ws.receive_json()
            second = ws.receive_json()
            assert second["sequence"] > first["sequence"]
            assert second["fair"]["price"] != first["fair"]["price"]
            assert second["book"] == {"bids": [], "asks": []}
            assert client.get("/fair").json()["price"] > 0


def test_fair_is_seeded_positive_and_independent_of_orders():
    a = Market(MarketConfig(seed=4))
    b = Market(MarketConfig(seed=4))
    place(a.engine, "a", "buy", 100, 1)
    values = []
    for _ in range(100):
        a.step_fair()
        b.step_fair()
        values.append(a.fair()["price"])
        assert a.fair()["price"] == b.fair()["price"]
        assert math.isfinite(values[-1]) and values[-1] >= 1
    assert len(set(values)) == 100


def test_slow_subscriber_receives_latest_full_snapshot():
    market = Market(MarketConfig())
    queue = asyncio.Queue(maxsize=1)
    market.subscribers.add(queue)
    market.publish()
    place(market.engine, "a", "buy", 100, 1)
    market.publish()
    assert queue.qsize() == 1
    snapshot = queue.get_nowait()
    assert snapshot["sequence"] == 2
    assert snapshot["book"]["bids"] == [{"price": 100, "quantity": 1}]


@pytest.mark.parametrize("kwargs", [
    {"initial_fair": 0}, {"interval_seconds": 0}, {"volatility": -1},
    {"drift": float("nan")}, {"volatility": float("inf")},
])
def test_invalid_price_config(kwargs):
    with pytest.raises(ValueError):
        MarketConfig(**kwargs)
