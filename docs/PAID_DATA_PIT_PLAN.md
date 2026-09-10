# Paid Data & PIT Universe Plan

Last updated: 2026-09-10

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
| Worker prefer-Polygon OHLCV | `backend/trading/engine/worker.py::_fetch_ohlcv` |
| Env knobs | `.env.example` (`POLYGON_*`, `ORDER_STORE_*`) |

## Operator steps

```bash
# Offline IPO-aware snapshots (no API key required)
PYTHONPATH=backend:. python3 scripts/build_historical_universe.py

# Refine list dates when POLYGON_API_KEY is set
PYTHONPATH=backend:. python3 scripts/build_historical_universe.py --use-polygon
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

## Acceptance

- [x] `HistoricalUniverse` loads repo `data/historical_universe.json` without fallback.
- [x] Backtests can report `historical_available: true`.
- [x] Worker attempts Polygon bars before Yahoo when configured.
