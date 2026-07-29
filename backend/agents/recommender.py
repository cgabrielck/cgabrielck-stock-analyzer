import concurrent.futures
from typing import Any, Callable, Dict, List, Optional

from agents.data_fetcher import fetch_all_stocks, fetch_news
from agents.fundamental_analyzer import calculate_all_scores
from agents.sec_analyzer import get_latest_filing
from agents.auto_upgrader import agent_state
from agents.technical_analyzer import compute_all_technical
from agents.llm_agent import analyze_stocks_batch, is_available as llm_available
from agents.risk_analyzer import calculate_risk_adjusted_score, fetch_risk_metrics
from agents.market_regime import detect_global_market_regime
from agents.sentiment_analyzer import compute_sentiment
from agents.signal_pillars import calculate_five_pillar_score
from utils.constants import SECTOR_CN_MAP, STOCK_UNIVERSE
from utils.selection import MIN_RECOMMENDATION_METRICS, select_recommendations
from i18n import t

ENTRY_THRESHOLD: float = 60.0
FILL_THRESHOLD: float = 50.0
MAX_PER_SECTOR: int = 2
TOP_N: int = 5
TECHNICAL_CANDIDATES: int = 15


def run_full_analysis(
    progress_callback: Optional[Callable[[int, int], None]] = None,
    selected_tickers: Optional[List[str]] = None,
    custom_weights: Optional[Dict[str, float]] = None,
    filters: Optional[Dict[str, Any]] = None,
    lang: str = "zh_tw",
    llm_weight: float = 0.2,
    force_refresh: bool = False,
    use_llm_analysis: bool = True,
) -> Dict[str, Any]:
    agent_state.log_upgrade("开始新一轮分析")

    cn_map = {s["ticker"]: s["name_cn"] for s in STOCK_UNIVERSE}
    sector_map = {s["ticker"]: s["sector"] for s in STOCK_UNIVERSE}
    tier_map = {s["ticker"]: s.get("universe_tier", "core") for s in STOCK_UNIVERSE}

    all_data = fetch_all_stocks(selected_tickers, progress_callback, force_refresh)

    scored = calculate_all_scores(all_data, custom_weights)

    if filters:
        scored = _apply_filters(scored, filters)

    if not scored:
        return {
            "recommendations": [],
            "all_rankings": [],
            "scored_data": [],
            "source_health": agent_state.get_health_status(),
            "upgrade_logs": agent_state.get_upgrade_logs(),
            "cache_status": {},
            "error": "无法获取任何股票数据",
        }

    for s in scored:
        ticker = s["ticker"]
        s["name_cn"] = cn_map.get(ticker, s.get("longName", ticker))
        custom_sec = sector_map.get(ticker)
        if custom_sec:
            s["sector"] = custom_sec
        s["universe_tier"] = tier_map.get(ticker, "satellite")
        s["sector_cn"] = SECTOR_CN_MAP.get(s.get("sector"), s.get("sector", "未知"))

    market_regime = detect_global_market_regime(force_refresh=force_refresh)
    tickers = [stock["ticker"] for stock in scored]
    tech_data = compute_all_technical(tickers, force_refresh=force_refresh)
    for stock in scored:
        tech = tech_data.get(stock["ticker"], {})
        if not tech.get("error") and tech.get("signal_pillars"):
            pillars = calculate_five_pillar_score(
                tech, tech.get("risk_metrics"), market_regime.get("pillar_weights"),
            )
            tech["signal_pillars"] = pillars
            tech["technical_score"] = pillars["score"]
        tech_score = tech.get("technical_score")
        stock["technical_score"] = tech_score
        stock["signal_pillars"] = tech.get("signal_pillars")
        for key in (
            "dollar_volume_10d_avg", "dollar_volume_50d_avg", "dollar_volume_ratio",
            "volume_quality_score", "obv_trend", "volume_signal",
        ):
            stock[key] = tech.get(key)
        stock["risk_metrics"] = tech.get("risk_metrics", {"available": False, "risk_level": "unknown"})
        if tech.get("price") is not None:
            for key in ("price", "price_session", "price_source", "price_quote_time", "price_market_state", "price_stale"):
                stock[key] = tech.get(key)
        if tech_score is not None:
            stock["base_score"] = round(stock.get("growth_score", 0) * 0.7 + tech_score * 0.3, 1)
            stock["total_score"] = stock["base_score"]

    use_llm = use_llm_analysis and llm_available()
    if use_llm:
        candidates = sorted(
            scored, key=lambda stock: stock.get("total_score", 0) or 0, reverse=True,
        )[:TECHNICAL_CANDIDATES]
        scored_with_llm = analyze_stocks_batch(candidates, tech_data, lang, progress_callback, llm_weight)
        llm_tickers = {s["ticker"] for s in scored_with_llm}
        rest = [s for s in scored if s["ticker"] not in llm_tickers]
        scored = scored_with_llm + rest

    news_by_ticker = _fetch_news_batch([stock["ticker"] for stock in scored], force_refresh)
    for stock in scored:
        ticker = stock["ticker"]
        sentiment = compute_sentiment(news_by_ticker.get(ticker, []), tech_data.get(ticker, {}))
        stock["sentiment"] = sentiment
        stock["score_before_sentiment"] = stock.get("total_score", 0) or 0
        stock["sentiment_modifier_pct"] = _sentiment_modifier_pct(sentiment)
        stock["total_score"] = round(max(0, min(
            100, stock["score_before_sentiment"] * (1 + stock["sentiment_modifier_pct"] / 100),
        )), 1)

    for stock in scored:
        stock.update(calculate_risk_adjusted_score(
            stock.get("total_score", 0) or 0,
            stock.get("risk_metrics", {}),
        ))
    scored.sort(key=lambda stock: stock.get("risk_adjusted_score", 0) or 0, reverse=True)
    picks = select_recommendations(
        scored,
        entry_threshold=market_regime.get("entry_threshold", ENTRY_THRESHOLD),
        fill_threshold=market_regime.get("fill_threshold", FILL_THRESHOLD),
        max_per_sector=MAX_PER_SECTOR,
        top_n=TOP_N,
        min_metrics=MIN_RECOMMENDATION_METRICS,
        max_satellite=1,
        satellite_threshold=65.0,
        score_field="risk_adjusted_score",
    )

    recommendations: List[Dict[str, Any]] = []
    for rec in picks:
        sec_info = get_latest_filing(rec.get("cik"), rec["ticker"], lang=lang)
        rec["sec_insights"] = sec_info

        news = news_by_ticker.get(rec["ticker"], [])
        rec["news"] = news

        if rec.get("llm_reasoning"):
            rec["reasoning"] = rec["llm_reasoning"]
        else:
            rec["reasoning"] = _generate_reasoning(rec, news, lang)

        recommendations.append(rec)

    missing_risk = [rec["ticker"] for rec in recommendations if not rec.get("risk_metrics", {}).get("available")]
    if missing_risk:
        risk_by_ticker = fetch_risk_metrics(missing_risk)
        for rec in recommendations:
            if rec["ticker"] in risk_by_ticker:
                rec["risk_metrics"] = risk_by_ticker[rec["ticker"]]

    if recommendations:
        agent_state.log_recommendation(recommendations)
        agent_state.log_upgrade(f"成功推荐 {len(recommendations)} 只股票")

    use_llm = use_llm_analysis and llm_available()
    all_rankings: List[Dict[str, Any]] = []
    for s in scored:
        entry = {
            "rank": len(all_rankings) + 1,
            "ticker": s["ticker"],
            "name": cn_map.get(s["ticker"], s.get("longName", s["ticker"])),
            "sector": s.get("sector", "Unknown"),
            "price": s.get("price"),
            "growth_score": s.get("growth_score", 0),
            "model_score": s.get("total_score", 0),
            "risk_penalty": s.get("risk_penalty", 0),
            "risk_adjusted_score": s.get("risk_adjusted_score", s.get("total_score", 0)),
            "risk_level": s.get("risk_metrics", {}).get("risk_level", "unknown"),
            "timing_score": s.get("technical_score"),
            "sentiment_score": s.get("sentiment", {}).get("composite_score"),
            "sentiment_modifier_pct": s.get("sentiment_modifier_pct", 0),
            "dollar_volume_10d_avg": s.get("dollar_volume_10d_avg"),
            "dollar_volume_ratio": s.get("dollar_volume_ratio"),
            "revenue_growth": s.get("revenue_growth"),
            "eps_growth": s.get("eps_growth"),
            "profit_margin": s.get("profit_margin"),
            "peg": s.get("peg"),
            "roe": s.get("roe"),
            "debt_equity": s.get("debt_equity"),
        }
        if use_llm:
            entry["llm_score"] = s.get("llm_score")
            entry["technical_signal"] = s.get("llm_key_signal", "")
        all_rankings.append(entry)

    return {
        "recommendations": recommendations,
        "all_rankings": all_rankings,
        "scored_data": scored,
        "source_health": agent_state.get_health_status(),
        "upgrade_logs": agent_state.get_upgrade_logs(),
        "cache_status": {},
        "agent_summary": agent_state.get_summary(),
        "use_llm": use_llm,
        "market_regime": market_regime,
        "_debug_all_data": all_data,
    }


