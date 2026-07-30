import concurrent.futures
import queue
import time
from typing import Any, Callable, Dict, List, Optional

from agents.data_fetcher import (
    fetch_news,
    fetch_options_chain,
    fetch_stock_data,
    fetch_trading_session_ranges,
    get_seed_fallback,
)
from agents.fundamental_analyzer import calculate_growth_score
from agents.llm_agent import suggest_trading_strategy
from agents.risk_analyzer import calculate_risk_adjusted_score
from agents.sec_analyzer import get_latest_filing
from agents.technical_analyzer import compute_technical_indicators
from utils.constants import STOCK_UNIVERSE


ProgressCallback = Callable[[Dict[str, Any]], None]
MARKET_DATA_TIMEOUT_SECONDS = 12.0
CORE_DATA_TIMEOUT_SECONDS = 35.0
DEFAULT_BATCH_TIMEOUT_SECONDS = 150.0


def analyze_ticker(
    ticker: str,
    lang: str = "zh_tw",
    force_refresh: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    analysis_started = time.monotonic()
    _emit(progress_callback, ticker, "fundamental", "started")
    _emit(progress_callback, ticker, "technical", "started")
    def timed(call: Callable[..., Dict[str, Any]], *args: Any, **kwargs: Any) -> tuple[Dict[str, Any], int]:
        started = time.monotonic()
        value = call(*args, **kwargs)
        return value, round((time.monotonic() - started) * 1000)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    try:
        stock_future = executor.submit(timed, fetch_stock_data, ticker, force_refresh=force_refresh)
        technical_future = executor.submit(
            timed, compute_technical_indicators, ticker, period="1y", force_refresh=force_refresh,
        )
        done, _ = concurrent.futures.wait(
            (stock_future, technical_future), timeout=CORE_DATA_TIMEOUT_SECONDS,
        )
        if stock_future in done:
            stock, fundamental_ms = stock_future.result()
        else:
            stock_future.cancel()
            stock = get_seed_fallback(ticker, "fundamental_timeout") or {
                "ticker": ticker, "error": "fundamental_timeout",
            }
            fundamental_ms = round(CORE_DATA_TIMEOUT_SECONDS * 1000)
        if technical_future in done:
            technical, technical_ms = technical_future.result()
        else:
            technical_future.cancel()
            technical = {"ticker": ticker, "error": "technical_timeout"}
            technical_ms = round(CORE_DATA_TIMEOUT_SECONDS * 1000)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    if stock.get("error"):
        _emit(progress_callback, ticker, "fundamental", "failed")
        return {"ticker": ticker, "error": stock["error"], "stage": "fundamental"}
    _emit(progress_callback, ticker, "fundamental", "completed")
    if technical.get("error"):
        _emit(progress_callback, ticker, "technical", "failed")
        return {"ticker": ticker, "error": technical["error"], "stage": "technical", "technical": technical}
    _emit(progress_callback, ticker, "technical", "completed")

    metadata = next((item for item in STOCK_UNIVERSE if item["ticker"] == ticker), {})
    for key in ("name_cn", "name_tw", "name_en", "sector", "universe_tier"):
        if metadata.get(key) is not None:
            stock[key] = metadata[key]

    growth_score, details, metrics_used = calculate_growth_score(stock)
    stock.update({
        "growth_score": growth_score,
        "total_score": growth_score,
        "score_details": details,
        "metrics_used": metrics_used,
    })
    technical_score = technical.get("technical_score")
    if technical_score is not None:
        stock["technical_score"] = technical_score
        stock["total_score"] = round(growth_score * 0.7 + technical_score * 0.3, 1)
    stock["risk_metrics"] = technical.get("risk_metrics", {"available": False})
    stock.update(calculate_risk_adjusted_score(stock["total_score"], stock["risk_metrics"]))
    result = analyze_selected_stock(
        stock, lang=lang, technical=technical, progress_callback=progress_callback,
        force_refresh=force_refresh,
    )
    result["timing"] = {
        "total_ms": round((time.monotonic() - analysis_started) * 1000),
        "stages": {
            "fundamental": {"duration_ms": fundamental_ms},
            "technical": {"duration_ms": technical_ms},
            **result.get("timing", {}).get("stages", {}),
        },
    }
    return result


def analyze_tickers(
    tickers: List[str],
    lang: str = "zh_tw",
    force_refresh: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
    batch_timeout_seconds: float = DEFAULT_BATCH_TIMEOUT_SECONDS,
) -> Dict[str, Dict[str, Any]]:
    unordered: Dict[str, Dict[str, Any]] = {}
    events: queue.Queue[Dict[str, Any]] = queue.Queue()

    def analyze(ticker: str) -> Dict[str, Any]:
        try:
            return analyze_ticker(ticker, lang=lang, force_refresh=force_refresh, progress_callback=events.put)
        except Exception as exc:
            return {"ticker": ticker, "error": str(exc), "stage": "unexpected"}

    started_at = time.monotonic()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=min(3, len(tickers) or 1))
    try:
        futures = {executor.submit(analyze, ticker): ticker for ticker in tickers}
        pending = set(futures)
        while pending and time.monotonic() - started_at < batch_timeout_seconds:
            remaining_seconds = max(0.0, batch_timeout_seconds - (time.monotonic() - started_at))
            try:
                event = events.get(timeout=min(0.2, remaining_seconds))
                _safe_callback(progress_callback, event)
            except queue.Empty:
                _safe_callback(progress_callback, {"ticker": None, "stage": "heartbeat", "state": "running"})
            completed = {future for future in pending if future.done()}
            for future in completed:
                ticker = futures[future]
                while not events.empty():
                    _safe_callback(progress_callback, events.get_nowait())
                unordered[ticker] = future.result()
                _safe_callback(progress_callback, {"ticker": ticker, "stage": "ticker", "state": "completed"})
            pending -= completed
        for future in pending:
            ticker = futures[future]
            future.cancel()
            unordered[ticker] = {"ticker": ticker, "error": "analysis_timeout", "stage": "timeout"}
            _safe_callback(progress_callback, {"ticker": ticker, "stage": "ticker", "state": "failed"})
        while not events.empty():
            _safe_callback(progress_callback, events.get_nowait())
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return {ticker: unordered[ticker] for ticker in tickers}


