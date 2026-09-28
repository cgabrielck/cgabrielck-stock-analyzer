"""Named defensive_gld — operator-chosen; must not steal research_list default."""
from unittest.mock import MagicMock

import pandas as pd

from backend.api.app import DEFAULT_AI_MODE
from backend.trading.engine.cadence import CADENCE_INTRADAY
from backend.trading.strategies.defensive_gld import DefensiveGldStrategy, defensive_bucket
from backend.trading.strategies.registry import KNOWN_STRATEGIES, get_strategy
from backend.trading.strategies.research_list import ResearchListStrategy


def _bars(n=40, last=180.0):
    close = [last - (n - i) * 0.05 for i in range(n)]
    return pd.DataFrame(
        {
            "Open": close,
            "High": [c * 1.01 for c in close],
            "Low": [c * 0.99 for c in close],
            "Close": close,
            "Volume": [2_000_000] * n,
        }
    )


def test_registry_has_defensive_gld_and_keeps_research_list():
    assert "defensive_gld" in KNOWN_STRATEGIES
    assert "research_list" in KNOWN_STRATEGIES
    assert get_strategy("defensive_gld").strategy_id == "defensive_gld"
    assert get_strategy("research_list").strategy_id == "research_list"
    assert isinstance(get_strategy("research_list"), ResearchListStrategy)


def test_default_strategy_is_unchanged():
    assert DEFAULT_AI_MODE["strategy"] != "defensive_gld"
    assert DEFAULT_AI_MODE["strategy"] == "breakout"
    assert DEFAULT_AI_MODE.get("cadence", CADENCE_INTRADAY) == CADENCE_INTRADAY
    assert get_strategy("does_not_exist").strategy_id == "adaptive"


def test_defensive_bucket_maps_cgab_regimes():
    assert defensive_bucket("high_volatility") == "PANIC"
    assert defensive_bucket("bear") == "RED"
    assert defensive_bucket("bull") == "GREEN"
    assert defensive_bucket("neutral") == "YELLOW"
    assert defensive_bucket(None) == "YELLOW"


def test_diagnose_skips_non_gld_even_in_panic():
    strat = DefensiveGldStrategy()
    strat.set_regime({"regime": "high_volatility"})
    assert strat.diagnose_entry("AAPL", _bars(), 90, None, []) == "not_defensive_asset"
    assert strat.generate_signal("AAPL", _bars(), 90, None, []) is None


def test_diagnose_skips_when_regime_not_red_or_panic():
    strat = DefensiveGldStrategy()
    strat.set_regime({"regime": "bull"})
    assert strat.diagnose_entry("GLD", _bars(), 50, None, []) == "not_defensive_regime"
    strat.set_regime({"regime": "neutral"})
    assert strat.diagnose_entry("GLD", _bars(), 50, None, []) == "not_defensive_regime"
    strat.set_regime({})
    assert strat.diagnose_entry("GLD", _bars(), 50, None, []) == "not_defensive_regime"


def test_generate_buys_gld_in_red_and_panic():
    strat = DefensiveGldStrategy()
    df = _bars()
    strat.set_regime({"regime": "bear"})
    red = strat.generate_signal("GLD", df, 50, None, [])
    assert red is not None
    assert red.strategy_id == "defensive_gld"
    assert red.ticker == "GLD"
    assert red.meta["regime_bucket"] == "RED"
    assert red.stop_loss_price < red.entry_price < red.take_profit_price

    strat.set_regime({"regime": "high_volatility"})
    panic = strat.generate_signal("GLD", df, 50, None, [])
    assert panic is not None
    assert panic.meta["regime_bucket"] == "PANIC"
    assert panic.confidence > red.confidence


def test_llm_bearish_never_orders():
    strat = DefensiveGldStrategy()
    strat.set_regime({"regime": "high_volatility"})
    assert strat.diagnose_entry("GLD", _bars(), 50, "bearish", []) == "llm_bearish"
    assert strat.generate_signal("GLD", _bars(), 50, "bearish", []) is None


def test_already_held_gld():
    strat = DefensiveGldStrategy()
    strat.set_regime({"regime": "bear"})
    held = [{"symbol": "GLD", "quantity": 10}]
    assert strat.diagnose_entry("GLD", _bars(), 50, None, held) == "already_held"


def test_history_short():
    strat = DefensiveGldStrategy()
    strat.set_regime({"regime": "bear"})
    assert strat.diagnose_entry("GLD", _bars(n=10), 50, None, []) == "history_short"


def test_exit_on_green_regime():
    strat = DefensiveGldStrategy()
    strat.set_regime({"regime": "bull"})
    df = _bars()
    px = float(df["Close"].iloc[-1])
    sig = strat.check_exit("GLD", px * 0.98, px, df, px * 0.90, px * 1.20)
    assert sig is not None
    assert sig.reason == "signal_reversal"


def test_hold_gld_in_yellow():
    strat = DefensiveGldStrategy()
    strat.set_regime({"regime": "neutral"})
    df = _bars()
    px = float(df["Close"].iloc[-1])
    assert strat.check_exit("GLD", px * 0.98, px, df, px * 0.90, px * 1.20) is None


def test_worker_default_universe_is_gld_only():
    from backend.trading.engine.worker import TradingWorker

    worker = TradingWorker(
        broker=MagicMock(),
        store=MagicMock(),
        manager=MagicMock(),
        reconciler=MagicMock(),
        strategy_id="defensive_gld",
    )
    assert worker.ticker_universe == ["GLD"]
    worker_rl = TradingWorker(
        broker=MagicMock(),
        store=MagicMock(),
        manager=MagicMock(),
        reconciler=MagicMock(),
        strategy_id="research_list",
    )
    assert worker_rl.ticker_universe != ["GLD"]
    assert "AAPL" in worker_rl.ticker_universe
    assert worker_rl.strategy.strategy_id == "research_list"
