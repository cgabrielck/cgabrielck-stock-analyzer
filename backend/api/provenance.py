"""Provenance helpers for Scan / Deep payloads on the FastAPI desk."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional


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


def _vendor_from_price_source(source: Optional[str]) -> str:
    text = str(source or "").lower()
    if "polygon" in text or "massive" in text:
        return "polygon"
    if "alpha" in text:
        return "alpha_vantage"
    if (
        "yahoo" in text
        or "yfinance" in text
        or "history" in text
        or "regular" in text
        or "pre_" in text
        or "after" in text
    ):
        return "yahoo"
    # Missing/unknown on cached rows → Yahoo is product primary (not a fake score).
    if not text or text in ("unavailable", "unknown", "none"):
        return "yahoo"
    return text.split("_")[0] or "yahoo"


def polygon_configured() -> bool:
    """True only for a real key — placeholders do not count."""
    try:
        from backend.agents.polygon_equity import is_configured

        return bool(is_configured())
    except Exception:
        try:
            from agents.polygon_equity import is_configured  # type: ignore

            return bool(is_configured())
        except Exception:
            key = (os.getenv("POLYGON_API_KEY") or os.getenv("MASSIVE_API_KEY") or "").strip()
            if not key:
                return False
            low = key.lower()
            if any(token in low for token in ("replace-with", "your-polygon", "changeme")):
                return False
            return True


def row_provenance(row: Dict[str, Any], *, scan_ts: Optional[str] = None) -> Dict[str, Any]:
    """Per-ticker provenance chip fields for desk Scan/Deep cards."""
    price_source = row.get("price_source") or (row.get("technical") or {}).get("price_source")
    quote_time = row.get("price_quote_time") or (row.get("technical") or {}).get("price_quote_time")
    as_of = quote_time or scan_ts or row.get("fetched_at")
    bars_source = (
        row.get("technical_source")
        or row.get("bars_vendor")
        or row.get("bars_source")
        or (row.get("technical") or {}).get("technical_source")
        or (row.get("technical") or {}).get("bars_vendor")
    )
    quote_vendor = _vendor_from_price_source(price_source)
    bars_vendor = _vendor_from_price_source(bars_source) if bars_source else quote_vendor
    keyed = polygon_configured()
    vendor = bars_vendor if bars_source else quote_vendor
    age = _age_hours(as_of)
    stale = bool(row.get("price_stale"))
    if age is not None and age > 24:
        stale = True
    yahoo_fallback = vendor == "yahoo" and keyed
    return {
        "vendor": vendor,
        "vendor_bars": bars_vendor,
        "vendor_quote": quote_vendor,
        "vendor_primary": "polygon" if keyed else "yahoo",
        "price_source": price_source or vendor,
        "as_of": as_of,
        "cache_age_hours": round(age, 2) if age is not None else None,
        "stale": stale,
        "session": row.get("price_session") or (row.get("technical") or {}).get("price_session"),
        "market_state": row.get("price_market_state") or (row.get("technical") or {}).get("price_market_state"),
        "polygon_available": keyed,
        "fallback": yahoo_fallback or bool(row.get("bars_fallback")),
        "calendar": "us_equity",
    }


def scan_book_provenance(
    *,
    scan_ts: Optional[str],
    use_llm: bool,
    ranking_count: int,
) -> Dict[str, Any]:
    age = _age_hours(scan_ts)
    keyed = polygon_configured()
    primary = "polygon" if keyed else "yahoo"
    return {
        "vendor": primary,
        "vendor_primary": primary,
        "vendor_bars": primary,
        "fallback_vendor": "yahoo",
        "as_of": scan_ts,
        "cache_age_hours": round(age, 2) if age is not None else None,
        "llm_overlay": bool(use_llm),
        "ranking_count": ranking_count,
        "polygon_available": keyed,
        "calendar": "us_equity",
        "note": (
            "OHLCV prefers Polygon daily bars; Yahoo is the labeled fallback when Polygon misses. Quotes may still be Yahoo."
            if keyed
            else "Yahoo primary — set POLYGON_API_KEY for paid daily bars on the seal path. Without a key the customer narrative stays Yahoo fallback (not live)."
        ),
    }
