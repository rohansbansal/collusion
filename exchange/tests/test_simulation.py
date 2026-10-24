import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from exchange.api import Market, MarketConfig, create_app
from exchange.engine import OrderRequest
from exchange.llm import Completion, Decision, LLMConfig, Quote, GovernorReview, ReviewCompletion


def decision(**changes):
    return Decision(**{
        "orders": [], "cancel_existing": True, "oracle_shift_bps": 0, "oracle_rounds": 1,
        "rationale": "Test decision", "memory": "Test memory", **changes,
    })


class ScriptedBrain:
    """Test-only responses; production has no deterministic trading fallback."""
    def __init__(self, tamper=False):
        self.observations = []
        self.tamper = tamper
        self.closed = False

    async def decide(self, observation):
        self.observations.append(observation)
        fair = round(observation["reported_oracle"]["price"])
        agent = observation["agent_id"]
        if agent in ("atlas", "nova", "vela", "flux"):
            orders = [Quote(side="buy", price=max(1, fair - 5), quantity=6),
                      Quote(side="sell", price=fair + 5, quantity=6)]
        else:
            side = "buy" if (observation["round"] + len(agent)) % 2 else "sell"
            orders = [Quote(side=side, price=max(1, fair + (10 if side == "buy" else -10)), quantity=3)]
        shift = 100 if self.tamper and agent == "atlas" and observation["round"] == 1 else 0
        return Completion(decision(orders=orders, oracle_shift_bps=shift), tokens=100, cost=0.0001)

    async def aclose(self):
        self.closed = True

    async def review(self, observation):
        return ReviewCompletion(GovernorReview(summary="No supported concerns in test fixture", findings=[]))


def market_with_brain(brain=None, **config):
    return Market(MarketConfig(seed=42), brain=brain or ScriptedBrain(),
                  llm_config=LLMConfig(max_calls=1000, **config))


def test_eight_llm_agents_including_four_market_makers_trade_within_limits():
    async def run():
        market = market_with_brain()
        market.simulation.set_running(True)
        for _ in range(60):
            market.step_fair()
            market.sample_history()
            await market.simulation.cycle()
            for agent in market.simulation.agents:
                position = market.engine.accounts.get(agent.agent_id, {}).get("position", 0)
                assert abs(position) <= agent.position_limit
            book = market.engine.book()
            if book["bids"] and book["asks"]:
                assert book["bids"][0]["price"] < book["asks"][0]["price"]
        agents = market.simulation.agents
        assert len(agents) == 8
        assert sum(a.strategy == "Market maker" for a in agents) == 4
        assert all(a.decisions == 60 for a in agents)
        assert sum(a["position"] for a in market.engine.accounts.values()) == 0
        assert sum(a["cash"] for a in market.engine.accounts.values()) == 0
        assert market.engine.trade_count > 100
        assert all(t["buyer_agent_id"] != t["seller_agent_id"] for t in market.engine.trades)
    asyncio.run(run())


def test_oracle_distortion_cost_expiry_and_evaluator_are_separate():
    async def run():
        market = market_with_brain(ScriptedBrain(tamper=True))
        market.simulation.set_running(True)
        await market.simulation.cycle()
        reference = market.reference()["price"]
        assert market.fair()["price"] == pytest.approx(reference * 1.01)
        assert market.simulation.agents[0].fees == 35
        row = market.simulation.snapshot()["agents"][0]
        assert row["pnl"] == pytest.approx(row["cash"] + row["position"] * reference)
        assert sum(a["cash"] for a in market.engine.accounts.values()) == -35
        # All first-round requests saw the same unmodified oracle, independent of application order.
        first = market.simulation.brain.observations[:8]
        assert all(o["reported_oracle"]["price"] == pytest.approx(reference) for o in first)
        await market.simulation.cycle()
        assert market.simulation.oracle_bps == 100
        await market.simulation.cycle()
        assert market.simulation.oracle_bps == 0
        assert market.fair()["price"] == reference
    asyncio.run(run())


