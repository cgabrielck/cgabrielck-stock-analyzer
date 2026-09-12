"""Background Scan / Deep Research jobs + on-disk cache for the paper worker."""
from __future__ import annotations

import json
import math
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.pathsetup import ensure_backend_on_path
from backend.utils.constants import DATA_DIR, STOCK_UNIVERSE

ensure_backend_on_path()

LAST_SCAN_PATH = Path(DATA_DIR) / "last_scan.json"
LAST_DEEP_PATH = Path(DATA_DIR) / "last_deep.json"
# One US trading session buffer (~Fri close → Mon open). Not wall-clock 24h.
STALE_AFTER_HOURS = float(os.getenv("SCAN_STALE_AFTER_HOURS", "72") or 72)

_lock = threading.Lock()
_jobs: Dict[str, Dict[str, Any]] = {}
_UNIVERSE_INDEX = {str(item.get("ticker") or "").upper(): item for item in STOCK_UNIVERSE}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Any = None) -> Any:
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _meta_for(ticker: Any) -> Dict[str, Any]:
    return _UNIVERSE_INDEX.get(str(ticker or "").upper(), {})


def _num(value: Any, ndigits: int = 2) -> Any:
    try:
        if value is None or value == "":
            return None
        return round(float(value), ndigits)
    except (TypeError, ValueError):
        return None


def sanitize(value: Any, depth: int = 0) -> Any:
    """Make research payloads JSON-safe (drop DataFrames, NaN, huge blobs)."""
    if depth > 8:
        return None
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, (list, tuple)):
        return [sanitize(v, depth + 1) for v in value[:200]]
    if isinstance(value, dict):
        out = {}
        for k, v in list(value.items())[:80]:
            key = str(k)
            if key.startswith("_") or key in ("data", "ohlcv", "history", "chart_data", "_debug_all_data"):
                continue
            out[key] = sanitize(v, depth + 1)
        return out
    # pandas / numpy objects
    try:
        import pandas as pd

        if isinstance(value, pd.DataFrame):
            return {"rows": len(value), "cols": list(value.columns)[:20]}
        if isinstance(value, pd.Series):
            return value.tail(5).tolist()
    except Exception:
        pass
    try:
        return float(value)
    except Exception:
        return str(value)[:500]


def map_lang(lang: str) -> str:
    lang = (lang or "en").strip().lower()
    if lang in ("zh-tw", "zh_tw", "zh-hant", "tw"):
        return "zh_tw"
    if lang in ("zh-cn", "zh_cn", "zh-hans", "cn"):
        return "zh_cn"
    return "en"


def _age_hours(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0)
    except Exception:
        return None


def scan_is_stale(payload: Optional[Dict[str, Any]] = None) -> bool:
    data = payload if payload is not None else _read_json(LAST_SCAN_PATH, {})
    if not data or not data.get("rankings"):
        return True
    age = _age_hours(data.get("ts"))
    return age is None or age > STALE_AFTER_HOURS


def list_jobs(kind: Optional[str] = None) -> List[Dict[str, Any]]:
    with _lock:
        rows = [dict(j) for j in _jobs.values()]
    if kind:
        rows = [j for j in rows if j.get("kind") == kind]
    rows.sort(key=lambda j: str(j.get("updated_at") or ""), reverse=True)
    return rows


def latest_job(kind: str) -> Optional[Dict[str, Any]]:
    rows = list_jobs(kind)
    return rows[0] if rows else None


def resolve_job(kind: str, job_id: str) -> Dict[str, Any]:
    """Return an in-memory job, or reconstruct done/error from the on-disk cache."""
    job = get_job(job_id)
    if job:
        return job
    latest = latest_scan() if kind == "scan" else latest_deep()
    if str(latest.get("job_id") or "") == str(job_id) and latest.get("available"):
        return {
            "id": job_id,
            "kind": kind,
            "status": "done",
            "pct": 100,
            "phase": "done",
            "eta_sec": 0,
            "progress": latest.get("ranking_count") or latest.get("universe_size") or 0,
            "total": latest.get("universe_size") or latest.get("ranking_count") or 0,
            "message": "Complete",
            "result": latest,
            "error": None,
        }
    return {
        "id": job_id,
        "kind": kind,
        "status": "error",
        "error": "job_lost",
        "message": "Job is no longer in memory. Showing the last saved result.",
        "result": latest if latest.get("available") else None,
    }


