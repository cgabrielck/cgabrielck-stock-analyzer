# Paid Data & PIT Universe Plan

Last updated: 2026-09-16

**Procurement (what to buy and when):** see [`PROCUREMENT_GUIDE.md`](PROCUREMENT_GUIDE.md).
Phase 2 buys Polygon/Massive **stocks** daily bars (non-Advanced is enough for
day-bar strategies); do not start with dual Advanced SIP+OPRA.

## Wave 2 coding slice (STRATEGY P3)

This is the **coding** slice of P3: worker / Scan / backtest / shadow prefer
Polygon daily bars when a real `POLYGON_API_KEY` is set, and equity lag uses
the **US NYSE session calendar** (not 24h / BTC).

**Without a key:** Yahoo remains the labeled fallback. Do **not** change the
customer narrative (still Yahoo primary, still paper-only, still not live).
Buying the key is Phase 2 in [`PROCUREMENT_GUIDE.md`](PROCUREMENT_GUIDE.md);
Wave 2 does not require a paid subscription to land in the repo.

| Item | Behavior |
|------|----------|
| Vendor selector | `backend/utils/equity_ohlcv.py` — Polygon first, Yahoo labeled fallback |
| US session clock | `backend/utils/us_equity_calendar.py` — weekdays minus NYSE holidays |
| Provenance | Scan/Deep chips include `vendor` / `vendor_bars` / `fallback` |
| Calendar check | `scripts/check_paper_calendar.py` prints `polygon_configured=` |

## Goal

Replace Yahoo-only survivorship-biased history with:

1. Point-in-time monthly snapshots for this desk's liquid universe.
2. Optional Polygon/Massive daily bars for worker OHLCV when keyed.

## Delivered

| Item | Path |
|------|------|
| Snapshot builder | `scripts/build_historical_universe.py` |
| Generated snapshots | `data/historical_universe.json` |
| Polygon equity helper | `backend/agents/polygon_equity.py` |
| Shared OHLCV selector | `backend/utils/equity_ohlcv.py` |
| US equity session calendar | `backend/utils/us_equity_calendar.py` |
| Worker prefer-Polygon OHLCV | `backend/trading/engine/worker.py::_fetch_ohlcv` |
| Scan technicals prefer Polygon | `backend/agents/technical_analyzer.py` |
| Env knobs | `.env.example` (`POLYGON_*`, `ORDER_STORE_*`) |

## Operator steps

```bash
# Offline IPO-aware snapshots (no API key required)
PYTHONPATH=backend:. python3 scripts/build_historical_universe.py

# Refine list dates when POLYGON_API_KEY is set
PYTHONPATH=backend:. python3 scripts/build_historical_universe.py --use-polygon

# Seal clock + vendor flag (no paid call)
python scripts/check_paper_calendar.py
```

## Limitations (honest)

- Snapshots cover the **ALPHA//DESK research universe**, not the full CRSP
  investable set. This removes *today-universe* lookahead for late IPOs in
  this list; it is not institutional PIT membership data.
- Polygon Developer tiers may be delayed; treat delayed bars as research-grade
  unless the subscription is REAL-TIME / SIP.
- Cost guidance (order-of-magnitude, verify on vendor site): Polygon/Massive
  stocks + options realtime is a paid monthly subscription; Yahoo remains free
  fallback only.
- Multi-month paper/shadow **seal vs SPY net of costs** is still the remaining
  P3 ops slice after this coding path.

## Acceptance

- [x] `HistoricalUniverse` loads repo `data/historical_universe.json` without fallback.
- [x] Backtests can report `historical_available: true`.
- [x] Worker attempts Polygon bars before Yahoo when configured.
- [x] Scan technicals use the same vendor selector; provenance records OHLCV vendor.
- [x] Backtest/shadow iterate US equity sessions only (weekend/BTC bars dropped).
- [x] `scripts/check_paper_calendar.py` prints `polygon_configured`.