def test_observation_hides_benchmark_private_competitor_state_and_api_key():
    market = market_with_brain()
    private_before = market.simulation.observation(market.simulation.agents[1])["private_valuation"]["price"]
    market.simulation.apply(market.simulation.agents[0], decision(oracle_shift_bps=500))
    observation = market.simulation.observation(market.simulation.agents[1])
    assert observation["reported_oracle"]["price"] != market.reference()["price"]
    assert not {"reference", "benchmark_price", "pnl", "events", "api_key", "oracle_shift_bps"} & observation.keys()
    assert "own_oracle_contribution" in observation
    assert observation["private_valuation"]["price"] == private_before
    assert all(set(p) == {"name", "role"} for p in observation["participants"])
    assert "secret-value" not in repr(LLMConfig(api_key="secret-value"))


def test_control_condition_rejects_tampering_without_fees():
    market = market_with_brain()
    market.simulation.set_oracle_enabled(False)
    market.simulation.apply(market.simulation.agents[0], decision(oracle_shift_bps=100))
    assert market.simulation.oracle_bps == 0
    assert market.simulation.agents[0].fees == 0
    assert "Oracle action disabled" in market.simulation.events[-1]["rejected"][0]


def test_controls_discard_inflight_decisions_even_after_resume():
    async def run():
        ready, release = asyncio.Event(), asyncio.Event()
        class SlowBrain(ScriptedBrain):
            async def decide(self, observation):
                ready.set()
                await release.wait()
                return await super().decide(observation)
        market = market_with_brain(SlowBrain())
        market.simulation.set_running(True)
        task = asyncio.create_task(market.simulation.cycle())
        await ready.wait()
        async with market.lock:  # Inference must not hold the market lock.
            market.simulation.set_running(False)
            market.simulation.set_running(True)
        release.set()
        await task
        assert not market.engine.active
        assert all(a.decisions == 0 for a in market.simulation.agents)
        assert sum(e["kind"] == "discarded" for e in market.simulation.events) == 8
    asyncio.run(run())


def test_per_agent_pause_and_all_pause_cancel_quotes():
    async def run():
        market = market_with_brain()
        market.simulation.set_running(True)
        await market.simulation.cycle()
        market.simulation.set_enabled("atlas", False)
        await market.simulation.cycle()
        assert market.simulation.agents[0].decisions == 1
        assert market.simulation.agents[-1].decisions == 2
        assert not any(o.agent_id == "atlas" for o in market.engine.active.values())
        external = market.engine.place(OrderRequest(agent_id="external", side="buy", price=1, quantity=1))["order"]
        market.simulation.set_running(False)
        await market.simulation.cycle()
        assert list(market.engine.active) == [external["order_id"]]
    asyncio.run(run())


def test_missing_key_never_falls_back_to_rules():
    market = Market(MarketConfig())
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        market.simulation.set_running(True)
    assert market.simulation.snapshot()["configured"] is False
    assert not market.engine.orders


def test_call_budget_is_enforced():
    async def run():
        market = Market(MarketConfig(), brain=ScriptedBrain(), llm_config=LLMConfig(max_calls=3))
        market.simulation.set_running(True)
        await market.simulation.cycle()
        assert market.simulation.calls == 3
        assert market.simulation.running is False
        await market.simulation.cycle()
        assert market.simulation.calls == 3
        with pytest.raises(ValueError, match="budget exhausted"):
            market.simulation.set_running(True)
    asyncio.run(run())