def _score_from_row(row: Optional[Dict[str, Any]]) -> Optional[float]:
    if not row:
        return None
    for key in ("risk_adjusted_score", "growth_score", "model_score"):
        value = row.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def get_fund_score(ticker: str) -> Dict[str, Any]:
    """Score lookup for the paper worker. Prefer risk_adjusted, then growth.

    Keeps the last *real* score when the Scan book is stale (as-of flag only).
    Defaults to 50 only when there is no scan book or the ticker is missing.
    """
    ticker = ticker.upper().strip()
    scan = _read_json(LAST_SCAN_PATH, {})
    scores = (scan.get("scores_by_ticker") or {})
    row = scores.get(ticker)
    has_book = bool(scan.get("rankings") or scores)
    stale = scan_is_stale(scan) if has_book else True
    top5 = {str(t).upper() for t in (scan.get("top5_tickers") or [])}

    if not has_book:
        return {
            "score": 50.0,
            "raw_score": None,
            "source": "default",
            "stale": True,
            "scan_ts": scan.get("ts"),
            "in_top5": False,
        }

    score_f = _score_from_row(row)
    if score_f is None:
        return {
            "score": 50.0,
            "raw_score": None,
            "source": "missing",
            "stale": stale,
            "scan_ts": scan.get("ts"),
            "in_top5": False,
        }

    return {
        "score": score_f,
        "raw_score": score_f,
        "source": "last_scan_stale" if stale else "last_scan",
        "stale": stale,
        "scan_ts": scan.get("ts"),
        "in_top5": False if stale else ticker in top5,
        "sentiment_score": row.get("sentiment_score"),
        "growth_score": row.get("growth_score"),
        "risk_adjusted_score": row.get("risk_adjusted_score"),
    }


def get_deep_trade_plan(ticker: str) -> Optional[Dict[str, Any]]:
    ticker = ticker.upper().strip()
    deep = _read_json(LAST_DEEP_PATH, {})
    reports = deep.get("reports") or {}
    report = reports.get(ticker)
    if not report or report.get("error"):
        return None
    plan = report.get("trade_plan") or {}
    if not plan:
        return None
    return {
        "ticker": ticker,
        "ts": deep.get("ts"),
        "stance": plan.get("stance"),
        "action": plan.get("action"),
        "entry_zone": plan.get("entry_zone"),
        "confirmation_price": plan.get("confirmation_price"),
        "stop_loss": plan.get("stop_loss"),
        "targets": plan.get("targets"),
        "short_score": (report.get("short_term") or {}).get("score"),
        "long_score": (report.get("long_term") or {}).get("score"),
    }


def latest_scan() -> Dict[str, Any]:
    data = _read_json(LAST_SCAN_PATH, {})
    recs = data.get("recommendations") or []
    ranks = data.get("rankings") or []
    if not data or not (recs or ranks):
        return {"available": False, "stale": True, "recommendations": [], "rankings": []}
    return {
        "available": True,
        "stale": scan_is_stale(data),
        "age_hours": _age_hours(data.get("ts")),
        **data,
    }


def latest_deep() -> Dict[str, Any]:
    data = _read_json(LAST_DEEP_PATH, {})
    if not data:
        return {"available": False, "reports": {}}
    return {"available": True, "age_hours": _age_hours(data.get("ts")), **data}


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def _update_job(job_id: str, **fields: Any) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.update(fields)
        job["updated_at"] = _now_iso()
        if job.get("pct") is not None and job.get("created_at") and job.get("status") == "running":
            eta = _eta_seconds(job.get("created_at"), float(job.get("pct") or 0))
            job["eta_sec"] = eta


def _eta_seconds(created_at: Optional[str], pct: float) -> Optional[int]:
    if not created_at or pct <= 0.5:
        return None
    try:
        started = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds()
        if elapsed < 1:
            return None
        remaining = elapsed * (100.0 - min(pct, 99.0)) / max(pct, 0.5)
        return max(0, int(remaining))
    except Exception:
        return None


