from typing import Any, Dict, Optional

import pandas as pd
import yfinance as yf

from utils.cache import cache


REGIME_CONFIG: Dict[str, Dict[str, Any]] = {
    "bull": {
        "entry_threshold": 60.0, "fill_threshold": 50.0, "target_allocation": 0.90,
        "pillar_weights": {"trend": 0.30, "momentum": 0.25, "mean_reversion": 0.10, "volume": 0.20, "risk": 0.15},
    },
    "neutral": {
        "entry_threshold": 65.0, "fill_threshold": 55.0, "target_allocation": 0.70,
        "pillar_weights": {"trend": 0.25, "momentum": 0.20, "mean_reversion": 0.15, "volume": 0.20, "risk": 0.20},
    },
    "bear": {
        "entry_threshold": 72.0, "fill_threshold": 65.0, "target_allocation": 0.40,
        "pillar_weights": {"trend": 0.15, "momentum": 0.15, "mean_reversion": 0.25, "volume": 0.15, "risk": 0.30},
    },
    "high_volatility": {
        "entry_threshold": 75.0, "fill_threshold": 68.0, "target_allocation": 0.40,
        "pillar_weights": {"trend": 0.15, "momentum": 0.10, "mean_reversion": 0.20, "volume": 0.15, "risk": 0.40},
    },
}


def classify_market_regime(
    spy_close: pd.Series,
    vix_close: Optional[pd.Series] = None,
) -> Dict[str, Any]:
    spy = spy_close.dropna()
    if len(spy) < 200:
        return _result("neutral", available=False, spy_price=None, sma50=None, sma200=None, vix=None)

    price = float(spy.iloc[-1])
    sma50 = float(spy.tail(50).mean())
    sma200 = float(spy.tail(200).mean())
    vix_series = vix_close.dropna() if vix_close is not None else pd.Series(dtype=float)
    vix = float(vix_series.iloc[-1]) if not vix_series.empty else None

    if vix is not None and vix >= 25:
        regime = "high_volatility"
    elif price > sma200 and sma50 > sma200:
        regime = "bull"
    elif price < sma200 and sma50 < sma200:
        regime = "bear"
    else:
        regime = "neutral"
    return _result(regime, available=True, spy_price=price, sma50=sma50, sma200=sma200, vix=vix)


def detect_global_market_regime(force_refresh: bool = False) -> Dict[str, Any]:
    if not force_refresh:
        cached = cache.get("global_market_regime", "strategy")
        if cached:
            return cached
    try:
        data = yf.download(["SPY", "^VIX"], period="1y", progress=False, auto_adjust=True)
        close = data["Close"] if "Close" in data.columns else data
        result = classify_market_regime(close["SPY"], close.get("^VIX"))
    except Exception:
        result = _result("neutral", available=False, spy_price=None, sma50=None, sma200=None, vix=None)
    cache.set("global_market_regime", "strategy", result)
    return result


def _result(
    regime: str,
    *,
    available: bool,
    spy_price: Optional[float],
    sma50: Optional[float],
    sma200: Optional[float],
    vix: Optional[float],
) -> Dict[str, Any]:
    return {
        "regime": regime,
        "available": available,
        "spy_price": round(spy_price, 2) if spy_price is not None else None,
        "sma50": round(sma50, 2) if sma50 is not None else None,
        "sma200": round(sma200, 2) if sma200 is not None else None,
        "vix": round(vix, 2) if vix is not None else None,
        **REGIME_CONFIG[regime],
    }
