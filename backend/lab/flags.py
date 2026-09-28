"""Environment flags for lab citation-try. All default OFF."""
from __future__ import annotations

import os
from typing import Any, Dict


def _truthy(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default)) or default))
    except (TypeError, ValueError):
        return default


def instructor_enabled() -> bool:
    return _truthy("LAB_INSTRUCTOR")


def finbert_enabled() -> bool:
    return _truthy("LAB_FINBERT")


def quantstats_enabled() -> bool:
    return _truthy("LAB_QUANTSTATS")


def advisory_enabled() -> bool:
    """Bull/Bear/Risk LLM extras. Never wires to OrderManager.

    Desk still shows a rule-based committee when this flag is off.
    """
    return _truthy("LAB_ADVISORY")


def advisory_llm_max_calls() -> int:
    """Daily LLM committee completions (UTC). Default 3."""
    return _env_int("LAB_ADVISORY_MAX_LLM_CALLS", 3)


def advisory_llm_max_tokens() -> int:
    """Max tokens per committee completion. Default 400."""
    return max(64, _env_int("LAB_ADVISORY_MAX_TOKENS", 400))


def lab_status() -> Dict[str, Any]:
    """Auditable snapshot of which lab extras are requested vs importable."""
    status: Dict[str, Any] = {
        "branch_hint": "lab/citation-try",
        "never_places_orders": True,
        "flags": {
            "LAB_INSTRUCTOR": instructor_enabled(),
            "LAB_FINBERT": finbert_enabled(),
            "LAB_QUANTSTATS": quantstats_enabled(),
            "LAB_ADVISORY": advisory_enabled(),
            "WORKER_SENTIMENT_VETO": _truthy("WORKER_SENTIMENT_VETO"),
        },
        "advisory_budget": {
            "max_llm_calls_per_day": advisory_llm_max_calls(),
            "max_tokens_per_call": advisory_llm_max_tokens(),
        },
        "imports": {},
        "refused": [
            "FinRL/PPO as trading brain",
            "CrewAI as production orchestrator",
            "TradingAgents full-repo replace",
            "OpenBB+LEAN+Qlib one-shot install",
            "HFT / one-click AI trading repos",
        ],
    }
    try:
        import instructor  # noqa: F401

        status["imports"]["instructor"] = True
    except Exception:
        status["imports"]["instructor"] = False
    try:
        import quantstats  # noqa: F401

        status["imports"]["quantstats"] = True
    except Exception:
        status["imports"]["quantstats"] = False
    try:
        from transformers import pipeline  # noqa: F401

        status["imports"]["transformers"] = True
    except Exception:
        status["imports"]["transformers"] = False
    try:
        import langgraph  # noqa: F401

        status["imports"]["langgraph"] = True
    except Exception:
        status["imports"]["langgraph"] = False
    return status
