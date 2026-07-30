import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from agents import llm_agent
from agents.llm_agent import (
    _normalize_decision_explanation,
    _normalize_news_impact,
    _classify_strategy_error,
    get_model_for_task,
    get_public_config,
)


def test_model_routing_reserves_reasoner_for_strategy() -> None:
    config = get_public_config()
    assert get_model_for_task("strategy") == config["reasoning_model"]
    assert get_model_for_task("stock_analysis") == config["chat_model"]
    assert get_model_for_task("price_targets") == config["chat_model"]
    assert get_model_for_task("options") == config["chat_model"]
    assert get_model_for_task("sec_summary") == config["chat_model"]
    assert get_model_for_task("news_impact") == config["chat_model"]


def test_public_config_never_exposes_api_key() -> None:
    config = get_public_config()

    assert config["provider"] == "openai-compatible"
    assert "api_key" not in config
    assert config["timeout_seconds"] > 0
    assert config["uses_separate_reasoner"] == (config["reasoning_model"] != config["chat_model"])


def test_reasoner_failure_falls_back_to_chat(monkeypatch) -> None:
    calls = []

    class Completions:
        def create(self, model, **kwargs):
            calls.append(model)
            if model == "reasoner":
                raise RuntimeError("unsupported")
            return "ok"

    class Client:
        class Chat:
            completions = Completions()
        chat = Chat()

    monkeypatch.setattr(llm_agent, "_REASONING_MODEL", "reasoner")
    monkeypatch.setattr(llm_agent, "_CHAT_MODEL", "chat")

    assert llm_agent._create_completion(Client(), "strategy", messages=[]) == "ok"
    assert calls == ["reasoner", "chat"]


def test_news_impact_normalization_rejects_invalid_enums_and_bounds_confidence() -> None:
    result = _normalize_news_impact({
        "direction": "guaranteed_up", "magnitude": "huge", "horizon": "tomorrow",
        "event_type": "rumor", "confidence": 150, "key_risks": "not-a-list",
    })

    assert result["direction"] == "neutral"
    assert result["magnitude"] == "low"
    assert result["event_type"] == "other"
    assert result["confidence"] == 100


def test_decision_explanation_falls_back_to_authoritative_avoid_context() -> None:
    result = _normalize_decision_explanation(
        {"label": "guaranteed_buy", "why": "Risk and trend blockers remain."},
        {"stance": "bearish", "action": "avoid_or_hedge", "avoid_reasons": ["High risk"]},
    )

    assert result["label"] == "avoid"
    assert result["why"] == "Risk and trend blockers remain."


def test_decision_explanation_cannot_override_authoritative_bullish_context() -> None:
    result = _normalize_decision_explanation(
        {"label": "bearish", "why": "The model decision remains bullish."},
        {"stance": "bullish", "action": "buy", "avoid_reasons": []},
    )

    assert result["label"] == "bullish"


def test_strategy_error_code_classification() -> None:
    # Keep provider diagnostics stable for the Deep Analysis fallback message.
    for message, expected in (
        ("request timeout", "timeout"),
        ("401 Unauthorized", "authentication"),
        ("429 rate limit", "rate_limit"),
    ):
        assert _classify_strategy_error(RuntimeError(message)) == expected


def test_strategy_circuit_breaker_skips_recently_failed_provider(monkeypatch) -> None:
    monkeypatch.setattr(llm_agent, "_llm_last_check", time.time())
    monkeypatch.setattr(llm_agent, "_llm_healthy", False)
    monkeypatch.setattr(llm_agent, "_get_client", lambda: object())

    result = llm_agent.suggest_trading_strategy("AAPL", {}, {}, 100)

    assert result["error_code"] == "provider_error"
    assert "temporarily unavailable" in result["error"]
