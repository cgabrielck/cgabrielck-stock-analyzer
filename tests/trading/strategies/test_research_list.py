from unittest.mock import patch

import pandas as pd

from backend.trading.strategies.research_list import ResearchListStrategy
from backend.trading.strategies.registry import get_strategy
from backend.trading.strategies.stable import StableStrategy


def _bars(n=40, last=100.0):
    close = [last - (n - i) * 0.1 for i in range(n)]
    return pd.DataFrame(
        {
            "Open": close,
            "High": [c * 1.01 for c in close],
            "Low": [c * 0.99 for c in close],
            "Close": close,
            "Volume": [1_000_000] * n,
        }
    )


def test_research_list_skips_stale_scan():
    strat = ResearchListStrategy()
    with patch.object(strat, "_scan_book", return_value=(["AAPL", "MSFT"], True)):
        assert strat.diagnose_entry("AAPL", _bars(), 80, None, []) == "research_stale"
        assert strat.generate_signal("AAPL", _bars(), 80, None, []) is None


def test_research_list_buys_fresh_top_name():
    strat = ResearchListStrategy()
    with patch.object(strat, "_scan_book", return_value=(["NVDA", "MSFT"], False)):
        sig = strat.generate_signal("NVDA", _bars(), 80, None, [])
    assert sig is not None
    assert sig.strategy_id == "research_list"
    assert sig.stop_loss_price < sig.entry_price < sig.take_profit_price


def test_research_list_skips_names_not_on_scan():
    strat = ResearchListStrategy()
    with patch.object(strat, "_scan_book", return_value=(["AAPL"], False)):
        assert strat.diagnose_entry("TSLA", _bars(), 90, None, []) == "not_in_scan_list"


def test_registry_has_research_list():
    assert get_strategy("research_list").strategy_id == "research_list"
    assert get_strategy("stable").strategy_id == "stable"


def test_stable_diagnose_fund_gate():
    strat = StableStrategy()
    df = pd.DataFrame(
        {
            "Open": [100] * 220,
            "High": [101] * 220,
            "Low": [99] * 220,
            "Close": [100] * 220,
            "Volume": [1_000_000] * 220,
        }
    )
    assert strat.diagnose_entry("AAPL", df, 40, None, []) == "fund_lt_65"
    assert strat.generate_signal("AAPL", df, 40, None, []) is None
