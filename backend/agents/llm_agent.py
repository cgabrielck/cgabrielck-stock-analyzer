import json
import os
from typing import Any, Dict, List, Optional

import time as _time

from dotenv import load_dotenv
from openai import OpenAI

from agents.auto_upgrader import agent_state
from config import get_secret

load_dotenv()

_API_KEY: Optional[str] = get_secret("LLM_API_KEY")
_BASE_URL: Optional[str] = str(get_secret("LLM_BASE_URL") or "").strip() or None
_CHAT_MODEL: str = str(get_secret("LLM_MODEL") or "deepseek-chat").strip() or "deepseek-chat"
_REASONING_MODEL: str = str(get_secret("LLM_REASONING_MODEL") or "").strip() or _CHAT_MODEL
try:
    _LLM_TIMEOUT_SECONDS = min(120.0, max(1.0, float(get_secret("LLM_TIMEOUT_SECONDS") or "45")))
except (TypeError, ValueError):
    _LLM_TIMEOUT_SECONDS = 45.0

_CLIENT: Optional[OpenAI] = None


def _get_client() -> Optional[OpenAI]:
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    if not _API_KEY or not _BASE_URL:
        return None
    _CLIENT = OpenAI(
        api_key=_API_KEY, base_url=_BASE_URL,
        timeout=_LLM_TIMEOUT_SECONDS, max_retries=0,
    )
    return _CLIENT


def is_available() -> bool:
    return _get_client() is not None


def get_model_for_task(task: str) -> str:
    """Keep high-volume structured tasks on chat; reserve reasoning for deep strategy work."""
    return _REASONING_MODEL if task == "strategy" else _CHAT_MODEL


def get_public_config() -> Dict[str, Any]:
    return {
        "configured": bool(_API_KEY and _BASE_URL),
        "provider": "openai-compatible",
        "base_url": _BASE_URL,
        "chat_model": _CHAT_MODEL,
        "reasoning_model": _REASONING_MODEL,
        "timeout_seconds": _LLM_TIMEOUT_SECONDS,
        "uses_separate_reasoner": _REASONING_MODEL != _CHAT_MODEL,
    }


def _create_completion(client: OpenAI, task: str, **kwargs: Any) -> Any:
    primary_model = get_model_for_task(task)
    try:
        return client.chat.completions.create(model=primary_model, **kwargs)
    except Exception:
        if primary_model == _CHAT_MODEL:
            raise
        return client.chat.completions.create(model=_CHAT_MODEL, **kwargs)


_SYSTEM_PROMPT = """You are a professional equity analyst. Given fundamental data and technical indicators for a stock, rate it from 0-100 and provide concise analysis.

Respond in JSON format only:
{
  "final_score": <0-100>,
  "reasoning": "<2-3 sentence analysis in Chinese combining fundamentals and technicals>",
  "technical_summary": "<1 sentence technical outlook>",
  "key_signal": "<bullish|neutral|bearish>"
}

Scoring guidelines:
- 85+: Strong buy (exceptional fundamentals + bullish technicals)
- 70-84: Buy (solid fundamentals + positive technicals)
- 50-69: Hold (mixed signals)
- 30-49: Weak (below-average fundamentals or bearish technicals)
- <30: Avoid (poor fundamentals + bearish technicals)

Consider:
- Revenue/EPS growth trends, profitability (PEG, ROE, margins)
- Debt levels, institutional confidence
- RSI (overbought/oversold), MACD trend, price vs SMA50
- Bollinger Bands position, volume trends
- Do NOT recommend stocks with RSI > 75 (overbought) or price far above upper BB
- Favor stocks near SMA50 support with positive MACD momentum"""


