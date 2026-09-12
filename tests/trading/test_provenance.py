"""Thin Slice B — provenance chips for Scan/Deep."""
from __future__ import annotations

from backend.api.provenance import row_provenance, scan_book_provenance, _vendor_from_price_source


def test_vendor_from_price_source():
    assert _vendor_from_price_source("yahoo_regular_market") == "yahoo"
    assert _vendor_from_price_source("polygon_agg") == "polygon"
    assert _vendor_from_price_source(None) == "yahoo"
    assert _vendor_from_price_source("unknown") == "yahoo"


def test_row_provenance_as_of_and_vendor():
    info = row_provenance(
        {
            "price_source": "yahoo_regular_market",
            "price_quote_time": "2026-09-12T15:00:00+00:00",
            "price_stale": False,
        },
        scan_ts="2026-09-12T16:00:00+00:00",
    )
    assert info["vendor"] == "yahoo"
    assert info["as_of"]
    assert "cache_age_hours" in info
    assert info["polygon_available"] in (True, False)


def test_scan_book_provenance():
    book = scan_book_provenance(scan_ts="2026-09-12T16:00:00+00:00", use_llm=True, ranking_count=74)
    assert book["as_of"]
    assert book["vendor"] == "yahoo"
    assert book["vendor_primary"] == "yahoo"
    assert book["llm_overlay"] is True
    assert book["ranking_count"] == 74
    assert "note" in book
