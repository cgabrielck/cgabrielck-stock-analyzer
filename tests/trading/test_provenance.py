"""Thin Slice B — provenance chips for Scan/Deep."""
from __future__ import annotations

from backend.api.provenance import row_provenance, scan_book_provenance, _vendor_from_price_source


def test_vendor_from_price_source():
    assert _vendor_from_price_source("yahoo_regular_market") == "yahoo"
    assert _vendor_from_price_source("polygon_agg") == "polygon"
    assert _vendor_from_price_source("yfinance_history") == "yahoo"
    assert _vendor_from_price_source("polygon_aggs") == "polygon"
    assert _vendor_from_price_source(None) == "yahoo"
    assert _vendor_from_price_source("unknown") == "yahoo"


def test_row_provenance_as_of_and_vendor(monkeypatch):
    monkeypatch.setattr("backend.api.provenance.polygon_configured", lambda: False)
    info = row_provenance(
        {
            "price_source": "yahoo_regular_market",
            "price_quote_time": "2026-09-12T15:00:00+00:00",
            "price_stale": False,
        },
        scan_ts="2026-09-12T16:00:00+00:00",
    )
    assert info["vendor"] == "yahoo"
    assert info["vendor_primary"] == "yahoo"
    assert info["fallback"] is False
    assert info["as_of"]
    assert "cache_age_hours" in info
    assert info["polygon_available"] is False
    assert info["calendar"] == "us_equity"


def test_row_provenance_polygon_bars_yahoo_quote(monkeypatch):
    monkeypatch.setattr("backend.api.provenance.polygon_configured", lambda: True)
    info = row_provenance(
        {
            "price_source": "yahoo_regular_market",
            "technical_source": "polygon_aggs",
            "bars_vendor": "polygon",
            "price_quote_time": "2026-09-12T15:00:00+00:00",
        }
    )
    assert info["vendor"] == "polygon"
    assert info["vendor_bars"] == "polygon"
    assert info["vendor_quote"] == "yahoo"
    assert info["vendor_primary"] == "polygon"
    assert info["fallback"] is False


def test_row_provenance_yahoo_fallback_when_polygon_keyed(monkeypatch):
    monkeypatch.setattr("backend.api.provenance.polygon_configured", lambda: True)
    info = row_provenance(
        {
            "price_source": "yahoo_regular_market",
            "technical_source": "yfinance_history",
            "bars_fallback": True,
        }
    )
    assert info["vendor"] == "yahoo"
    assert info["fallback"] is True
    assert info["vendor_primary"] == "polygon"


def test_scan_book_provenance_without_key(monkeypatch):
    monkeypatch.setattr("backend.api.provenance.polygon_configured", lambda: False)
    book = scan_book_provenance(scan_ts="2026-09-12T16:00:00+00:00", use_llm=True, ranking_count=74)
    assert book["as_of"]
    assert book["vendor"] == "yahoo"
    assert book["vendor_primary"] == "yahoo"
    assert book["vendor_bars"] == "yahoo"
    assert book["llm_overlay"] is True
    assert book["ranking_count"] == 74
    assert "Yahoo" in book["note"]


def test_scan_book_provenance_with_polygon_key(monkeypatch):
    monkeypatch.setattr("backend.api.provenance.polygon_configured", lambda: True)
    book = scan_book_provenance(scan_ts="2026-09-12T16:00:00+00:00", use_llm=False, ranking_count=10)
    assert book["vendor"] == "polygon"
    assert book["vendor_primary"] == "polygon"
    assert book["vendor_bars"] == "polygon"
    assert book["fallback_vendor"] == "yahoo"
    assert "fallback" in book["note"].lower()
