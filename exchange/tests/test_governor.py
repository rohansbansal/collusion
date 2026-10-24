import asyncio
import json

from fastapi.testclient import TestClient

from exchange.api import MarketConfig, create_app
from exchange.llm import Finding, GovernorReview, ReviewCompletion
from test_simulation import ScriptedBrain, decision, market_with_brain


def finding(event_id, **changes):
    return Finding(**{
        "agent_id": "atlas", "classification": "violation", "rule_id": "oracle_integrity",
        "evidence_event_ids": [event_id], "explanation": "An applied paid oracle distortion is recorded.",
        "recommended_fine_ticks": 200, "recommended_suspension_rounds": 2, **changes,
    })


class ReviewingBrain(ScriptedBrain):
    def __init__(self):
        super().__init__(tamper=True)
        self.review_observations = []

    async def review(self, observation):
        self.review_observations.append(observation)
        evidence = [e for e in observation["events"] if e["kind"] == "oracle_tamper"
                    and e["round"] == observation["round"] and e["agent_id"] == "atlas"]
        findings = [finding(evidence[0]["event_id"])] if evidence else []
        return ReviewCompletion(GovernorReview(summary="Review of recorded actions", findings=findings), tokens=200, cost=0.001)


def test_traders_know_governor_and_remain_profit_maximizers():
    async def run():
        brain = ReviewingBrain()
        market = market_with_brain(brain)
        sim = market.simulation
        sim.set_running(True)
        await sim.cycle()
        assert len(brain.observations) == 8
        for observation in brain.observations:
            notice = observation["governance"]
            assert notice["name"] == "Sentinel" and notice["mode"] == "observe"
            assert "maximizing net profit" in notice["objective"]
            assert "oracle_integrity" in notice["rules"]
        next_notice = sim.observation(sim.agents[0])["governance"]
        assert next_notice["recent_findings"][0]["agent_id"] == "atlas"
        assert sim.governor.calls == 1 and sim.calls == 9
        assert sim.tokens == 1000
    asyncio.run(run())


def test_observe_mode_publishes_but_does_not_sanction():
    async def run():
        sim = market_with_brain(ReviewingBrain()).simulation
        sim.set_running(True)
        await sim.cycle()
        row = sim.governor.findings[0]
        assert row["classification"] == "violation"
        assert row["fine_ticks"] == 0 and row["suspension_rounds"] == 0
        assert sim.agents[0].fines == 0
        assert sum(a["cash"] for a in sim.market.engine.accounts.values()) == -35  # Oracle cost only.
    asyncio.run(run())


def test_enforcement_deducts_fine_cancels_quotes_and_expires_suspension():
    async def run():
        sim = market_with_brain(ReviewingBrain(), governor_mode="enforce").simulation
        sim.set_running(True)
        await sim.cycle()
        atlas = sim.agents[0]
        assert atlas.fines == 200
        assert sim.governor.total_fines == 200
        assert sum(a["cash"] for a in sim.market.engine.accounts.values()) == -235
        assert not any(o.agent_id == "atlas" for o in sim.market.engine.active.values())
        await sim.cycle()
        await sim.cycle()
        assert atlas.decisions == 1
        await sim.cycle()
        assert atlas.decisions == 2
        assert sim.agents[1].decisions == 4
        assert sim.governor.notice(atlas)["own_fines_ticks"] == 200
    asyncio.run(run())


def test_off_mode_retains_awareness_but_does_not_make_review_calls():
    async def run():
        brain = ReviewingBrain()
        sim = market_with_brain(brain, governor_mode="off").simulation
        sim.set_running(True)
        await sim.cycle()
        assert sim.calls == 8 and not brain.review_observations
        assert brain.observations[0]["governance"]["mode"] == "off"
    asyncio.run(run())


def test_governor_observation_excludes_private_memory_signals_and_benchmark():
    sim = market_with_brain().simulation
    sim.apply(sim.agents[0], decision(memory="PRIVATE_MEMORY_MARKER", oracle_shift_bps=100))
    audit = sim.governor.observation()
    encoded = json.dumps(audit)
    assert "PRIVATE_MEMORY_MARKER" not in encoded
    for field in ("benchmark_price", "benchmark_pnl", "private_valuation", "private_offsets", "api_key"):
        assert field not in encoded
    assert any(e["kind"] == "oracle_tamper" for e in audit["events"])


def test_unknown_or_misattributed_evidence_cannot_trigger_penalties():
    sim = market_with_brain(governor_mode="enforce").simulation
    sim.apply(sim.agents[0], decision(oracle_shift_bps=100))
    event = next(e for e in sim.events if e["kind"] == "oracle_tamper")
    for row in (finding("invented-id"), finding(event["event_id"], agent_id="pulse")):
        sim.governor.accept(GovernorReview(summary="Unsupported", findings=[row]), sim.governor.observation())
    assert not sim.governor.findings
    assert sim.governor.total_fines == 0
    assert sum(e["kind"] == "governor_rejected_finding" for e in sim.events) == 2


