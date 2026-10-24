import pytest
from fastapi.testclient import TestClient

from exchange.api import Market, MarketConfig, create_app
from exchange.engine import OrderRequest


def run_round(market):
    market.step_fair()
    market.sample_history()
    market.simulation.step()


def test_strategies_react_differently_to_the_same_rising_market():
    market = Market(MarketConfig(seed=42))
    maker, momentum, reversion, value, noise = market.simulation.agents
    book = {"bids": [{"price": 10089, "quantity": 10}],
            "asks": [{"price": 10111, "quantity": 10}]}
    history = [10000] * 20 + [10100]
    buy, _ = momentum.decide(10100, history, book, 0, market.simulation.rng)
    sell, _ = reversion.decide(10100, history, book, 0, market.simulation.rng)
    assert buy[0].side == "buy" and buy[0].price == 10111
    assert sell[0].side == "sell" and sell[0].price == 10089
    quotes, _ = maker.decide(10100, history, book, 30, market.simulation.rng)
    assert [q.side for q in quotes] == ["buy", "sell"]
    assert (quotes[0].price + quotes[1].price) / 2 < 10100  # Inventory skew.
    cheap = {"bids": [], "asks": [{"price": 10000, "quantity": 10}]}
    quotes, _ = value.decide(10100, history, cheap, 0, market.simulation.rng)
    assert quotes[0].side == "buy" and quotes[0].price == 10000


def test_five_strategies_trade_with_bounded_inventory_and_balanced_ledger():
    market = Market(MarketConfig(seed=42))
    market.simulation.set_running(True)
    for _ in range(300):
        run_round(market)
        for agent in market.simulation.agents:
            position = market.engine.accounts.get(agent.agent_id, {}).get("position", 0)
            assert abs(position) <= agent.position_limit
        book = market.engine.book()
        if book["bids"] and book["asks"]:
            assert book["bids"][0]["price"] < book["asks"][0]["price"]
    assert len({a.strategy for a in market.simulation.agents}) == 5
    assert all(market.engine.accounts[a.agent_id]["fills"] > 0 for a in market.simulation.agents)
    assert sum(a["position"] for a in market.engine.accounts.values()) == 0
    assert sum(a["cash"] for a in market.engine.accounts.values()) == 0
    assert market.engine.trade_count > 100
    assert len(market.engine.active) <= 10
    assert len(market.history) == 240
    assert len(market.engine.trades) == 100
    assert all(t["buyer_agent_id"] != t["seller_agent_id"] for t in market.engine.trades)


def test_pausing_cancels_demo_quotes_and_preserves_external_orders():
    market = Market(MarketConfig(seed=7))
    market.simulation.set_running(True)
    run_round(market)
    external = market.engine.place(OrderRequest(agent_id="external", side="buy", price=1, quantity=1))["order"]
    market.simulation.set_running(False)
    rounds = market.simulation.rounds
    run_round(market)
    assert market.simulation.rounds == rounds
    assert list(market.engine.active) == [external["order_id"]]
    positions = {k: v["position"] for k, v in market.engine.accounts.items()}
    market.simulation.set_running(True)
    assert {k: v["position"] for k, v in market.engine.accounts.items()} == positions
    run_round(market)
    assert market.simulation.rounds == rounds + 1


def test_disabling_one_agent_does_not_stop_the_others():
    market = Market(MarketConfig(seed=7))
    market.simulation.set_running(True)
    run_round(market)
    market.simulation.set_enabled("atlas", False)
    previous = market.simulation.agents[0].orders_submitted
    for _ in range(10):
        run_round(market)
    assert market.simulation.agents[0].orders_submitted == previous
    assert not any(o.agent_id == "atlas" for o in market.engine.active.values())
    assert market.simulation.agents[-1].orders_submitted > previous
    market.simulation.set_enabled("atlas", True)
    run_round(market)
    assert market.simulation.agents[0].orders_submitted > previous


def test_agents_do_not_change_seeded_external_price_path():
    demo = Market(MarketConfig(seed=42))
    idle = Market(MarketConfig(seed=42))
    demo.simulation.set_running(True)
    for _ in range(50):
        run_round(demo)
        run_round(idle)
        assert demo.fair()["price"] == idle.fair()["price"]


def test_dashboard_and_control_api():
    with TestClient(create_app(MarketConfig(interval_seconds=3600))) as client:
        page = client.get("/")
        assert page.status_code == 200 and "A market of minds" in page.text
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/style.css").status_code == 200
        assert client.get("/agents").json()["running"] is False
        with client.websocket_connect("/ws") as ws:
            assert len(ws.receive_json()["simulation"]["agents"]) == 5
            assert client.post("/simulation/start").json()["running"] is True
            assert ws.receive_json()["simulation"]["running"] is True
            assert client.patch("/agents/atlas", json={"enabled": False}).status_code == 200
            assert ws.receive_json()["simulation"]["agents"][0]["enabled"] is False
            assert client.patch("/agents/unknown", json={"enabled": True}).status_code == 404
            assert client.patch("/agents/atlas", json={"enabled": "false"}).status_code == 422
            assert client.post("/simulation/invalid").status_code == 404
            assert client.post("/simulation/pause").json()["running"] is False
            assert ws.receive_json()["simulation"]["running"] is False


def test_demo_runs_automatically_when_enabled():
    with TestClient(create_app(MarketConfig(interval_seconds=0.01, seed=4), demo=True)) as client:
        with client.websocket_connect("/ws") as ws:
            # A bounded number of price updates covers several agent rounds.
            for _ in range(400):
                snapshot = ws.receive_json()
                if snapshot["simulation"]["rounds"] >= 3:
                    break
            else:
                pytest.fail("Demo agent loop did not run")
            assert snapshot["stats"]["trades"] > 0
            assert snapshot["stats"]["volume"] > 0
