#!/usr/bin/env python3
"""
Build point-in-time monthly snapshots for data/historical_universe.json.

Purpose:
  Eliminate the survivorship-bias fallback where backtests use today's
  STOCK_UNIVERSE for every historical month.

Design:
  - Start from the current liquid universe in backend.utils.constants.
  - Apply known public-listing / inclusion dates so late IPOs are absent
    before they existed (RIVN, RKLB, ASTS, LUNR, etc.).
  - Optionally probe Polygon ticker overview when POLYGON_API_KEY is set
    to refine list_date (never required for offline builds).
  - Write ISO-date monthly snapshots under data/historical_universe.json.

Usage:
  python scripts/build_historical_universe.py
  python scripts/build_historical_universe.py --start 2021-01-01 --end 2026-09-01
  python scripts/build_historical_universe.py --use-polygon
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.utils.constants import STOCK_UNIVERSE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "historical_universe.json"

# Approximate first-trade / inclusion dates for names that post-date 2021-01.
# Tickers omitted here are treated as available from --start.
INCLUSION_DATES: Dict[str, str] = {
    "RIVN": "2021-11-10",
    "LI": "2020-07-30",
    "RKLB": "2021-08-25",
    "ASTS": "2021-04-06",
    "LUNR": "2023-02-14",
    "META": "2012-05-18",  # traded as FB earlier; keep for META ticker era clarity
}


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def month_starts(start: date, end: date) -> List[date]:
    """First calendar day of each month from start..end inclusive."""
    cursor = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    out: List[date] = []
    while cursor <= last:
        out.append(cursor)
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return out


def resolve_inclusion_dates(use_polygon: bool) -> Dict[str, date]:
    resolved: Dict[str, date] = {
        ticker: _parse_date(iso) for ticker, iso in INCLUSION_DATES.items()
    }
    if not use_polygon:
        return resolved

    try:
        from backend.agents import polygon_equity as poly
    except Exception as exc:  # pragma: no cover - optional dependency path
        logger.warning("Polygon equity module unavailable: %s", exc)
        return resolved

    if not poly.is_configured():
        logger.warning("POLYGON_API_KEY not set; using built-in inclusion dates only.")
        return resolved

    for row in STOCK_UNIVERSE:
        ticker = row["ticker"]
        list_date = poly.fetch_ticker_list_date(ticker)
        if list_date is None:
            continue
        prev = resolved.get(ticker)
        if prev is None or list_date > prev:
            resolved[ticker] = list_date
            logger.info("Polygon list_date %s → %s", ticker, list_date.isoformat())
    return resolved


def build_snapshots(
    start: date,
    end: date,
    inclusion: Dict[str, date],
) -> Dict[str, List[str]]:
    all_tickers = sorted({row["ticker"] for row in STOCK_UNIVERSE})
    snapshots: Dict[str, List[str]] = {}
    for month in month_starts(start, end):
        eligible = [
            ticker
            for ticker in all_tickers
            if inclusion.get(ticker, date(1900, 1, 1)) <= month
        ]
        snapshots[month.isoformat()] = eligible
    return snapshots


def write_universe(path: Path, snapshots: Dict[str, List[str]], meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": meta,
        "snapshots": snapshots,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    logger.info(
        "Wrote %d monthly snapshots (%d–%d tickers) → %s",
        len(snapshots),
        min(len(v) for v in snapshots.values()),
        max(len(v) for v in snapshots.values()),
        path,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build PIT historical universe snapshots")
    parser.add_argument("--start", default="2021-01-01", help="First snapshot month (YYYY-MM-DD)")
    parser.add_argument("--end", default=date.today().isoformat(), help="Last snapshot month")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path")
    parser.add_argument(
        "--use-polygon",
        action="store_true",
        help="Refine inclusion dates via Polygon ticker overview when configured",
    )
    args = parser.parse_args(argv)

    start = _parse_date(args.start)
    end = _parse_date(args.end)
    if end < start:
        logger.error("--end must be on or after --start")
        return 2

    inclusion = resolve_inclusion_dates(use_polygon=args.use_polygon)
    snapshots = build_snapshots(start, end, inclusion)
    if not snapshots:
        logger.error("No snapshots generated")
        return 1

    write_universe(
        Path(args.output),
        snapshots,
        meta={
            "builder": "scripts/build_historical_universe.py",
            "universe_size": len(STOCK_UNIVERSE),
            "inclusion_overrides": {k: v.isoformat() for k, v in sorted(inclusion.items())},
            "use_polygon": bool(args.use_polygon),
            "notes": (
                "Liquid research universe with IPO-aware monthly snapshots. "
                "Not a full CRSP/Compustat constituent file; still reduces "
                "naive today-universe survivorship for this desk's ticker set."
            ),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