def _sentiment_modifier_pct(sentiment: Dict[str, Any]) -> float:
    score = sentiment.get("composite_score")
    if score is not None and score >= 80:
        return 3.0
    if score is not None and score <= 20:
        return -3.0
    return 0.0


def _fetch_news_batch(tickers: List[str], force_refresh: bool) -> Dict[str, List[Dict[str, Any]]]:
    results: Dict[str, List[Dict[str, Any]]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(tickers) or 1)) as executor:
        futures = {
            executor.submit(fetch_news, ticker, 5, force_refresh): ticker for ticker in tickers
        }
        for future in concurrent.futures.as_completed(futures):
            ticker = futures[future]
            try:
                results[ticker] = future.result()
            except Exception:
                results[ticker] = []
    return results


def _generate_reasoning(data: Dict[str, Any], news: List[Dict[str, Any]], lang: str = "zh_tw") -> str:
    parts: List[str] = []

    score = data.get("total_score", 0)
    if score >= 85:
        parts.append(t("reason.intro_great", lang, s=score))
    elif score >= 70:
        parts.append(t("reason.intro_good", lang, s=score))
    else:
        parts.append(t("reason.intro_normal", lang, s=score))

    rev = data.get("revenue_growth")
    if rev is not None:
        if rev >= 40:
            parts.append(t("reason.rev_rapid", lang, v=rev))
        elif rev >= 20:
            parts.append(t("reason.rev_steady", lang, v=rev))
        elif rev >= 10:
            parts.append(t("reason.rev_moderate", lang, v=rev))
        else:
            parts.append(t("reason.rev_weak", lang, v=rev))

    eps = data.get("eps_growth")
    if eps is not None:
        if eps >= 40:
            parts.append(t("reason.eps_rapid", lang, v=eps))
        elif eps >= 20:
            parts.append(t("reason.eps_steady", lang, v=eps))
        elif eps >= 10:
            parts.append(t("reason.eps_moderate", lang, v=eps))
        else:
            parts.append(t("reason.eps_weak", lang, v=eps))

    peg = data.get("peg")
    if peg is not None and peg > 0:
        if peg <= 0.5:
            parts.append(t("reason.peg_low", lang, v=peg))
        elif peg <= 1.0:
            parts.append(t("reason.peg_fair_low", lang, v=peg))
        elif peg <= 1.5:
            parts.append(t("reason.peg_fair_high", lang, v=peg))
        else:
            parts.append(t("reason.peg_high", lang, v=peg))

    roe = data.get("roe")
    if roe is not None:
        if roe >= 30:
            parts.append(t("reason.roe_great", lang, v=roe))
        elif roe >= 15:
            parts.append(t("reason.roe_good", lang, v=roe))
        else:
            parts.append(t("reason.roe_low", lang, v=roe))

    de = data.get("debt_equity")
    if de is not None:
        if de <= 0.5:
            parts.append(t("reason.de_low", lang, v=de))
        elif de <= 1.5:
            parts.append(t("reason.de_mid", lang, v=de))
        else:
            parts.append(t("reason.de_high", lang, v=de))

    price = data.get("price")
    high52 = data.get("fifty_two_week_high")
    low52 = data.get("fifty_two_week_low")
    if price and high52 and high52 > 0:
        dist = (price / high52 - 1) * 100
        if dist >= -10:
            pos = t("reason.52week_high", lang)
        elif dist >= -25:
            pos = t("reason.52week_mid", lang)
        else:
            pos = t("reason.52week_low", lang)
        parts.append(t("reason.52week", lang, d=dist, pos=pos))

    target = data.get("target_mean_price")
    if price and target and target > 0:
        upside = (target / price - 1) * 100
        parts.append(t("reason.upside", lang, t=target, p=upside))

    rating = data.get("rating_label")
    n_analysts = data.get("number_of_analysts")
    if rating and n_analysts:
        parts.append(t("reason.rating", lang, r=rating, n=n_analysts))

    mcap = data.get("market_cap")
    if mcap:
        mcap_b = mcap / 1e9
        if mcap_b >= 200:
            size = t("reason.mcap_large", lang)
        elif mcap_b >= 10:
            size = t("reason.mcap_mid", lang)
        else:
            size = t("reason.mcap_small", lang)
        parts.append(t("reason.mcap", lang, v=mcap_b, size=size))

    beta = data.get("beta")
    if beta is not None:
        if beta >= 1.2:
            vol = t("reason.beta_high", lang)
        elif beta >= 0.8:
            vol = t("reason.beta_mid", lang)
        else:
            vol = t("reason.beta_low", lang)
        parts.append(t("reason.beta", lang, v=beta, vol=vol))

    div = data.get("dividend_yield")
    if div and div > 0:
        parts.append(t("reason.div_yield", lang, v=div * 100))

    fcf = data.get("fcf")
    if fcf and mcap and mcap > 0:
        fcf_yield = fcf / mcap * 100
        status = t("reason.fcf_strong", lang) if fcf_yield >= 3 else t("reason.fcf_weak", lang)
        parts.append(t("reason.fcf_yield", lang, v=fcf_yield, status=status))

    inst = data.get("held_percent_institutions")
    if inst is not None:
        inst_pct = inst * 100
        if inst_pct >= 70:
            level = t("reason.inst_high", lang)
        elif inst_pct >= 40:
            level = t("reason.inst_mid", lang)
        else:
            level = t("reason.inst_low", lang)
        parts.append(t("reason.inst_own", lang, v=inst_pct, level=level))

    insider = data.get("insider_net_shares")
    if insider is not None:
        if insider > 0:
            parts.append(t("reason.insider_buy", lang, n=insider))
        elif insider < 0:
            parts.append(t("reason.insider_sell", lang, n=abs(insider)))

    esg = data.get("esg_score")
    if esg is not None:
        if esg >= 70:
            esg_level = t("reason.esg_leader", lang)
        elif esg >= 50:
            esg_level = t("reason.esg_avg", lang)
        else:
            esg_level = t("reason.esg_below", lang)
        parts.append(t("reason.esg", lang, v=esg, level=esg_level))

    if news:
        pos = sum(1 for n in news if n.get("sentiment") == "positive")
        neg = sum(1 for n in news if n.get("sentiment") == "negative")
        total = len(news)
        if pos > neg:
            parts.append(t("reason.news_pos", lang, n=total, p=pos))
        elif neg > pos:
            parts.append(t("reason.news_neg", lang, n=total, neg=neg))
        else:
            parts.append(t("reason.news_neutral", lang, n=total))

    dq = data.get("data_quality", {})
    avail = dq.get("metrics_available", 0)
    total_m = dq.get("metrics_total", 6)
    ratio = avail / total_m if total_m > 0 else 0
    if ratio >= 0.8:
        conf = t("reason.confidence_high", lang)
    elif ratio >= 0.5:
        conf = t("reason.confidence_medium", lang)
    else:
        conf = t("reason.confidence_low", lang)
    parts.append(t("reason.confidence", lang, level=conf, a=avail, t=total_m))

    return "。".join(parts) + "。"


def _apply_filters(
    scored: List[Dict[str, Any]],
    filters: Dict[str, Any],
) -> List[Dict[str, Any]]:
    result = list(scored)
    min_revenue = filters.get("min_revenue_growth")
    if min_revenue is not None:
        result = [s for s in result if s.get("revenue_growth") is None or s["revenue_growth"] >= min_revenue]
    max_peg = filters.get("max_peg")
    if max_peg is not None:
        result = [s for s in result if s.get("peg") is None or s["peg"] <= max_peg]
    min_roe = filters.get("min_roe")
    if min_roe is not None:
        result = [s for s in result if s.get("roe") is None or s["roe"] >= min_roe]
    max_de = filters.get("max_debt_equity")
    if max_de is not None:
        result = [s for s in result if s.get("debt_equity") is None or s["debt_equity"] <= max_de]
    return result