def analyze_selected_stock(
    stock: Dict[str, Any], lang: str = "zh_tw", technical: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[ProgressCallback] = None, force_refresh: bool = False,
) -> Dict[str, Any]:
    ticker = stock["ticker"]
    technical = technical or compute_technical_indicators(ticker, period="1y", force_refresh=force_refresh)
    if technical.get("error"):
        return {"ticker": ticker, "error": technical["error"]}

    short_score = _short_term_score(stock, technical)
    long_score = _long_term_score(stock, technical)
    avoid = _avoid_assessment(stock, technical, short_score, long_score)
    trade_plan = _build_trade_plan(stock, technical, short_score, long_score, avoid)
    current_price = technical.get("price") or stock.get("price") or 0
    _emit(progress_callback, ticker, "market_data", "started")
    market_started = time.monotonic()
    provider_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    market_deadline = time.monotonic() + MARKET_DATA_TIMEOUT_SECONDS
    provider_futures = {
        "options": provider_executor.submit(fetch_options_chain, ticker, current_price=current_price, force_refresh=force_refresh),
        "sessions": provider_executor.submit(fetch_trading_session_ranges, ticker, force_refresh=force_refresh),
        "news": provider_executor.submit(fetch_news, ticker, max_items=5, force_refresh=force_refresh),
        "sec": provider_executor.submit(
            get_latest_filing, stock.get("cik"), ticker, lang,
            force_refresh=force_refresh, include_llm_summary=False,
            deadline_monotonic=market_deadline,
        ),
    }
    done, pending = concurrent.futures.wait(provider_futures.values(), timeout=MARKET_DATA_TIMEOUT_SECONDS)
    values: Dict[str, Any] = {}
    for name, future in provider_futures.items():
        if future in done:
            try:
                values[name] = future.result()
            except Exception as exc:
                values[name] = {"error": str(exc)} if name != "news" else []
        else:
            future.cancel()
            values[name] = {"error": "provider_timeout"} if name != "news" else []
    provider_executor.shutdown(wait=False, cancel_futures=True)
    options = values["options"]
    session_ranges = values["sessions"]
    news = values["news"]
    sec_evidence = values["sec"]
    enrichment_errors = {
        "options": options.get("error") if isinstance(options, dict) else None,
        "sessions": session_ranges.get("error") if isinstance(session_ranges, dict) else None,
        "news": "unavailable" if not news else None,
        "sec": sec_evidence.get("status") if not sec_evidence.get("available") else None,
    }
    options_plan = _build_options_plan(options, trade_plan)
    market_ms = round((time.monotonic() - market_started) * 1000)
    _emit(progress_callback, ticker, "market_data", "completed")
    _emit(progress_callback, ticker, "strategy", "started")
    strategy_started = time.monotonic()
    short_view = _view(short_score, avoid["active"])
    long_view = _view(long_score, avoid["active"] and long_score < 50)
    strategy = suggest_trading_strategy(
        ticker,
        stock,
        technical,
        current_price,
        options_data=options,
        news_data=news,
        lang=lang,
        decision_context={
            "stance": trade_plan.get("stance"),
            "action": trade_plan.get("action"),
            "short_view": short_view,
            "long_view": long_view,
            "short_score": short_score,
            "long_score": long_score,
            "avoid_reasons": avoid.get("reasons", []),
        },
    )
    strategy_ms = round((time.monotonic() - strategy_started) * 1000)
    _emit(progress_callback, ticker, "strategy", "failed" if strategy.get("error") else "completed")
    return {
        "ticker": ticker,
        "short_term": {
            "score": short_score,
            "view": short_view,
            "horizon": "5-20 trading days",
        },
        "long_term": {
            "score": long_score,
            "view": long_view,
            "horizon": "6-18 months",
        },
        "avoid": avoid,
        "technical": technical,
        "strategy": strategy,
        "trade_plan": trade_plan,
        "options": options,
        "options_plan": options_plan,
        "session_ranges": session_ranges,
        "validation": {"available": False, "reason": "not_run"},
        "news": news,
        "sec_evidence": sec_evidence,
        "enrichment_errors": {key: value for key, value in enrichment_errors.items() if value},
        "quant_score": stock.get("total_score"),
        "risk_adjusted_score": stock.get("risk_adjusted_score"),
        "provenance": {
            "fundamentals": stock.get("data_quality", {}),
            "technical": {
                "source": technical.get("technical_source"),
                "period": technical.get("technical_period"),
                "as_of": technical.get("technical_as_of"),
                "fetched_at": technical.get("technical_fetched_at"),
                "from_cache": technical.get("technical_from_cache", False),
            },
            "options": {key: options.get(key) for key in ("source", "fetched_at", "as_of", "from_cache")},
            "sessions": {key: session_ranges.get(key) for key in ("source", "fetched_at", "as_of", "from_cache")},
            "sec": sec_evidence.get("provenance", {}),
        },
        "timing": {
            "stages": {
                "market_data": {"duration_ms": market_ms},
                "strategy": {"duration_ms": strategy_ms},
            },
        },
    }