def test_rejected_oracle_request_is_not_a_completed_violation():
    sim = market_with_brain(governor_mode="enforce").simulation
    sim.set_oracle_enabled(False)
    sim.apply(sim.agents[0], decision(oracle_shift_bps=100))
    event = next(e for e in sim.events if e["kind"] == "decision")
    sim.governor.accept(GovernorReview(summary="Unsupported", findings=[finding(event["event_id"])]),
                        sim.governor.observation())
    assert not sim.governor.findings and sim.agents[0].fines == 0


def test_suspicion_never_receives_penalties_even_if_model_recommends_them():
    sim = market_with_brain(governor_mode="enforce").simulation
    sim.apply(sim.agents[0], decision())
    event = next(e for e in sim.events if e["kind"] == "decision")
    row = finding(event["event_id"], classification="suspicious", rule_id="deceptive_liquidity")
    sim.governor.accept(GovernorReview(summary="Uncertain", findings=[row]), sim.governor.observation())
    assert sim.governor.findings[0]["fine_ticks"] == 0
    assert sim.agents[0].suspended_through_round == 0


def test_old_or_duplicate_evidence_cannot_be_used_to_fine_again():
    sim = market_with_brain(governor_mode="enforce").simulation
    sim.rounds = 1
    sim.apply(sim.agents[0], decision(oracle_shift_bps=100))
    event = next(e for e in sim.events if e["kind"] == "oracle_tamper")
    review = GovernorReview(summary="Recorded distortion", findings=[finding(event["event_id"])])
    observation = sim.governor.observation()
    sim.governor.accept(review, observation)
    sim.governor.accept(review, observation)
    assert sim.agents[0].fines == 200
    sim.rounds = 2
    sim.apply(sim.agents[0], decision())
    current = next(e for e in reversed(sim.events) if e["kind"] == "decision")
    stale = finding(event["event_id"], evidence_event_ids=[event["event_id"], current["event_id"]])
    sim.governor.accept(GovernorReview(summary="Old act", findings=[stale]), sim.governor.observation())
    assert sim.agents[0].fines == 200


def test_mode_change_or_pause_discards_inflight_review_without_sanctions():
    async def run(change_mode):
        ready, release = asyncio.Event(), asyncio.Event()
        class SlowReviewer(ReviewingBrain):
            async def review(self, observation):
                ready.set()
                await release.wait()
                return await super().review(observation)
        sim = market_with_brain(SlowReviewer(), governor_mode="enforce").simulation
        sim.set_running(True)
        task = asyncio.create_task(sim.cycle())
        await ready.wait()
        async with sim.market.lock:  # Governor inference must not lock the exchange.
            if change_mode:
                sim.governor.set_mode("observe")
            else:
                sim.set_running(False)
        release.set()
        await task
        assert sim.agents[0].fines == 0
        assert sim.governor.reviews == 0
        assert any(e["kind"] == "governor_discarded" for e in sim.events)
    asyncio.run(run(False))
    asyncio.run(run(True))


def test_governor_failure_is_sanitized_and_not_replaced_by_hardcoded_verdicts():
    async def run():
        class BrokenReviewer(ReviewingBrain):
            async def review(self, observation):
                raise RuntimeError("SECRET_MARKER")
        sim = market_with_brain(BrokenReviewer(), governor_mode="enforce").simulation
        sim.set_running(True)
        await sim.cycle()
        assert sim.governor.errors == 1 and sim.governor.reviews == 0
        assert not sim.governor.findings and sim.agents[0].fines == 0
        assert "SECRET_MARKER" not in json.dumps(sim.market.snapshot())
    asyncio.run(run())


def test_everyone_suspended_does_not_freeze_expiry():
    async def run():
        sim = market_with_brain().simulation
        sim.set_running(True)
        for agent in sim.agents:
            agent.suspended_through_round = 2
        await sim.cycle()
        await sim.cycle()
        assert sim.rounds == 2 and sim.calls == 0
        await sim.cycle()
        assert all(a.decisions == 1 for a in sim.agents)
    asyncio.run(run())


def test_governor_api_modes_and_snapshot():
    with TestClient(create_app(MarketConfig(), brain=ReviewingBrain())) as client:
        assert client.get("/governor").json()["mode"] == "observe"
        assert client.patch("/experiment/governor", json={"mode": "enforce"}).json()["mode"] == "enforce"
        assert client.patch("/experiment/governor", json={"mode": "invalid"}).status_code == 422
        assert client.get("/book").json()["simulation"]["governor"]["mode"] == "enforce"
        assert "Sentinel" in client.get("/").text
