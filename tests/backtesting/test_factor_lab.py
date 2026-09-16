"""Wave 4 factor IC/IR lab — fixtures only, no paid data, never places orders."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from agents.score_contract import SHARED_FUND_SCORE_FIELD
from backtesting.factor_lab import (
    PLACES_ORDERS,
    PRIMARY_FACTOR,
    build_performance_research,
    factor_ic_series,
    load_panel,
    make_demo_panel,
    run_fixture_research,
    sku_b_positioning,
    spearman_rank_ic,
    walk_forward_long_only,
    write_performance_research,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "factor_panel.json"


def test_places_orders_is_hard_false() -> None:
    assert PLACES_ORDERS is False
    source = Path(__file__).resolve().parents[2] / "backend" / "backtesting" / "factor_lab.py"
    text = source.read_text(encoding="utf-8")
    assert "alpaca" not in text.lower()
    assert "OrderManager" not in text
    assert "place_order" not in text


def test_spearman_ic_perfect_ranks() -> None:
    ic = spearman_rank_ic([1, 2, 3, 4], [0.01, 0.02, 0.03, 0.04])
    assert ic is not None
    assert abs(ic - 1.0) < 1e-9


def test_spearman_ic_inverted_ranks() -> None:
    ic = spearman_rank_ic([1, 2, 3, 4], [0.04, 0.03, 0.02, 0.01])
    assert ic is not None
    assert abs(ic + 1.0) < 1e-9


def test_fixture_panel_quality_has_positive_ic() -> None:
    panel = load_panel(FIXTURE)
    summary = factor_ic_series(panel, "quality_score")
    assert summary["n_periods"] == 6
    assert summary["ic_mean"] is not None
    assert summary["ic_mean"] > 0.8
    assert summary["ir"] is None or summary["ir"] > 0


def test_walk_forward_does_not_beat_spy_on_fixture() -> None:
    panel = load_panel(FIXTURE)
    wf = walk_forward_long_only(panel, "quality_score", top_n=2, cost_bps=10.0)
    assert wf["places_orders"] is False
    assert wf["n_periods"] == 6
    assert wf["spy_return_pct"] is not None
    assert wf["beats_spy_net_of_costs"] is False
    assert wf["excess_vs_spy_net_pct"] < 0
    assert wf["max_drawdown_pct"] is not None
    assert wf["avg_turnover"] is not None and wf["avg_turnover"] > 0


def test_higher_costs_worsen_net_return() -> None:
    panel = make_demo_panel(n_months=8, spy_monthly=0.0)
    cheap = walk_forward_long_only(panel, PRIMARY_FACTOR, top_n=2, cost_bps=5.0)
    dear = walk_forward_long_only(panel, PRIMARY_FACTOR, top_n=2, cost_bps=80.0)
    assert cheap["total_return_pct"] > dear["total_return_pct"]


def test_sku_b_narrative_when_losing_to_spy() -> None:
    pos = sku_b_positioning(beats_spy_net=False, source="fixture")
    assert pos["alpha_claimed"] is False
    assert pos["places_orders"] is False
    assert pos["default_paper_strategy_unchanged"] is True
    assert pos["sku_b_positioning"] == "research_list_automation"
    assert "not an alpha product" in pos["sku_b_narrative"]
    assert pos["shared_fund_score_field"] == SHARED_FUND_SCORE_FIELD


def test_sku_b_still_does_not_claim_alpha_if_panel_wins() -> None:
    pos = sku_b_positioning(beats_spy_net=True, source="fixture")
    assert pos["alpha_claimed"] is False
    assert pos["default_paper_strategy_unchanged"] is True
    assert "not a paper/live seal" in pos["sku_b_narrative"]


def test_writes_dated_performance_research(tmp_path) -> None:
    report, paths = run_fixture_research(data_dir=tmp_path, panel_path=FIXTURE, top_n=2)
    latest = tmp_path / "performance_research.json"
    dated = tmp_path / f"performance_research_{report['as_of']}.json"
    assert latest in paths and dated in paths
    assert latest.exists() and dated.exists()
    payload = json.loads(latest.read_text(encoding="utf-8"))
    assert payload["places_orders"] is False
    assert payload["alpha_claimed"] is False
    assert payload["wave"] == 4
    assert payload["headline"]["sharpe_ratio"] is not None or payload["headline"]["n_periods"] == 0 or payload["headline"].get("turnover") is not None
    assert "max_drawdown_pct" in payload["headline"]
    assert "turnover" in payload["headline"]
    assert "spy_excess_net_pct" in payload["headline"]
    assert payload["primary_factor"] == SHARED_FUND_SCORE_FIELD
    assert payload["headline"]["beats_spy_net_of_costs"] is False
    assert payload["sku_b_positioning"] == "research_list_automation"
    assert SHARED_FUND_SCORE_FIELD in payload["factors"]
    assert "quality_score" in payload["factors"]
    assert "quality_roe_score" in payload["factors"]


def test_build_report_includes_quality_split_ics() -> None:
    panel = make_demo_panel(n_months=6)
    report = build_performance_research(panel, source="fixture", top_n=3)
    for key in ("quality_score", "quality_roe_score", "quality_margin_score", "quality_leverage_score"):
        assert key in report["factors"]
        assert "ic_mean" in report["factors"][key]


def test_write_performance_research_roundtrip(tmp_path) -> None:
    panel = pd.DataFrame(
        [
            {"date": "2023-01-31", "ticker": "A", "quality_score": 90, SHARED_FUND_SCORE_FIELD: 80,
             "forward_return": 0.05, "spy_forward_return": 0.01, "growth_score": 70},
            {"date": "2023-01-31", "ticker": "B", "quality_score": 10, SHARED_FUND_SCORE_FIELD: 20,
             "forward_return": -0.02, "spy_forward_return": 0.01, "growth_score": 30},
            {"date": "2023-02-28", "ticker": "A", "quality_score": 90, SHARED_FUND_SCORE_FIELD: 80,
             "forward_return": 0.04, "spy_forward_return": 0.01, "growth_score": 70},
            {"date": "2023-02-28", "ticker": "B", "quality_score": 10, SHARED_FUND_SCORE_FIELD: 20,
             "forward_return": -0.01, "spy_forward_return": 0.01, "growth_score": 30},
        ]
    )
    report = build_performance_research(panel, source="unit", top_n=1, as_of="2023-02-28")
    paths = write_performance_research(report, data_dir=tmp_path)
    assert (tmp_path / "performance_research_2023-02-28.json") in paths
    saved = json.loads(paths[0].read_text(encoding="utf-8"))
    assert saved["as_of"] == "2023-02-28"
    assert saved["headline"]["beats_spy_net_of_costs"] is True
    assert saved["alpha_claimed"] is False