def analyze_stock(
    ticker: str,
    fundamental_data: Dict[str, Any],
    technical_data: Dict[str, Any],
    lang: str = "zh_tw",
) -> Dict[str, Any]:
    client = _get_client()
    if client is None:
        return {"error": "LLM not configured", "final_score": None, "reasoning": "", "technical_summary": "", "key_signal": "neutral"}

    price = fundamental_data.get("price") or technical_data.get("price")
    sector = fundamental_data.get("sector", "N/A")
    industry = fundamental_data.get("industry", "N/A")
    mcap = fundamental_data.get("market_cap")
    mcap_str = f"${mcap / 1e9:.1f}B" if mcap else "N/A"

    metrics = {
        "Revenue Growth": f"{fundamental_data.get('revenue_growth', 'N/A')}%" if fundamental_data.get('revenue_growth') is not None else "N/A",
        "EPS Growth": f"{fundamental_data.get('eps_growth', 'N/A')}%" if fundamental_data.get('eps_growth') is not None else "N/A",
        "Profit Margin": f"{fundamental_data.get('profit_margin', 'N/A')}%" if fundamental_data.get('profit_margin') is not None else "N/A",
        "PEG": f"{fundamental_data.get('peg', 'N/A')}" if fundamental_data.get('peg') is not None else "N/A",
        "ROE": f"{fundamental_data.get('roe', 'N/A')}%" if fundamental_data.get('roe') is not None else "N/A",
        "Debt/Equity": f"{fundamental_data.get('debt_equity', 'N/A')}" if fundamental_data.get('debt_equity') is not None else "N/A",
        "FCF": f"${fundamental_data.get('fcf', 'N/A')}" if fundamental_data.get('fcf') is not None else "N/A",
        "Institutional Ownership": f"{fundamental_data.get('held_percent_institutions', 'N/A')}" if fundamental_data.get('held_percent_institutions') is not None else "N/A",
        "Analyst Rating": fundamental_data.get('rating_label', 'N/A'),
        "Target Price": f"${fundamental_data.get('target_mean_price', 'N/A')}" if fundamental_data.get('target_mean_price') is not None else "N/A",
        "Dividend Yield": f"{fundamental_data.get('dividend_yield', 'N/A')}" if fundamental_data.get('dividend_yield') is not None else "N/A",
        "Beta": f"{fundamental_data.get('beta', 'N/A')}" if fundamental_data.get('beta') is not None else "N/A",
    }

    tech = {
        "RSI(14)": f"{technical_data.get('rsi_14', 'N/A')}",
        "MACD Histogram": f"{technical_data.get('macd_histogram', 'N/A')}",
        "Price vs SMA50": f"{technical_data.get('price_vs_sma50_pct', 'N/A')}%",
        "Bollinger Position": technical_data.get('bb_signal', 'N/A'),
        "Volume Ratio(10/50)": f"{technical_data.get('volume_ratio_10_50', 'N/A')}",
        "Trend": technical_data.get('trend_signal', 'N/A'),
        "SMA20": f"${technical_data.get('sma_20', 'N/A')}" if technical_data.get('sma_20') is not None else "N/A",
        "SMA50": f"${technical_data.get('sma_50', 'N/A')}" if technical_data.get('sma_50') is not None else "N/A",
    }

    data_block = (
        f"Ticker: {ticker}\n"
        f"Price: ${price}\n" if price else f"Ticker: {ticker}\n"
        f"Sector: {sector} | Industry: {industry} | Market Cap: {mcap_str}\n\n"
        f"-- Fundamentals --\n"
    )
    for k, v in metrics.items():
        data_block += f"  {k}: {v}\n"

    data_block += f"\n-- Technical Indicators --\n"
    for k, v in tech.items():
        data_block += f"  {k}: {v}\n"

    if technical_data.get("error") and technical_data["error"] == "insufficient_history":
        data_block += "\nNote: Limited price history available. Technical analysis may be unreliable.\n"

    try:
        resp = _create_completion(
            client,
            "stock_analysis",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Analyze this stock:\n\n{data_block}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=500,
        )
        text = resp.choices[0].message.content.strip()
        parsed = json.loads(text)
        agent_state.log_source_result(f"llm:{ticker}", True)
        return {
            "final_score": parsed.get("final_score"),
            "reasoning": parsed.get("reasoning", ""),
            "technical_summary": parsed.get("technical_summary", ""),
            "key_signal": parsed.get("key_signal", "neutral"),
        }
    except Exception as e:
        agent_state.log_source_result(f"llm:{ticker}", False, str(e))
        return {"error": str(e), "final_score": None, "reasoning": "", "technical_summary": "", "key_signal": "neutral"}