def _emit(callback: Optional[ProgressCallback], ticker: str, stage: str, state: str) -> None:
    _safe_callback(callback, {"ticker": ticker, "stage": stage, "state": state, "timestamp": time.monotonic()})


def _safe_callback(callback: Optional[ProgressCallback], event: Dict[str, Any]) -> None:
    if callback:
        try:
            callback(event)
        except Exception:
            pass


def _short_term_score(stock: Dict[str, Any], technical: Dict[str, Any]) -> float:
    score = float(technical.get("technical_score") or 0) * 0.55
    price = technical.get("price") or 0
    ema9, ema21, ema50 = technical.get("ema_9"), technical.get("ema_21"), technical.get("ema_50")
    if ema9 and ema21 and ema50:
        score += 15 if ema9 > ema21 > ema50 else 5 if ema9 > ema21 else 0
    adx = technical.get("adx_14")
    if adx is not None:
        score += 10 if adx >= 25 else 5 if adx >= 18 else 0
    if ema21 and price > ema21:
        score += 10
    penalty = float(stock.get("risk_penalty") or 0)
    score += max(0, 10 - penalty)
    return round(max(0, min(100, score)), 1)


def _long_term_score(stock: Dict[str, Any], technical: Dict[str, Any]) -> float:
    fundamental = float(stock.get("growth_score") or 0)
    price = technical.get("price") or 0
    sma200 = technical.get("sma_200")
    ema200 = technical.get("ema_200")
    trend = 0.0
    if sma200 and price > sma200:
        trend += 10
    if ema200 and price > ema200:
        trend += 10
    penalty = float(stock.get("risk_penalty") or 0)
    return round(max(0, min(100, fundamental * 0.8 + trend - penalty * 0.5)), 1)