def map_engine_progress(current: int, total: int, universe: int = 74) -> Dict[str, Any]:
    """Map fetch (N/74) vs LLM overlay (N/15) onto a stable 0–100% bar."""
    current = max(0, int(current or 0))
    total = max(1, int(total or universe))
    if total <= 20:
        pct = 58 + min(22, int(22 * current / total))
        return {
            "pct": pct,
            "phase": "llm",
            "progress": current,
            "total": universe,
            "message": f"AI overlay {current}/{total}",
        }
    pct = min(55, int(55 * current / total))
    return {
        "pct": pct,
        "phase": "fetch",
        "progress": current,
        "total": total,
        "message": f"Fetching {current}/{total}",
    }


def _slim_recommendation(rec: Dict[str, Any]) -> Dict[str, Any]:
    sentiment = rec.get("sentiment") or {}
    risk = rec.get("risk_metrics") or {}
    ticker = rec.get("ticker")
    meta = _meta_for(ticker)
    news = rec.get("news") or []
    headlines = []
    for item in news[:3]:
        if isinstance(item, dict):
            headlines.append(
                {
                    "title": item.get("title"),
                    "sentiment": item.get("sentiment"),
                    "publisher": item.get("publisher"),
                }
            )
    reasoning = rec.get("reasoning") or rec.get("llm_reasoning") or ""
    if isinstance(reasoning, str) and len(reasoning) > 900:
        reasoning = reasoning[:900]
    return sanitize(
        {
            "ticker": ticker,
            "name": meta.get("name_en") or rec.get("longName") or rec.get("name"),
            "name_zh": meta.get("name_tw") or meta.get("name_cn") or rec.get("name_cn") or rec.get("name"),
            "sector": rec.get("sector") or meta.get("sector"),
            "universe_tier": rec.get("universe_tier") or meta.get("universe_tier"),
            "price": _num(rec.get("price"), 4),
            "growth_score": _num(rec.get("growth_score"), 1),
            "model_score": _num(rec.get("total_score"), 1),
            "risk_adjusted_score": _num(rec.get("risk_adjusted_score"), 1),
            "technical_score": _num(rec.get("technical_score"), 1),
            "llm_score": _num(rec.get("llm_score"), 1),
            "llm_key_signal": rec.get("llm_key_signal"),
            "sentiment_score": _num(sentiment.get("composite_score"), 1),
            "sentiment_label": sentiment.get("label") or sentiment.get("tone") or sentiment.get("composite_label"),
            "sentiment_modifier_pct": _num(rec.get("sentiment_modifier_pct"), 1),
            "risk_level": risk.get("risk_level") or rec.get("risk_level"),
            "revenue_growth": _num(rec.get("revenue_growth"), 1),
            "eps_growth": _num(rec.get("eps_growth"), 1),
            "profit_margin": _num(rec.get("profit_margin"), 1),
            "peg": _num(rec.get("peg"), 2),
            "roe": _num(rec.get("roe"), 1),
            "pe_ratio": _num(rec.get("pe_ratio"), 2),
            "market_cap": rec.get("market_cap"),
            "beta": _num(rec.get("beta"), 2),
            "reasoning": reasoning,
            "news": headlines,
        }
    )


def _slim_ranking(row: Dict[str, Any]) -> Dict[str, Any]:
    ticker = row.get("ticker")
    meta = _meta_for(ticker)
    return sanitize(
        {
            "rank": row.get("rank"),
            "ticker": ticker,
            "name": meta.get("name_en") or row.get("name"),
            "name_zh": meta.get("name_tw") or meta.get("name_cn") or row.get("name"),
            "sector": row.get("sector") or meta.get("sector"),
            "price": _num(row.get("price"), 4),
            "growth_score": _num(row.get("growth_score"), 1),
            "model_score": _num(row.get("model_score"), 1),
            "risk_penalty": _num(row.get("risk_penalty"), 1),
            "risk_adjusted_score": _num(row.get("risk_adjusted_score"), 1),
            "risk_level": row.get("risk_level"),
            "timing_score": _num(row.get("timing_score") or row.get("technical_score"), 1),
            "sentiment_score": _num(row.get("sentiment_score"), 1),
            "llm_score": _num(row.get("llm_score"), 1),
            "llm_key_signal": row.get("technical_signal") or row.get("llm_key_signal"),
            "revenue_growth": _num(row.get("revenue_growth"), 1),
            "eps_growth": _num(row.get("eps_growth"), 1),
            "profit_margin": _num(row.get("profit_margin"), 1),
            "peg": _num(row.get("peg"), 2),
            "roe": _num(row.get("roe"), 1),
        }
    )


