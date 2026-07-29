import pandas as pd
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from backtesting.calibration import ExpandingScoreCalibrator
from backtesting.engine import (
    BacktestResult,
    DEFAULT_START,
    _calculate_turnover,
    _close_on_date,
    _compute_aggregate_metrics,
    _alpha_interval_reason,
    _extract_fundamentals_as_of,
    _target_weights,
)
from utils.selection import MIN_RECOMMENDATION_METRICS


def test_historical_peg_uses_price_available_on_rebalance_date() -> None:
    columns = pd.to_datetime([
        "2024-12-31", "2024-09-30", "2024-06-30", "2024-03-31", "2023-12-31",
    ])
    financials = pd.DataFrame(
        [
            [120, 110, 105, 100, 100],
            [2.4, 2.2, 2.1, 2.0, 2.0],
            [24, 22, 21, 20, 20],
        ],
        index=["Total Revenue", "Diluted EPS", "Net Income"],
        columns=columns,
    )
    balance_sheet = pd.DataFrame(
        [[100, 95, 90, 85, 80], [20, 20, 20, 20, 20]],
        index=["Stockholders Equity", "Total Debt"],
        columns=columns,
    )
    prices = pd.DataFrame(
        {"Close": [100.0, 1_000.0]},
        index=pd.to_datetime(["2025-03-31", "2026-01-01"]),
    )

    data = _extract_fundamentals_as_of(
        financials,
        balance_sheet,
        prices,
        pd.Timestamp("2025-03-31"),
    )

    # 2024 Q4 EPS growth is 20%; PEG must use the $100 rebalance-date close,
    # not the $1,000 close that arrives later in the price dataframe.
    assert round(data["peg"], 2) == 2.08


def test_historical_fundamentals_are_not_substituted_with_future_reports() -> None:
    financials = pd.DataFrame(
        [[100], [2]],
        index=["Total Revenue", "Diluted EPS"],
        columns=pd.to_datetime(["2025-12-31"]),
    )

    assert _extract_fundamentals_as_of(
        financials,
        None,
        None,
        pd.Timestamp("2025-03-31"),
    ) == {}


def test_historical_fundamentals_handle_timezone_aware_prices() -> None:
    columns = pd.to_datetime([
        "2024-12-31", "2024-09-30", "2024-06-30", "2024-03-31", "2023-12-31",
    ])
    financials = pd.DataFrame(
        [[120, 110, 105, 100, 100], [2.4, 2.2, 2.1, 2.0, 2.0]],
        index=["Total Revenue", "Diluted EPS"],
        columns=columns,
    )
    prices = pd.DataFrame(
        {"Close": [100.0]},
        index=pd.DatetimeIndex(["2025-03-31"], tz="America/New_York"),
    )

    data = _extract_fundamentals_as_of(
        financials,
        None,
        prices,
        pd.Timestamp("2025-03-31"),
    )

    assert data["peg"] == 2.08


def test_drawdown_includes_loss_in_first_period() -> None:
    result = BacktestResult()
    result.periods = [{"avg_return": -10.0, "spy_return": 0.0, "alpha": -10.0}]

    _compute_aggregate_metrics(result, [9000.0], [10000.0])

    assert result.max_drawdown_pct == 10.0


def test_turnover_includes_cash_and_full_replacement() -> None:
    assert _calculate_turnover({}, {"A": 0.9}) == 0.9
    assert _calculate_turnover({"A": 0.9}, {"B": 0.9}) == 0.9
    assert _calculate_turnover({"A": 0.9}, {"A": 0.9}) == 0.0


def test_calibrated_kelly_falls_back_to_equal_weight_during_warmup() -> None:
    picks = [{"ticker": ticker, "total_score": 70} for ticker in "ABCDE"]
    weights, ready = _target_weights(
        picks,
        "calibrated_kelly",
        ExpandingScoreCalibrator(min_observations=2),
    )

    assert ready is False
    assert round(sum(weights.values()), 4) == 0.9
    assert set(weights.values()) == {0.18}


def test_backtest_result_exposes_calibration_quality_flag() -> None:
    result = BacktestResult()

    assert result.calibration == {}
    assert result.model_scope == "fundamental_technical"
    assert MIN_RECOMMENDATION_METRICS == 4


def test_close_on_date_requires_an_exact_common_session() -> None:
    prices = pd.DataFrame(
        {"Close": [100.0, 101.0]},
        index=pd.DatetimeIndex(["2026-01-02", "2026-01-05"], tz="America/New_York"),
    )

    assert _close_on_date(prices, "2026-01-05") == 101.0
    assert _close_on_date(prices, "2026-01-03") is None


def test_alpha_interval_reasons_distinguish_negative_zero_and_unavailable() -> None:
    assert _alpha_interval_reason({"available": True, "lower": -5, "upper": -1}) == "alpha_interval_below_zero"
    assert _alpha_interval_reason({"available": True, "lower": -1, "upper": 2}) == "alpha_interval_includes_zero"
    assert _alpha_interval_reason({"available": False}) == "alpha_interval_unavailable"


def test_default_backtest_window_is_about_three_years() -> None:
    age_days = (datetime.now() - datetime.strptime(DEFAULT_START, "%Y-%m-%d")).days

    assert 3 * 365 - 2 <= age_days <= 3 * 365 + 2