def analyze_stocks_batch(
    candidates: List[Dict[str, Any]],
    technical_data: Dict[str, Dict[str, Any]],
    lang: str = "zh_tw",
    progress_callback=None,
    llm_weight: float = 0.2,
) -> List[Dict[str, Any]]:
    if not candidates:
        return candidates

    total = len(candidates)
    for i, stock in enumerate(candidates):
        ticker = stock["ticker"]
        tech = technical_data.get(ticker, {})
        llm_result = analyze_stock(ticker, stock, tech, lang)
        stock["llm_score"] = llm_result.get("final_score")
        stock["llm_reasoning"] = llm_result.get("reasoning", "")
        stock["llm_technical_summary"] = llm_result.get("technical_summary", "")
        stock["llm_key_signal"] = llm_result.get("key_signal", "neutral")
        stock["llm_error"] = llm_result.get("error")

        if stock.get("llm_score") is not None:
            llm_w = llm_weight
            base_w = 1.0 - llm_w
            stock["total_score"] = round(
                stock.get("base_score", stock.get("growth_score", 0)) * base_w +
                stock["llm_score"] * llm_w,
                1
            )

        if progress_callback:
            progress_callback(i + 1, total)

    candidates.sort(key=lambda x: x.get("total_score", 0) or 0, reverse=True)
    return candidates


def _build_tech_data_block(ticker: str, technical_data: Dict[str, Any], current_price: float) -> str:
    tech = {
        "RSI(14)": f"{technical_data.get('rsi_14', 'N/A')}",
        "MACD Histogram": f"{technical_data.get('macd_histogram', 'N/A')}",
        "Price vs SMA50": f"{technical_data.get('price_vs_sma50_pct', 'N/A')}%",
        "Bollinger Position": technical_data.get('bb_signal', 'N/A'),
        "Volume Ratio(10/50)": f"{technical_data.get('volume_ratio_10_50', 'N/A')}",
        "Trend": technical_data.get('trend_signal', 'N/A'),
        "SMA20": f"${technical_data.get('sma_20', 'N/A')}" if technical_data.get('sma_20') is not None else "N/A",
        "SMA50": f"${technical_data.get('sma_50', 'N/A')}" if technical_data.get('sma_50') is not None else "N/A",
        "ATR(14)": f"{technical_data.get('atr_14', 'N/A')}" if technical_data.get('atr_14') is not None else "N/A",
    }
    block = f"Ticker: {ticker}\nCurrent Price: ${current_price:.2f}\n\n-- Technical Indicators --\n"
    for k, v in tech.items():
        block += f"  {k}: {v}\n"
    if technical_data.get("error") == "insufficient_history":
        block += "\nNote: Limited price history available.\n"
    return block


def _build_news_block(news_data: list) -> str:
    if not news_data:
        return "\n-- News --\nNo recent news.\n"
    block = "\n-- Recent News --\n"
    for n in news_data[:5]:
        title = n.get("title", "")
        sentiment = n.get("sentiment", "neutral")
        summary = n.get("summary", "")
        block += f"  [{sentiment}] {title}\n"
        if summary:
            block += f"    {summary[:200]}\n"
    return block


_PRICE_SYSTEM_PROMPT = """You are a professional technical analyst. Given technical indicators and recent news for a stock, suggest entry and exit price targets.

Respond in JSON format only:
{
  "buy_price": <number or null>,
  "sell_price": <number or null>,
  "stop_loss": <number or null>,
  "confidence": <0-100>,
  "reasoning": "<2-3 sentence analysis in Chinese>"
}

Guidelines:
- buy_price: suggested entry price (support level or current dip). null if now is a bad time to buy.
- sell_price: suggested take-profit target (resistance level or upside target). null if unclear.
- stop_loss: suggested stop-loss level. null if not applicable.
- confidence: how confident you are in this analysis (0-100).
- Consider RSI for overbought/oversold, Bollinger Bands for support/resistance, SMA lines for trend direction, volume for confirmation.
- If current price is above upper BB or RSI > 70, be cautious about buying.
- If price is near SMA50 support and RSI is neutral-oversold, it may be a good entry."""