def _avoid_assessment(
    stock: Dict[str, Any], technical: Dict[str, Any], short_score: float, long_score: float,
) -> Dict[str, Any]:
    reasons = []
    reason_codes = []
    price = technical.get("price") or 0
    sma50, sma200 = technical.get("sma_50"), technical.get("sma_200")
    if short_score < 45 and long_score < 50:
        reasons.append("Weak short- and long-horizon scores")
        reason_codes.append("weak_scores")
    if sma50 and sma200 and price < sma50 < sma200:
        reasons.append("Price is below both SMA50 and SMA200")
        reason_codes.append("below_long_trend")
    if stock.get("risk_penalty_level") == "high":
        reasons.append("High volatility or drawdown risk")
        reason_codes.append("high_risk")
    if stock.get("metrics_used", 0) < 4:
        reasons.append("Insufficient fundamental coverage")
        reason_codes.append("insufficient_fundamentals")
    return {
        "active": bool(reasons),
        "reasons": reasons,
        "reason_codes": reason_codes,
        "review": "Review after 5 trading days or a material signal change",
        "invalidation": "Avoid status clears when risk and trend conditions recover",
    }


def _view(score: float, avoid: bool) -> str:
    if avoid:
        return "avoid"
    if score >= 70:
        return "bullish"
    if score >= 50:
        return "neutral"
    return "avoid"


def _build_trade_plan(
    stock: Dict[str, Any],
    technical: Dict[str, Any],
    short_score: float,
    long_score: float,
    avoid: Dict[str, Any],
) -> Dict[str, Any]:
    price = float(technical.get("price") or stock.get("price") or 0)
    if price <= 0:
        return {"action": "no_trade", "error": "price_unavailable"}
    atr = max(abs(float(technical.get("atr_14") or price * 0.025)), price * 0.005)
    if atr > price * 0.5:
        return {
            "stance": "neutral", "action": "no_trade", "position_side": "none",
            "stop_type": "invalidation_only", "error": "price_scale_mismatch",
            "reference_price": round(price, 2), "atr_14": round(atr, 2),
        }
    bb_lower = technical.get("bb_lower")
    bb_upper = technical.get("bb_upper")
    ema21 = technical.get("ema_21")
    sma50 = technical.get("sma_50")
    bullish = short_score >= 60 and not avoid.get("active")
    bearish = short_score < 45 or (avoid.get("active") and long_score < 50)
    if bullish:
        stance, action, setup = "bullish", "buy", "pullback_or_breakout"
        position_side, stop_type = "long", "sell_stop"
        support_candidates = [value for value in (ema21, sma50, bb_lower) if value and value < price]
        support = max(support_candidates) if support_candidates else price - atr
        entry_low = max(support, price - 0.75 * atr)
        entry_high = price + 0.15 * atr
        stop = min(entry_low - 0.8 * atr, support - 0.35 * atr)
        risk = max(entry_high - stop, atr)
        targets = [entry_high + 1.5 * risk, entry_high + 2.5 * risk]
        confirmation = max(price + 0.25 * atr, float(bb_upper or 0))
        execution = "Regular session; use a limit order after the first 15 minutes"
    elif bearish:
        stance, action, setup = "bearish", "avoid_or_hedge", "failed_rally_or_breakdown"
        position_side, stop_type = "short_or_hedge", "buy_to_cover_stop"
        resistance_candidates = [value for value in (ema21, sma50, bb_upper) if value and value > price]
        resistance = min(resistance_candidates) if resistance_candidates else price + atr
        entry_low = price - 0.15 * atr
        entry_high = min(resistance, price + 0.75 * atr)
        stop = max(entry_high + 0.8 * atr, resistance + 0.35 * atr)
        risk = max(stop - entry_low, atr)
        targets = [max(0.01, entry_low - 1.5 * risk), max(0.01, entry_low - 2.5 * risk)]
        confirmation = max(0.01, min(price - 0.25 * atr, float(bb_lower or price)))
        execution = "Regular session; wait for a failed rally or confirmed breakdown"
    else:
        stance, action, setup = "neutral", "watch", "wait_for_confirmation"
        position_side, stop_type = "none", "invalidation_only"
        entry_low, entry_high = price - 0.5 * atr, price + 0.5 * atr
        stop, risk = price - 1.5 * atr, 1.5 * atr
        targets = [price + 1.5 * atr, price + 2.5 * atr]
        confirmation = price + atr
        execution = "No immediate entry; reassess during regular-session liquidity"
    entry_reference = entry_high if stance == "bullish" else entry_low if stance == "bearish" else price
    return {
        "stance": stance,
        "action": action,
        "position_side": position_side,
        "stop_type": stop_type,
        "setup": setup,
        "execution_window": execution,
        "entry_zone": {"low": round(entry_low, 2), "high": round(entry_high, 2)},
        "confirmation_price": round(confirmation, 2),
        "stop_loss": round(stop, 2),
        "targets": [round(value, 2) for value in targets],
        "risk_reward": [1.5, 2.5],
        "entry_reference": round(entry_reference, 2),
        "risk_per_share": round(risk, 2),
        "atr_14": round(atr, 2),
        "method": "Deterministic ATR and observed support/resistance levels",
        "decision_basis": _decision_basis(
            stance, action, short_score, long_score, avoid, technical,
        ),
    }