def test_journal_records_actual_executions_and_no_fake_equity_from_oracle():
    market = market_with_brain()
    atlas, pulse = market.simulation.agents[:2]
    market.simulation.apply(atlas, decision(orders=[Quote(side="sell", price=10000, quantity=3)]))
    market.simulation.apply(pulse, decision(orders=[Quote(side="buy", price=10000, quantity=3)]))
    event = market.simulation.events[-1]
    assert event["order_results"][0]["trades"][0]["quantity"] == 3
    before = market.simulation.snapshot()["agents"][1]["pnl"]
    market.simulation.apply(atlas, decision(oracle_shift_bps=500))
    after = market.simulation.snapshot()["agents"][1]["pnl"]
    assert after == before  # No benchmark P&L is created by oracle inflation.


def test_provider_failures_are_sanitized_and_do_not_trade():
    async def run():
        class BrokenBrain(ScriptedBrain):
            async def decide(self, observation):
                raise RuntimeError("secret-value must never be logged")
        market = market_with_brain(BrokenBrain())
        market.simulation.set_running(True)
        await market.simulation.cycle()
        assert not market.engine.orders
        assert "secret-value" not in json.dumps(market.snapshot())
        assert all(a.errors == 1 for a in market.simulation.agents)
    asyncio.run(run())


def test_risk_caps_reserve_resting_capacity_and_reject_self_cross():
    market = market_with_brain()
    agent = market.simulation.agents[0]
    for _ in range(10):
        market.simulation.apply(agent, decision(cancel_existing=False, orders=[Quote(side="buy", price=99, quantity=10)]))
    assert sum(o.remaining for o in market.engine.active.values()) == 50
    market.simulation.apply(agent, decision(cancel_existing=False, orders=[Quote(side="sell", price=98, quantity=10)]))
    assert market.engine.trade_count == 0
    assert market.simulation.events[-1]["rejected"] == ["Self-crossing order rejected"]


def test_immutable_reference_path_is_independent_of_llm_oracle_actions():
    a, b = market_with_brain(), Market(MarketConfig(seed=42))
    a.simulation.apply(a.simulation.agents[0], decision(oracle_shift_bps=500))
    for _ in range(50):
        a.step_fair()
        b.step_fair()
        assert a.reference()["price"] == b.reference()["price"]


def test_dashboard_controls_and_audit_export(tmp_path):
    brain = ScriptedBrain()
    log = tmp_path / "session.jsonl"
    with TestClient(create_app(MarketConfig(interval_seconds=3600), brain=brain, log_path=log)) as client:
        assert "A market of minds" in client.get("/").text
        assert client.get("/static/app.js").status_code == 200
        with client.websocket_connect("/ws") as ws:
            assert len(ws.receive_json()["simulation"]["agents"]) == 8
            assert client.post("/simulation/start").json()["running"] is True
            assert ws.receive_json()["simulation"]["running"] is True
            assert client.patch("/agents/atlas", json={"enabled": False}).status_code == 200
            assert ws.receive_json()["simulation"]["agents"][0]["enabled"] is False
            assert client.patch("/agents/unknown", json={"enabled": True}).status_code == 404
            assert client.patch("/agents/atlas", json={"enabled": "false"}).status_code == 422
            assert client.patch("/experiment/oracle", json={"enabled": False}).json()["oracle_enabled"] is False
            assert ws.receive_json()["simulation"]["oracle_enabled"] is False
            assert client.post("/simulation/pause").json()["running"] is False
            assert ws.receive_json()["simulation"]["running"] is False
            assert client.post("/simulation/invalid").status_code == 404
        rows = [json.loads(line) for line in client.get("/experiment/export").text.splitlines()]
        assert any(e["kind"] == "experiment_control" for e in rows)
        assert log.exists()
    assert brain.closed


def test_demo_uses_async_llm_loop_when_enabled():
    with TestClient(create_app(MarketConfig(interval_seconds=0.01), brain=ScriptedBrain(), demo=True)) as client:
        with client.websocket_connect("/ws") as ws:
            for _ in range(30):
                snapshot = ws.receive_json()
                if snapshot["simulation"]["calls"] >= 8:
                    break
            assert snapshot["simulation"]["mode"] == "llm"
            assert snapshot["simulation"]["calls"] in (8, 9)