_OPTIONS_SYSTEM_PROMPT = """You are a professional options strategist. Given technical indicators for a stock, suggest an options trading strategy.

Respond in JSON format only:
{
  "option_type": "<call|put|none>",
  "strike_price": <number or null>,
  "expiration": "<specific suggestion like 'next monthly expiry' or '45 days out' or null>",
  "key_support": <number or null>,
  "key_resistance": <number or null>,
  "reasoning": "<2-3 sentence analysis in Chinese>"
}

Guidelines:
- Suggest "call" when technicals are bullish (uptrend, MACD bullish, RSI 30-60)
- Suggest "put" when technicals are bearish (downtrend, MACD bearish, RSI > 65 or < 30)
- Suggest "none" when signals are mixed
- Strike price: slightly OTM for defined risk, or ATM for directional plays
- expiration: suggest a time frame (e.g. 'next monthly', '30-45 days')
- key_support and key_resistance: identify from Bollinger Bands and SMA levels"""


def suggest_price_targets(
    ticker: str,
    technical_data: Dict[str, Any],
    news_data: list,
    current_price: float,
    lang: str = "zh_tw",
) -> Dict[str, Any]:
    client = _get_client()
    if client is None:
        return {"error": "LLM not configured", "error_code": "not_configured"}

    data_block = _build_tech_data_block(ticker, technical_data, current_price)
    data_block += _build_news_block(news_data)

    try:
        resp = _create_completion(
            client,
            "price_targets",
            messages=[
                {"role": "system", "content": _PRICE_SYSTEM_PROMPT},
                {"role": "user", "content": f"Suggest entry and exit prices for:\n\n{data_block}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=600,
        )
        text = resp.choices[0].message.content.strip()
        parsed = json.loads(text)
        agent_state.log_source_result(f"llm_price:{ticker}", True)
        return {
            "buy_price": parsed.get("buy_price"),
            "sell_price": parsed.get("sell_price"),
            "stop_loss": parsed.get("stop_loss"),
            "confidence": parsed.get("confidence"),
            "reasoning": parsed.get("reasoning", ""),
        }
    except Exception as e:
        agent_state.log_source_result(f"llm_price:{ticker}", False, str(e))
        return {"error": str(e)}


def suggest_options_strategy(
    ticker: str,
    technical_data: Dict[str, Any],
    current_price: float,
    news_data: list = None,
    lang: str = "zh_tw",
) -> Dict[str, Any]:
    client = _get_client()
    if client is None:
        return {"error": "LLM not configured"}

    data_block = _build_tech_data_block(ticker, technical_data, current_price)
    if news_data:
        data_block += _build_news_block(news_data)

    try:
        resp = _create_completion(
            client,
            "options",
            messages=[
                {"role": "system", "content": _OPTIONS_SYSTEM_PROMPT},
                {"role": "user", "content": f"Suggest options strategy for:\n\n{data_block}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=600,
        )
        text = resp.choices[0].message.content.strip()
        parsed = json.loads(text)
        agent_state.log_source_result(f"llm_option:{ticker}", True)
        return {
            "option_type": parsed.get("option_type", "none"),
            "strike_price": parsed.get("strike_price"),
            "expiration": parsed.get("expiration"),
            "key_support": parsed.get("key_support"),
            "key_resistance": parsed.get("key_resistance"),
            "reasoning": parsed.get("reasoning", ""),
        }
    except Exception as e:
        agent_state.log_source_result(f"llm_option:{ticker}", False, str(e))
        return {"error": str(e)}


_LLM_HEALTH_TTL = 60
_llm_last_check: float = 0
_llm_healthy: bool = False


def check_llm_health() -> bool:
    global _llm_last_check, _llm_healthy
    now = _time.time()
    if now - _llm_last_check < _LLM_HEALTH_TTL:
        return _llm_healthy
    _llm_last_check = now
    try:
        client = _get_client()
        if client is None:
            _llm_healthy = False
            return False
        resp = _create_completion(
            client,
            "health",
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            temperature=0,
        )
        _llm_healthy = resp is not None
    except Exception:
        _llm_healthy = False
    return _llm_healthy


_STRATEGY_SYSTEM_PROMPT = """You are a professional trading strategist. Given fundamental data, technical indicators, and market context for a stock, recommend the best trading strategy.

Available strategies:
1. trend_following — Buy pullbacks in uptrend (SMA20>SMA50), exit on death cross
2. mean_reversion — Buy oversold bounces (RSI<30, BB lower), exit at resistance
3. breakout_momentum — Buy breakouts above BB upper with volume, trail stop
4. value_entry — Buy undervalued stocks (PEG<1, low PE/PB, strong ROE)
5. income_defensive — Buy high dividend, low beta, stable companies

Respond in JSON only:
{
  "top_strategy": "<strategy_id>",
  "confidence": {"technical": 0-100, "fundamental": 0-100, "setup_quality": 0-100},
  "entry": {"type": "limit|market|stop", "price": <float or null>, "reason": "..."},
  "targets": [{"price": <float>, "size_pct": <33|50|100>}],
  "stop_loss": {"price": <float or null>, "type": "fixed|trailing"},
  "risk_reward": <float or null>,
  "time_horizon": "<short description>",
  "scenario": {"best": "...", "base": "...", "worst": "..."},
  "decision_explanation": {
    "label": "bullish|bearish|avoid|watch|neutral",
    "why": "<clear explanation of why the deterministic model assigned this label>",
    "supporting_evidence": ["<evidence>", "<evidence>"],
    "counter_evidence": ["<risk or conflicting evidence>"],
    "change_conditions": ["<specific condition that would change the label>"],
    "view_explanations": {
      "short_term": "<why the deterministic short-term view is bullish, neutral, watch, or avoid>",
      "long_term": "<why the deterministic long-term view is bullish, neutral, watch, or avoid>"
    }
  },
  "reasoning": "<2-3 sentence analysis>"
}

The deterministic decision context is authoritative. Explain its label and evidence; do not replace its stance, action, entry, stop, targets, or position size. Distinguish avoid (explicit risk/data/trend blockers), watch (wait for confirmation), neutral (balanced evidence), bullish, and bearish. Write all explanation text in the requested output language."""


def suggest_trading_strategy(
    ticker: str,
    fundamental_data: Dict[str, Any],
    technical_data: Dict[str, Any],
    current_price: float,
    options_data: Optional[Dict[str, Any]] = None,
    news_data: Optional[list] = None,
    lang: str = "zh_tw",
    decision_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    global _llm_last_check, _llm_healthy
    client = _get_client()
    if client is None:
        return {"error": "LLM not configured"}
    now = _time.time()
    if _llm_last_check and now - _llm_last_check < _LLM_HEALTH_TTL and not _llm_healthy:
        return {"error": "LLM provider temporarily unavailable", "error_code": "provider_error"}

    data_block = _build_tech_data_block(ticker, technical_data, current_price)
    data_block += f"\n-- Fundamentals --\n"
    for k in ["pe_ratio", "forward_pe", "peg", "roe", "debt_equity", "revenue_growth",
              "eps_growth", "profit_margin", "dividend_yield", "beta", "market_cap",
              "target_mean_price"]:
        v = fundamental_data.get(k)
        if v is not None:
            data_block += f"  {k}: {v}\n"
    if options_data and "error" not in options_data:
        data_block += f"\n-- Options --\n"
        data_block += f"  Put/Call Ratio: {options_data.get('put_call_ratio')}\n"
        data_block += f"  ATM Strike: {options_data.get('atm_strike')}\n"
        data_block += f"  Max Call OI: {options_data.get('max_call_oi')}\n"
        data_block += f"  Max Put OI: {options_data.get('max_put_oi')}\n"
    if news_data:
        data_block += _build_news_block(news_data)
    language = {"zh_cn": "Simplified Chinese", "zh_tw": "Traditional Chinese", "en": "English"}.get(lang, "Traditional Chinese")
    if decision_context:
        data_block += f"\n-- Authoritative deterministic decision --\n{json.dumps(decision_context, ensure_ascii=False)}\n"
    data_block += f"\nOutput language: {language}\n"

    try:
        resp = _create_completion(
            client,
            "strategy",
            messages=[
                {"role": "system", "content": _STRATEGY_SYSTEM_PROMPT},
                {"role": "user", "content": f"Recommend strategy for:\n\n{data_block}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=800,
        )
        text = resp.choices[0].message.content.strip()
        parsed = json.loads(text)
        _llm_last_check = _time.time()
        _llm_healthy = True
        agent_state.log_source_result(f"llm_strategy:{ticker}", True)
        return {
            "top_strategy": parsed.get("top_strategy"),
            "confidence": parsed.get("confidence", {}),
            "entry": parsed.get("entry", {}),
            "targets": parsed.get("targets", []),
            "stop_loss": parsed.get("stop_loss", {}),
            "risk_reward": parsed.get("risk_reward"),
            "time_horizon": parsed.get("time_horizon"),
            "scenario": parsed.get("scenario", {}),
            "decision_explanation": _normalize_decision_explanation(
                parsed.get("decision_explanation"), decision_context
            ),
            "reasoning": parsed.get("reasoning", ""),
        }
    except Exception as e:
        _llm_last_check = _time.time()
        _llm_healthy = False
        agent_state.log_source_result(f"llm_strategy:{ticker}", False, str(e))
        return {"error": str(e), "error_code": _classify_strategy_error(e)}


def _classify_strategy_error(error: Any) -> str:
    message = str(error).lower()
    if "timed out" in message or "timeout" in message:
        return "timeout"
    if "401" in message or "unauthorized" in message or "authentication" in message:
        return "authentication"
    if "429" in message or "rate limit" in message:
        return "rate_limit"
    return "provider_error"


def _normalize_decision_explanation(
    value: Any, decision_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    context = decision_context or {}
    action = context.get("action")
    expected = "avoid" if action == "avoid_or_hedge" and context.get("avoid_reasons") else (
        "watch" if action == "watch" else context.get("stance", "neutral")
    )
    label = expected
    def items(key: str) -> list[str]:
        raw = value.get(key)
        return [str(item)[:300] for item in raw[:4]] if isinstance(raw, list) else []
    return {
        "label": label,
        "why": str(value.get("why") or "")[:1200],
        "supporting_evidence": items("supporting_evidence"),
        "counter_evidence": items("counter_evidence"),
        "change_conditions": items("change_conditions"),
        "view_explanations": {
            key: str((value.get("view_explanations") or {}).get(key) or "")[:600]
            for key in ("short_term", "long_term")
        } if isinstance(value.get("view_explanations"), dict) else {},
    }


def analyze_news_impact(
    ticker: str,
    article: Dict[str, Any],
    earnings: Optional[Dict[str, Any]] = None,
    lang: str = "zh_tw",
) -> Dict[str, Any]:
    client = _get_client()
    if client is None:
        return {"error": "LLM not configured"}
    language = {"zh_cn": "Simplified Chinese", "zh_tw": "Traditional Chinese", "en": "English"}.get(lang, "Traditional Chinese")
    prompt = f"""Analyze how this news may affect {ticker}. Separate reported facts from inference. Do not predict a guaranteed price move.
Language: {language}
Title: {article.get('title', '')}
Summary: {article.get('summary', '')[:1200]}
Publisher: {article.get('publisher', '')}
Published: {article.get('published_at', '')}
Upcoming earnings context: {json.dumps(earnings or {}, ensure_ascii=False)}

Return JSON only:
{{
  "direction": "positive|neutral|negative|mixed",
  "magnitude": "low|medium|high",
  "horizon": "intraday|short_term|long_term",
  "event_type": "earnings|guidance|product|analyst_action|regulatory|litigation|m_and_a|management|macro|other",
  "thesis": "concise analysis",
  "key_risks": ["..."],
  "key_catalysts": ["..."],
  "confidence": <integer 0-100>
}}"""
    try:
        response = _create_completion(
            client,
            "news_impact",
            messages=[
                {"role": "system", "content": "You are a cautious equity news analyst. Return valid JSON and never invent facts."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=600,
        )
        parsed = json.loads(response.choices[0].message.content.strip())
        return _normalize_news_impact(parsed)
    except Exception as exc:
        return {"error": str(exc)}


def _normalize_news_impact(value: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {
        "direction": {"positive", "neutral", "negative", "mixed"},
        "magnitude": {"low", "medium", "high"},
        "horizon": {"intraday", "short_term", "long_term"},
        "event_type": {"earnings", "guidance", "product", "analyst_action", "regulatory", "litigation", "m_and_a", "management", "macro", "other"},
    }
    result = {}
    defaults = {"direction": "neutral", "magnitude": "low", "horizon": "short_term", "event_type": "other"}
    for key, choices in allowed.items():
        candidate = str(value.get(key, defaults[key])).lower()
        result[key] = candidate if candidate in choices else defaults[key]
    result["thesis"] = str(value.get("thesis", ""))[:800]
    result["key_risks"] = [str(item)[:300] for item in value.get("key_risks", [])[:3]] if isinstance(value.get("key_risks"), list) else []
    result["key_catalysts"] = [str(item)[:300] for item in value.get("key_catalysts", [])[:3]] if isinstance(value.get("key_catalysts"), list) else []
    try:
        result["confidence"] = max(0, min(100, int(value.get("confidence", 0))))
    except (TypeError, ValueError):
        result["confidence"] = 0
    return result


_SEC_SUMMARY_SYSTEM_PROMPT = """You are an expert financial analyst. Given the raw text from a company's SEC filing (10-K or 10-Q), produce an extremely concise summary in the user's specified language.

Rules:
- Output ONLY valid JSON with keys: "summary" (2-3 sentences max), "key_risks" (list of 1-2 bullet points), "key_positives" (list of 1-2 bullet points)
- Use the language the user requests (zh_cn, zh_tw, or en)
- Focus on material changes, revenue drivers, and risk factors
- Be direct and short, avoid boilerplate
- If the text is unreadable or empty, set summary to "N/A"
"""


def summarize_sec_filing(raw_text: str, ticker: str, lang: str = "zh_tw") -> Dict[str, Any]:
    if not raw_text or len(raw_text.strip()) < 50:
        return {"summary": "N/A", "key_risks": [], "key_positives": []}

    lang_name = {"zh_cn": "简体中文", "zh_tw": "繁體中文", "en": "English"}.get(lang, "繁體中文")
    truncated = raw_text[:4000]

    try:
        client = _get_client()
        if not client:
            raise ValueError("LLM not configured")
        data_block = (
            f"Ticker: {ticker}\n"
            f"Language: {lang_name}\n\n"
            f"Filing excerpt:\n{truncated}"
        )
        resp = _create_completion(
            client,
            "sec_summary",
            messages=[
                {"role": "system", "content": _SEC_SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": data_block},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=500,
        )
        text = resp.choices[0].message.content.strip()
        parsed = json.loads(text)
        agent_state.log_source_result(f"llm_sec_summary:{ticker}", True)
        return {
            "summary": parsed.get("summary", "N/A"),
            "key_risks": parsed.get("key_risks", []),
            "key_positives": parsed.get("key_positives", []),
        }
    except Exception as e:
        agent_state.log_source_result(f"llm_sec_summary:{ticker}", False, str(e))
        return {"summary": "N/A", "key_risks": [], "key_positives": [], "error": str(e)}
