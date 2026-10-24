import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from exchange.llm import Decision, LLMConfig, OpenRouterBrain, GOVERNOR_PROMPT, SYSTEM_PROMPT, DEFAULT_MODEL


ACTION = {"orders": [{"side": "buy", "price": 9990, "quantity": 3}],
          "cancel_existing": True, "oracle_shift_bps": 50, "oracle_rounds": 2,
          "rationale": "Test public action summary", "memory": "Track my inventory"}


def test_openrouter_request_and_structured_response():
    def respond(request):
        assert request.url == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer fake-test-key"
        payload = json.loads(request.content)
        assert payload["provider"]["require_parameters"] is True
        schema = payload["response_format"]["json_schema"]
        assert schema["strict"] and schema["schema"]["additionalProperties"] is False
        assert set(schema["schema"]["required"]) == set(ACTION)
        assert "fake-test-key" not in json.dumps(payload)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(ACTION)}}],
                                         "usage": {"total_tokens": 200, "cost": 0.001}})
    async def run():
        brain = OpenRouterBrain(LLMConfig(api_key="fake-test-key"), transport=httpx.MockTransport(respond))
        try:
            result = await brain.decide({"agent_id": "atlas"})
            assert result.decision.oracle_shift_bps == 50
            assert result.decision.orders[0].quantity == 3
            assert result.cost == 0.001 and result.tokens == 200
        finally:
            await brain.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("change", [
    {"oracle_shift_bps": 501}, {"oracle_rounds": 4}, {"oracle_shift_bps": 1.5},
    {"orders": [{"side": "buy", "price": 0, "quantity": 1}]},
    {"orders": [{"side": "sell", "price": 100, "quantity": 11}]},
    {"shell_command": "not-an-available-action"},
])
def test_invalid_model_actions_rejected(change):
    with pytest.raises(ValidationError):
        Decision.model_validate({**ACTION, **change})


def test_truncated_model_output_is_not_executed():
    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
            "choices": [{"finish_reason": "length", "message": {"content": json.dumps(ACTION)}}],
        }))
        brain = OpenRouterBrain(LLMConfig(), transport=transport)
        try:
            with pytest.raises(ValueError, match="incomplete"):
                await brain.decide({})
        finally:
            await brain.aclose()
    asyncio.run(run())


def test_control_condition_from_environment(monkeypatch):
    monkeypatch.setenv("ORACLE_MANIPULATION_ENABLED", "false")
    assert LLMConfig.from_env().oracle_enabled is False
    monkeypatch.setenv("ORACLE_MANIPULATION_ENABLED", "invalid")
    with pytest.raises(ValueError, match="must be true or false"):
        LLMConfig.from_env()


def test_open_model_is_default_for_traders_and_governor(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_GOVERNOR_MODEL", raising=False)
    assert LLMConfig().model == "qwen/qwen3-30b-a3b-instruct-2507"
    config = LLMConfig.from_env()
    assert config.model == DEFAULT_MODEL
    assert not config.governor_model  # Inherits the same open model.


def test_governor_uses_its_own_prompt_and_validates_structured_output():
    def respond(request):
        payload = json.loads(request.content)
        assert payload["model"] == "test/governor-model"
        assert payload["messages"][0]["content"] == GOVERNOR_PROMPT
        assert "UNTRUSTED EVIDENCE" in GOVERNOR_PROMPT
        assert payload["response_format"]["json_schema"]["name"] == "governor_review"
        assert "maximizing NET profit" in SYSTEM_PROMPT
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"summary": "No supported findings", "findings": []})}}],
            "usage": {"total_tokens": 100, "cost": 0.001}})
    async def run():
        brain = OpenRouterBrain(LLMConfig(governor_model="test/governor-model"), transport=httpx.MockTransport(respond))
        try:
            result = await brain.review({"events": []})
            assert result.review.findings == [] and result.tokens == 100
        finally:
            await brain.aclose()
    asyncio.run(run())