def _build_scores_index(rankings: List[Dict[str, Any]], recommendations: List[Dict[str, Any]]) -> Dict[str, Any]:
    scores: Dict[str, Any] = {}
    for row in rankings:
        t = str(row.get("ticker") or "").upper()
        if not t:
            continue
        scores[t] = {
            "growth_score": row.get("growth_score"),
            "model_score": row.get("model_score"),
            "risk_adjusted_score": row.get("risk_adjusted_score"),
            "sentiment_score": row.get("sentiment_score"),
            "timing_score": row.get("timing_score"),
        }
    for rec in recommendations:
        t = str(rec.get("ticker") or "").upper()
        if not t:
            continue
        scores.setdefault(t, {})
        for key in ("growth_score", "model_score", "risk_adjusted_score", "sentiment_score"):
            if rec.get(key) is not None:
                scores[t][key] = rec.get(key)
        if rec.get("total_score") is not None:
            scores[t]["model_score"] = rec.get("total_score")
    return scores


def _run_scan_job(job_id: str, lang: str, llm_weight: float, use_llm: bool, force_refresh: bool) -> None:
    try:
        _update_job(
            job_id,
            status="running",
            progress=0,
            total=len(STOCK_UNIVERSE),
            pct=1,
            phase="fetch",
            message="Fetching universe…",
        )
        ensure_backend_on_path()
        from agents.recommender import run_full_analysis

        def progress(current: int, total: int) -> None:
            mapped = map_engine_progress(current, total, len(STOCK_UNIVERSE))
            _update_job(job_id, **mapped)

        engine_lang = map_lang(lang)
        result = run_full_analysis(
            progress_callback=progress,
            selected_tickers=[s["ticker"] for s in STOCK_UNIVERSE],
            lang=engine_lang,
            llm_weight=llm_weight,
            force_refresh=force_refresh,
            use_llm_analysis=use_llm,
        )
        _update_job(job_id, pct=92, phase="score", message="Ranking, sentiment & risk gates…")
        rankings = [_slim_ranking(r) for r in (result.get("all_rankings") or []) if isinstance(r, dict)]
        recommendations = [_slim_recommendation(r) for r in (result.get("recommendations") or [])]
        regime = sanitize(result.get("market_regime") or {})
        top5 = [r.get("ticker") for r in recommendations if r.get("ticker")]
        scores_by_ticker = _build_scores_index(rankings, result.get("recommendations") or [])

        payload = {
            "ts": _now_iso(),
            "job_id": job_id,
            "lang": engine_lang,
            "universe_size": len(STOCK_UNIVERSE),
            "use_llm": bool(result.get("use_llm")),
            "market_regime": regime,
            "recommendations": recommendations,
            "top5_tickers": top5,
            "rankings": rankings,
            "scores_by_ticker": scores_by_ticker,
            "pick_count": len(recommendations),
            "ranking_count": len(rankings),
            "error": result.get("error"),
        }
        _write_json(LAST_SCAN_PATH, payload)
        # Also seed Cache fund_score_* for any legacy readers
        try:
            from backend.utils.cache import Cache

            cache = Cache()
            for ticker, row in scores_by_ticker.items():
                score = row.get("risk_adjusted_score") or row.get("growth_score") or row.get("model_score")
                if score is not None:
                    cache.set(f"fund_score_{ticker}", "fundamentals", float(score), ttl=86400)
        except Exception:
            pass

        result_view = {
            "ts": payload["ts"],
            "job_id": job_id,
            "market_regime": regime,
            "recommendations": recommendations,
            "rankings": rankings,
            "top5_tickers": top5,
            "scores_by_ticker": scores_by_ticker,
            "pick_count": len(recommendations),
            "ranking_count": len(rankings),
            "stale": False,
            "available": True,
        }
        _update_job(
            job_id,
            status="done",
            progress=payload["universe_size"],
            total=payload["universe_size"],
            pct=100,
            phase="done",
            eta_sec=0,
            message=f"Scan complete · {len(recommendations)} picks · {len(rankings)} ranked",
            result=result_view,
        )
    except Exception as exc:
        _update_job(job_id, status="error", message=str(exc), error=str(exc))


