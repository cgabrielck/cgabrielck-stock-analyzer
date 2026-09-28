"""Advisory Bull/Bear/Risk debate — research draft only. Never places orders.

Steals TradingAgents *roles*, not the full repo. Uses a simple sequential
graph; LangGraph is optional when LAB_ADVISORY=1 and the package is installed.

Desk product path (`product=True`) always emits a rule-based committee so Deep
looks like a memo without requiring the lab flag. LLM enrichment is cost-capped
and still never calls OrderManager / worker buys.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.lab.flags import advisory_enabled, advisory_llm_max_calls, advisory_llm_max_tokens
from backend.lab.schemas import AdvisoryRoleOut
from backend.utils.constants import DATA_DIR

logger = logging.getLogger(__name__)

_METRICS_PATH = Path(DATA_DIR) / "lab_advisory_metrics.json"
_BUDGET_PATH = Path(DATA_DIR) / "lab_advisory_budget.json"

# Tests may replace this callable. Production uses _default_llm_committee.
_call_llm_committee = None  # type: ignore[assignment]


def _save_metrics(payload: Dict[str, Any]) -> None:
    try:
        _METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _METRICS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("advisory metrics write failed: %s", exc)


def advisory_metrics() -> Dict[str, Any]:
    if not _METRICS_PATH.exists():
        return {}
    try:
        return json.loads(_METRICS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def advisory_budget() -> Dict[str, Any]:
    max_calls = advisory_llm_max_calls()
    max_tokens = advisory_llm_max_tokens()
    payload = {"day": _utc_day(), "calls": 0, "tokens": 0, "max_calls": max_calls, "max_tokens": max_tokens}
    if _BUDGET_PATH.exists():
        try:
            stored = json.loads(_BUDGET_PATH.read_text(encoding="utf-8"))
            if stored.get("day") == payload["day"]:
                payload["calls"] = int(stored.get("calls") or 0)
                payload["tokens"] = int(stored.get("tokens") or 0)
        except Exception:
            pass
    payload["remaining_calls"] = max(0, max_calls - int(payload["calls"]))
    payload["llm_allowed"] = advisory_enabled() and payload["remaining_calls"] > 0
    return payload


def _persist_budget(payload: Dict[str, Any]) -> None:
    try:
        _BUDGET_PATH.parent.mkdir(parents=True, exist_ok=True)
        _BUDGET_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("advisory budget write failed: %s", exc)


def _consume_llm_budget(tokens: int = 0) -> bool:
    snap = advisory_budget()
    if snap["remaining_calls"] <= 0:
        return False
    snap["calls"] = int(snap["calls"]) + 1
    snap["tokens"] = int(snap["tokens"]) + max(0, int(tokens))
    snap["remaining_calls"] = max(0, snap["max_calls"] - snap["calls"])
    snap["llm_allowed"] = advisory_enabled() and snap["remaining_calls"] > 0
    _persist_budget(snap)
    return True


def _rule_role(ticker: str, role: str, fund_score: float, context: str) -> AdvisoryRoleOut:
    """Deterministic offline roles so tests never need LLM/LangGraph."""
    points: List[str] = []
    if role == "bull":
        thesis = f"{ticker}: 建設性論點 — 品質／成長分數 {fund_score:.0f}。"
        points = ["若技術面確認，Stage-2／趨勢模板才有資格", "基本面分數僅支持觀察名單，不是下單"]
        conf = min(0.85, 0.4 + fund_score / 200)
    elif role == "bear":
        thesis = f"{ticker}: 風險論點 — 估值／市場狀態／流動性可推翻多頭。"
        points = ["強多頭裡現金拖累策略可能輸 SPY", "委員會輸出不得送單"]
        conf = 0.55
    else:
        thesis = f"{ticker}: 風控官 — 半凱利、授權範圍、急停綁定所有路徑。"
        points = [
            "Advisory 不得呼叫 OrderManager",
            "突破硬閘（RS／MACD／ATR）不受辯論改寫",
        ]
        conf = 0.9
    if context:
        points.append(context[:160])
    return AdvisoryRoleOut(
        role=role,  # type: ignore[arg-type]
        ticker=ticker,
        thesis=thesis,
        confidence=conf,
        key_points=points,
        places_order=False,
    )


def _coerce_roles(ticker: str, raw_roles: Any) -> List[AdvisoryRoleOut]:
    out: List[AdvisoryRoleOut] = []
    if not isinstance(raw_roles, list):
        return out
    for item in raw_roles:
        if not isinstance(item, dict):
            continue
        try:
            payload = dict(item)
            payload["ticker"] = ticker
            payload["places_order"] = False
            payload["role"] = str(payload.get("role") or "risk").lower()
            if payload["role"] not in ("bull", "bear", "risk"):
                continue
            out.append(AdvisoryRoleOut.model_validate(payload))
        except Exception:
            continue
    return out


def _default_llm_committee(
    ticker: str,
    *,
    fund_score: float,
    context: str,
) -> Optional[List[AdvisoryRoleOut]]:
    """One JSON completion for three roles. Cost-capped by caller."""
    try:
        from agents.llm_agent import _create_completion, _get_client
    except Exception:
        try:
            from backend.agents.llm_agent import _create_completion, _get_client
        except Exception:
            return None
    client = _get_client()
    if client is None:
        return None
    max_tokens = advisory_llm_max_tokens()
    prompt = (
        f"You are a research committee for {ticker}. Fund score={fund_score:.0f}.\n"
        f"Context (may be empty): {context[:800]}\n"
        "Return JSON only: {\"roles\":[{\"role\":\"bull|bear|risk\",\"thesis\":\"...\", "
        "\"confidence\":0-1,\"key_points\":[\"...\"]}]}. "
        "places_order is always false. Do not recommend submitting a broker order. "
        "Write thesis/key_points in Traditional Chinese."
    )
    try:
        response = _create_completion(
            client,
            "scan",
            messages=[
                {
                    "role": "system",
                    "content": "Research memo only. Never place or request orders.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=0,
        )
        text = (response.choices[0].message.content or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        data = json.loads(text)
        roles = _coerce_roles(ticker, data.get("roles") if isinstance(data, dict) else data)
        return roles or None
    except Exception as exc:
        logger.info("advisory LLM skipped: %s", exc)
        return None


def _maybe_llm_roles(
    ticker: str,
    *,
    fund_score: float,
    context: str,
) -> tuple[Optional[List[AdvisoryRoleOut]], Dict[str, Any]]:
    cost = advisory_budget()
    if not advisory_enabled():
        return None, {**cost, "llm_used": False, "reason": "flag_off"}
    if cost["remaining_calls"] <= 0:
        return None, {**cost, "llm_used": False, "reason": "budget"}
    fn = _call_llm_committee or _default_llm_committee
    roles = fn(ticker, fund_score=fund_score, context=context)
    tokens = advisory_llm_max_tokens() if roles else 0
    if roles:
        _consume_llm_budget(tokens=tokens)
        cost = advisory_budget()
        return roles, {**cost, "llm_used": True, "reason": "ok"}
    return None, {**cost, "llm_used": False, "reason": "llm_unavailable"}


def run_advisory_debate(
    ticker: str,
    *,
    fund_score: float = 50.0,
    scan_rank: Optional[int] = None,
    context: str = "",
    top_n_before: Optional[List[str]] = None,
    product: bool = False,
    use_llm: bool = False,
    record_metrics: bool = True,
) -> Dict[str, Any]:
    """Return bull/bear/risk memos. Does not mutate Scan top-N by default.

    When enabled, records whether a *suggested* reorder differs from input top-N
    for A/B metrics — worker still ignores this for entries.

    ``product=True`` (Desk / Deep) always emits rule-based roles even if
    LAB_ADVISORY=0. LLM only runs when the flag is on, ``use_llm`` is true, and
    the daily call budget remains.
    """
    ticker = (ticker or "").upper().strip()
    top_n_before = list(top_n_before or [])
    if not advisory_enabled() and not product:
        return {
            "enabled": False,
            "ticker": ticker,
            "roles": [],
            "top_n_before": top_n_before,
            "top_n_after_suggestion": top_n_before,
            "top_n_rewritten": False,
            "places_order": False,
            "engine": "off",
            "llm_used": False,
        }

    engine = "sequential_roles"
    try:
        import langgraph  # noqa: F401

        engine = "langgraph_available_sequential"  # steal roles; avoid full firm graph
    except Exception:
        pass

    llm_roles: Optional[List[AdvisoryRoleOut]] = None
    cost: Dict[str, Any] = advisory_budget()
    if use_llm:
        llm_roles, cost = _maybe_llm_roles(ticker, fund_score=fund_score, context=context)
        if llm_roles:
            engine = "llm_capped_json"

    roles = llm_roles or [
        _rule_role(ticker, "bull", fund_score, context),
        _rule_role(ticker, "bear", fund_score, context),
        _rule_role(ticker, "risk", fund_score, context),
    ]
    # Hard rule: pydantic Literal[False] plus explicit overwrite.
    dumped = []
    for r in roles:
        row = r.model_dump()
        row["places_order"] = False
        dumped.append(row)

    suggested = list(top_n_before)
    bull = next((r for r in roles if r.role == "bull"), roles[0])
    if ticker and bull.confidence >= 0.7:
        suggested = [ticker] + [t for t in suggested if t != ticker]
    rewritten = suggested != top_n_before

    payload = {
        "enabled": True,
        "lab_flag": advisory_enabled(),
        "ticker": ticker,
        "scan_rank": scan_rank,
        "roles": dumped,
        "top_n_before": top_n_before,
        "top_n_after_suggestion": suggested,
        "top_n_rewritten": rewritten,
        "places_order": False,
        "engine": engine,
        "llm_used": bool(cost.get("llm_used")),
        "cost": {k: cost.get(k) for k in ("day", "calls", "remaining_calls", "max_calls", "reason", "llm_used")},
        "note": "Worker must ignore advisory reorder; RiskEngine/strategy gates unchanged.",
        "disclaimer_zh": "研究備忘錄，不是下單。LLM／委員會永不送單。",
    }
    if record_metrics:
        hist = advisory_metrics()
        hist["runs"] = int(hist.get("runs") or 0) + 1
        hist["rewrites"] = int(hist.get("rewrites") or 0) + (1 if rewritten else 0)
        hist["last"] = {
            "ticker": ticker,
            "rewritten": rewritten,
            "engine": engine,
            "places_order": False,
            "llm_used": payload["llm_used"],
        }
        _save_metrics(hist)
    return payload


def compose_research_memo(report: Dict[str, Any], debate: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Deep-five memo: thesis, risks, levels, sources. Never an order."""
    ticker = str((report or {}).get("ticker") or (debate or {}).get("ticker") or "").upper()
    plan = (report or {}).get("trade_plan") or {}
    strat = (report or {}).get("strategy") or {}
    avoid = ((report or {}).get("avoid") or {}).get("reasons") or []
    zone = plan.get("entry_zone") or {}
    roles = (debate or {}).get("roles") or []
    by_role = {str(r.get("role")): r for r in roles if isinstance(r, dict)}

    thesis: List[str] = []
    why = (
        (strat.get("decision_explanation") or {}).get("why")
        or strat.get("reasoning")
        or strat.get("summary")
        or strat.get("rationale")
        or plan.get("stance")
    )
    if why:
        thesis.append(str(why)[:280])
    bull = by_role.get("bull") or {}
    if bull.get("thesis"):
        thesis.append(str(bull["thesis"])[:240])
    for pt in (bull.get("key_points") or [])[:2]:
        thesis.append(str(pt)[:160])

    risks: List[str] = [str(x)[:200] for x in avoid[:3] if x]
    bear = by_role.get("bear") or {}
    risk_off = by_role.get("risk") or {}
    if bear.get("thesis"):
        risks.append(str(bear["thesis"])[:240])
    for pt in (bear.get("key_points") or [])[:1] + (risk_off.get("key_points") or [])[:1]:
        risks.append(str(pt)[:160])
    if not risks:
        risks = ["風控與授權關卡仍綁定；本備忘錄不能開倉。"]

    sources: List[str] = []
    prov = (report or {}).get("provenance") or {}
    if isinstance(prov, dict) and (prov.get("vendor") or prov.get("as_of")):
        sources.append(
            f"{prov.get('vendor') or 'unknown'} · as-of {str(prov.get('as_of') or '—')[:19]}"
        )
    sec = (report or {}).get("sec_evidence") or {}
    if sec.get("summary") or sec.get("form"):
        sources.append(f"SEC {sec.get('form') or ''} {(sec.get('summary') or '')[:120]}".strip())
    for item in ((report or {}).get("news") or [])[:2]:
        title = (item or {}).get("title") or (item or {}).get("headline")
        if title:
            sources.append(str(title)[:160])
    sources.append("Deep trade_plan 僅供研究；worker 仍走策略硬閘。")

    return {
        "ticker": ticker,
        "thesis": thesis[:5],
        "risks": risks[:6],
        "levels": {
            "entry_low": zone.get("low"),
            "entry_high": zone.get("high"),
            "stop_loss": plan.get("stop_loss"),
            "targets": plan.get("targets") or [],
            "confirmation_price": plan.get("confirmation_price"),
        },
        "sources": sources[:6],
        "places_order": False,
        "disclaimer_zh": "研究備忘錄（論點／風險／價位／來源），不是下單指令。",
        "disclaimer_en": "Research memo (thesis, risks, levels, sources) — not an order.",
    }


