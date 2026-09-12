"""Scan/Deep engines must import Streamlit-era `agents.*` under FastAPI."""
from __future__ import annotations

import sys

from backend.pathsetup import BACKEND_DIR, ensure_backend_on_path


def test_ensure_backend_on_path_makes_agents_importable():
    # Simulate FastAPI's repo-root-only path.
    backend = str(BACKEND_DIR)
    while backend in sys.path:
        sys.path.remove(backend)
    try:
        import agents  # noqa: F401
        agents_ok_before = True
    except ModuleNotFoundError:
        agents_ok_before = False
    # If another test already imported agents, skip the negative assert.
    if "agents" not in sys.modules:
        assert agents_ok_before is False

    ensure_backend_on_path()
    assert backend in sys.path
    import agents.data_fetcher  # noqa: F401
    import agents.recommender  # noqa: F401
    import agents.deep_research  # noqa: F401
    import agents.llm_agent  # noqa: F401
    assert hasattr(agents.recommender, "run_full_analysis")
    assert hasattr(agents.deep_research, "analyze_tickers")


def test_scan_job_import_does_not_raise_agents_missing(tmp_path, monkeypatch):
    ensure_backend_on_path()
    captured = {}

    def fake_run_full_analysis(**kwargs):
        captured["ok"] = True
        captured["tickers"] = kwargs.get("selected_tickers")
        return {
            "recommendations": [{"ticker": "AAPL", "growth_score": 70, "total_score": 71, "risk_adjusted_score": 70}],
            "all_rankings": [{"ticker": "AAPL", "rank": 1, "risk_adjusted_score": 70, "growth_score": 70, "model_score": 71}],
            "market_regime": {"regime": "neutral", "entry_threshold": 65},
            "use_llm": False,
        }

    import agents.recommender as rec
    from backend.api import research_jobs as rj

    monkeypatch.setattr(rec, "run_full_analysis", fake_run_full_analysis)
    monkeypatch.setattr(rj, "LAST_SCAN_PATH", tmp_path / "last_scan.json")

    job = rj.start_scan(lang="en", use_llm=False)
    assert job["kind"] == "scan"
    import time

    for _ in range(50):
        live = rj.get_job(job["id"])
        if live and live.get("status") in ("done", "error"):
            break
        time.sleep(0.05)
    live = rj.get_job(job["id"])
    assert live["status"] != "error", live
    assert live["status"] == "done"
    assert captured.get("ok") is True
    assert live.get("error") is None
    assert (tmp_path / "last_scan.json").exists()
