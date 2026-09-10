"""Tests for Stage-2 performance seal gates."""
from datetime import datetime, timezone, timedelta
from pathlib import Path

from backend.trading.performance.tracker import (
    PerformanceReport,
    SealCriteria,
    seal_stage2,
    persist_performance_snapshot,
)


def _report(**overrides) -> PerformanceReport:
    now = datetime.now(timezone.utc)
    base = PerformanceReport(
        start_date=now - timedelta(days=60),
        end_date=now,
        trading_days=40,
        num_trades=10,
        sharpe_ratio=1.4,
        max_drawdown_pct=8.0,
        alpha_pct=2.5,
        spy_return_pct=3.0,
        total_return_pct=5.5,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_seal_stage2_passes_healthy_report():
    decision = seal_stage2(_report(), SealCriteria(min_calendar_months=1))
    assert decision.passed
    assert decision.reasons == []


def test_seal_stage2_fails_low_sharpe_and_high_dd():
    decision = seal_stage2(
        _report(sharpe_ratio=0.2, max_drawdown_pct=35.0),
        SealCriteria(min_sharpe=1.0, max_drawdown_pct=20.0, min_calendar_months=1),
    )
    assert not decision.passed
    assert any("sharpe" in r for r in decision.reasons)
    assert any("max_drawdown" in r for r in decision.reasons)


def test_persist_performance_snapshot(tmp_path: Path):
    report = _report()
    seal = seal_stage2(report, SealCriteria(min_calendar_months=1))
    out = persist_performance_snapshot(report, seal, path=tmp_path / "performance_shadow.json")
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert '"seal_passed": true' in text
    assert "disclaimer" in text