def attach_desk_advisory(
    report: Dict[str, Any],
    *,
    use_llm: bool = False,
    top_n_before: Optional[List[str]] = None,
    record_metrics: bool = True,
) -> Dict[str, Any]:
    """Attach committee + memo onto a Deep slim report. Worker ignores this."""
    ticker = str((report or {}).get("ticker") or "").upper()
    if not ticker or (report or {}).get("error"):
        return report
    fund = report.get("risk_adjusted_score")
    if fund is None:
        fund = report.get("quant_score")
    try:
        fund_score = float(fund if fund is not None else 50.0)
    except (TypeError, ValueError):
        fund_score = 50.0
    plan = report.get("trade_plan") or {}
    context_bits = [
        str(plan.get("stance") or ""),
        str(plan.get("action") or ""),
        str(((report.get("strategy") or {}).get("rationale") or "")[:180]),
    ]
    debate = run_advisory_debate(
        ticker,
        fund_score=fund_score,
        context=" · ".join(b for b in context_bits if b),
        top_n_before=list(top_n_before or []),
        product=True,
        use_llm=use_llm,
        record_metrics=record_metrics,
    )
    out = dict(report)
    out["advisory"] = {
        "roles": debate.get("roles") or [],
        "engine": debate.get("engine"),
        "llm_used": debate.get("llm_used"),
        "cost": debate.get("cost"),
        "places_order": False,
        "lab_flag": debate.get("lab_flag"),
        "disclaimer_zh": debate.get("disclaimer_zh"),
    }
    out["memo"] = compose_research_memo(out, debate)
    return out