def start_scan(
    lang: str = "en",
    llm_weight: float = 0.2,
    use_llm: bool = True,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    with _lock:
        for job in _jobs.values():
            if job.get("kind") == "scan" and job.get("status") in ("queued", "running"):
                return dict(job)
        job_id = str(uuid.uuid4())
        job = {
            "id": job_id,
            "kind": "scan",
            "status": "queued",
            "progress": 0,
            "total": len(STOCK_UNIVERSE),
            "pct": 0,
            "phase": "queued",
            "eta_sec": None,
            "message": "Queued",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "result": None,
            "error": None,
        }
        _jobs[job_id] = job

    thread = threading.Thread(
        target=_run_scan_job,
        args=(job_id, lang, llm_weight, use_llm, force_refresh),
        daemon=True,
        name=f"scan-{job_id[:8]}",
    )
    thread.start()
    return get_job(job_id) or job


def _slim_deep_report(report: Dict[str, Any]) -> Dict[str, Any]:
    technical = report.get("technical") or {}
    slim_tech = {
        "price": technical.get("price"),
        "technical_score": technical.get("technical_score"),
        "rsi_14": technical.get("rsi_14"),
        "atr_14": technical.get("atr_14"),
        "signal_pillars": technical.get("signal_pillars"),
    }
    return sanitize(
        {
            "ticker": report.get("ticker"),
            "error": report.get("error"),
            "stage": report.get("stage"),
            "schema_version": report.get("schema_version"),
            "short_term": report.get("short_term"),
            "long_term": report.get("long_term"),
            "avoid": report.get("avoid"),
            "trade_plan": report.get("trade_plan"),
            "strategy": report.get("strategy"),
            "news": (report.get("news") or [])[:5],
            "sec_evidence": {
                "available": (report.get("sec_evidence") or {}).get("available"),
                "summary": (report.get("sec_evidence") or {}).get("summary")
                or (report.get("sec_evidence") or {}).get("llm_summary"),
                "form": (report.get("sec_evidence") or {}).get("form"),
            },
            "options_plan": report.get("options_plan"),
            "session_ranges": report.get("session_ranges"),
            "quant_score": report.get("quant_score"),
            "risk_adjusted_score": report.get("risk_adjusted_score"),
            "technical": slim_tech,
            "enrichment_errors": report.get("enrichment_errors"),
        }
    )


def _run_deep_job(job_id: str, tickers: List[str], lang: str, force_refresh: bool) -> None:
    try:
        _update_job(
            job_id,
            status="running",
            progress=0,
            total=len(tickers),
            pct=3,
            phase="deep",
            message="Deep research started",
        )
        ensure_backend_on_path()
        from agents.deep_research import analyze_tickers

        engine_lang = map_lang(lang)
        completed = {"n": 0}

        def progress(event: Dict[str, Any]) -> None:
            if event.get("stage") == "ticker" and event.get("state") in ("completed", "failed"):
                completed["n"] += 1
                pct = min(99, int(100 * completed["n"] / max(len(tickers), 1)))
                _update_job(
                    job_id,
                    progress=completed["n"],
                    total=len(tickers),
                    pct=pct,
                    phase="deep",
                    message=f"Deep {completed['n']}/{len(tickers)} · {event.get('ticker') or ''}",
                )
            elif event.get("ticker") and event.get("stage"):
                pct = min(95, int(100 * completed["n"] / max(len(tickers), 1)))
                _update_job(
                    job_id,
                    pct=max(pct, 5),
                    phase=str(event.get("stage")),
                    message=f"{event.get('ticker')} · {event.get('stage')} · {event.get('state')}",
                )

        raw = analyze_tickers(
            tickers,
            lang=engine_lang,
            force_refresh=force_refresh,
            progress_callback=progress,
        )
        reports = {t: _slim_deep_report(raw.get(t) or {"ticker": t, "error": "missing"}) for t in tickers}
        # Merge with previous deep cache so older names remain available
        previous = _read_json(LAST_DEEP_PATH, {})
        merged = dict(previous.get("reports") or {})
        merged.update(reports)
        payload = {
            "ts": _now_iso(),
            "job_id": job_id,
            "lang": engine_lang,
            "tickers": tickers,
            "reports": merged,
            "latest_batch": tickers,
        }
        _write_json(LAST_DEEP_PATH, payload)
        _update_job(
            job_id,
            status="done",
            progress=len(tickers),
            total=len(tickers),
            pct=100,
            phase="done",
            eta_sec=0,
            message="Deep research complete",
            result={
                "ts": payload["ts"],
                "tickers": tickers,
                "reports": {t: reports[t] for t in tickers},
                "available": True,
            },
        )
    except Exception as exc:
        _update_job(job_id, status="error", message=str(exc), error=str(exc))


def start_deep(tickers: List[str], lang: str = "en", force_refresh: bool = False) -> Dict[str, Any]:
    clean: List[str] = []
    for t in tickers:
        sym = str(t).upper().strip()
        if sym and sym not in clean:
            clean.append(sym)
    if not clean:
        raise ValueError("Select at least one ticker")
    if len(clean) > 5:
        raise ValueError("Deep research supports at most 5 tickers at once")

    with _lock:
        for job in _jobs.values():
            if job.get("kind") == "deep" and job.get("status") in ("queued", "running"):
                return dict(job)
        job_id = str(uuid.uuid4())
        job = {
            "id": job_id,
            "kind": "deep",
            "status": "queued",
            "progress": 0,
            "total": len(clean),
            "pct": 0,
            "phase": "queued",
            "eta_sec": None,
            "message": "Queued",
            "tickers": clean,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "result": None,
            "error": None,
        }
        _jobs[job_id] = job

    thread = threading.Thread(
        target=_run_deep_job,
        args=(job_id, clean, lang, force_refresh),
        daemon=True,
        name=f"deep-{job_id[:8]}",
    )
    thread.start()
    return get_job(job_id) or job


def research_bundle(symbol: str) -> Dict[str, Any]:
    """Combine last scan + last deep + optional Yahoo quote for one symbol."""
    sym = symbol.upper().strip()
    scan = latest_scan()
    deep = latest_deep()
    scores = (scan.get("scores_by_ticker") or {}).get(sym) or {}
    ranking = next((r for r in (scan.get("rankings") or []) if str(r.get("ticker")).upper() == sym), None)
    pick = next((r for r in (scan.get("recommendations") or []) if str(r.get("ticker")).upper() == sym), None)
    report = (deep.get("reports") or {}).get(sym)
    yahoo: Dict[str, Any] = {}
    try:
        import yfinance as yf

        info = yf.Ticker(sym).info or {}
        yahoo = {
            "name": info.get("shortName") or info.get("longName") or sym,
            "sector": info.get("sector"),
            "price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "market_cap": info.get("marketCap"),
            "summary": (info.get("longBusinessSummary") or "")[:600],
        }
    except Exception as exc:
        yahoo = {"error": str(exc)}

    meta = next((s for s in STOCK_UNIVERSE if s["ticker"] == sym), {})
    return {
        "symbol": sym,
        "name": (pick or {}).get("name") or yahoo.get("name") or meta.get("name_en") or sym,
        "sector": (pick or {}).get("sector") or yahoo.get("sector") or meta.get("sector"),
        "price": (report or {}).get("technical", {}).get("price")
        or (pick or {}).get("price")
        or yahoo.get("price"),
        "fund_score": scores.get("growth_score") or scores.get("model_score"),
        "risk_adjusted_score": scores.get("risk_adjusted_score"),
        "sentiment_score": scores.get("sentiment_score"),
        "llm_score": (ranking or {}).get("llm_score"),
        "in_top5": sym in {str(t).upper() for t in (scan.get("top5_tickers") or [])},
        "scan_stale": scan.get("stale", True),
        "scan_ts": scan.get("ts"),
        "ranking": ranking,
        "recommendation": pick,
        "deep": report,
        "trade_plan": (report or {}).get("trade_plan") if report else None,
        "yahoo": yahoo,
        "note": None
        if scores
        else "No scan cache yet — run Scan all on the Scan page for scores.",
    }