def _decision_basis(
    stance: str,
    action: str,
    short_score: float,
    long_score: float,
    avoid: Dict[str, Any],
    technical: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "stance": stance,
        "action": action,
        "short_score": short_score,
        "long_score": long_score,
        "technical_score": technical.get("technical_score"),
        "risk_level": technical.get("risk_metrics", {}).get("risk_level", "unknown"),
        "avoid_active": bool(avoid.get("active")),
        "avoid_reason_codes": avoid.get("reason_codes", []),
    }


def _build_options_plan(options: Dict[str, Any], trade_plan: Dict[str, Any]) -> Dict[str, Any]:
    from agents.options_risk_agent import build_option_agent_trace

    stance = trade_plan.get("stance")
    if options.get("error"):
        return {
            "action": "none",
            "reason": options.get("error"),
            "error_code": options.get("error_code", "provider_error"),
            "retry_after_seconds": options.get("retry_after_seconds"),
        }
    if stance not in {"bullish", "bearish"}:
        return {"action": "none", "reason": "No directional edge"}
    option_type = "call" if stance == "bullish" else "put"
    candidates = options.get("calls" if option_type == "call" else "puts", [])
    liquid = [
        contract for contract in candidates
        if contract.get("ask") and contract.get("ask") > 0
        and contract.get("bid") is not None
        and (contract.get("open_interest", 0) >= 100 or contract.get("volume", 0) >= 25)
        and (contract.get("spread_pct") is None or contract["spread_pct"] <= 20)
    ]
    if not liquid:
        return {"action": "none", "reason": "No sufficiently liquid near-the-money contract"}
    contract = liquid[0]
    contract = {**contract, "option_type": option_type}
    entry = contract.get("mid") or contract.get("ask")
    if not entry:
        return {"action": "none", "reason": "Option premium unavailable"}
    stop = round(float(entry) * 0.65, 2)
    targets = [round(float(entry) * 1.35, 2), round(float(entry) * 1.7, 2)]
    expiry = options.get("selected_expiry") or options.get("nearest_expiry")
    max_entry = round(min(float(contract["ask"]), float(entry) * 1.05), 2)
    return {
        "action": "buy_to_open",
        "option_type": option_type,
        "expiry": expiry,
        "contract": contract,
        "max_entry_premium": max_entry,
        "stop_premium": stop,
        "take_profit_premiums": targets,
        "exit_rule": "Scale out at +35% and +70%; exit before expiry or on underlying invalidation",
        "underlying_invalidation": trade_plan.get("stop_loss"),
        "max_position_risk_pct": 1.0,
        "method": "Nearest liquid near-the-money contract with at least 21 DTE when available",
        "agent_trace": build_option_agent_trace(
            contract, expiry, max_entry, stop, targets, trade_plan.get("stop_loss"),
        ),
    }
