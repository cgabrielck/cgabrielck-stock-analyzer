"""Factor IC/IR lab (Qlib mechanisms, not the Qlib repo).

Wave 4 / Slice C / STRATEGY_ADOPTION P4 — first slice, not a years-long factory.

- Rank IC = Spearman (Pearson of average ranks) of factor vs next-period return
- IR = mean(IC) / std(IC) across rebalance dates
- Walk-forward long-only top-N vs SPY, net of costs (engine-style turnover)

Never places orders. If factors do not beat SPY net of costs, SKU B stays
risk-gated research-list automation, not alpha. Does not change default paper strategy.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from agents.score_contract import SHARED_FUND_SCORE_FIELD
from utils.constants import DATA_DIR

# Primary SKU B verdict uses the same field ranker + research_list share.
PRIMARY_FACTOR = SHARED_FUND_SCORE_FIELD
QUALITY_FACTORS = (
    "quality_score",
    "quality_roe_score",
    "quality_margin_score",
    "quality_leverage_score",
)
ALL_FACTORS = (
    PRIMARY_FACTOR,
    "growth_score",
    "growth_component_score",
    "value_component_score",
    *QUALITY_FACTORS,
)

DEFAULT_TOP_N = 5
DEFAULT_COST_BPS = 10.0
DEFAULT_INVESTED = 0.90
PLACES_ORDERS = False


def _finite_pairs(factor: Sequence[Any], forward: Sequence[Any]) -> Tuple[np.ndarray, np.ndarray]:
    xs: List[float] = []
    ys: List[float] = []
    for a, b in zip(factor, forward):
        try:
            fa = float(a)
            fb = float(b)
        except (TypeError, ValueError):
            continue
        if np.isfinite(fa) and np.isfinite(fb):
            xs.append(fa)
            ys.append(fb)
    return np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)


def spearman_rank_ic(
    factor: Sequence[Any],
    forward: Sequence[Any],
    *,
    min_n: int = 3,
) -> Optional[float]:
    """Spearman rank IC without scipy (Pearson correlation of average ranks)."""
    xs, ys = _finite_pairs(factor, forward)
    if xs.size < min_n:
        return None
    if np.unique(xs).size < 2 or np.unique(ys).size < 2:
        return None
    rx = pd.Series(xs).rank(method="average")
    ry = pd.Series(ys).rank(method="average")
    corr = float(rx.corr(ry, method="pearson"))
    if not np.isfinite(corr):
        return None
    return corr


def ic_ir_from_values(ics: Iterable[Optional[float]]) -> Dict[str, Any]:
    clean = [float(v) for v in ics if v is not None and np.isfinite(v)]
    n = len(clean)
    if n == 0:
        return {
            "ic_mean": None,
            "ic_std": None,
            "ir": None,
            "ic_hit_rate": None,
            "n_periods": 0,
        }
    arr = np.asarray(clean, dtype=float)
    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1)) if n > 1 else 0.0
    ir = None
    if n > 1 and std > 1e-12:
        ir = round(mean / std, 4)
    hit = float(np.mean(arr > 0))
    return {
        "ic_mean": round(mean, 4),
        "ic_std": round(std, 4) if n > 1 else 0.0,
        "ir": ir,
        "ic_hit_rate": round(hit, 4),
        "n_periods": n,
    }


def _date_key(value: Any) -> str:
    ts = pd.Timestamp(value)
    return ts.strftime("%Y-%m-%d")


def factor_ic_series(panel: pd.DataFrame, factor: str) -> Dict[str, Any]:
    """Per-date rank IC for one factor, then ICIR."""
    if panel is None or panel.empty or factor not in panel.columns:
        return {**ic_ir_from_values([]), "factor": factor, "series": []}
    series: List[Dict[str, Any]] = []
    ics: List[Optional[float]] = []
    for date, group in panel.groupby("date", sort=True):
        ic = spearman_rank_ic(group[factor].tolist(), group["forward_return"].tolist())
        ics.append(ic)
        series.append({
            "date": _date_key(date),
            "ic": None if ic is None else round(float(ic), 4),
            "n": int(len(group)),
        })
    summary = ic_ir_from_values(ics)
    summary["factor"] = factor
    summary["series"] = series
    return summary


def _turnover(current: Dict[str, float], target: Dict[str, float]) -> float:
    """Same L1/2 definition as ``backtesting.engine._calculate_turnover`` (no engine import)."""
    tickers = set(current) | set(target)
    stock_turnover = sum(abs(target.get(ticker, 0.0) - current.get(ticker, 0.0)) for ticker in tickers)
    current_cash = 1.0 - sum(current.values())
    target_cash = 1.0 - sum(target.values())
    return 0.5 * (stock_turnover + abs(target_cash - current_cash))


def _max_drawdown_pct(values: Sequence[float], initial: float) -> float:
    peak = initial
    max_dd = 0.0
    for value in values:
        if value > peak:
            peak = value
        if peak > 0:
            dd = (peak - value) / peak * 100
            if dd > max_dd:
                max_dd = dd
    return round(max_dd, 2)


def _sharpe_monthly(returns_pct: Sequence[float]) -> Optional[float]:
    arr = np.asarray(list(returns_pct), dtype=float)
    if arr.size < 2:
        return None
    std = float(np.std(arr, ddof=1))
    if std <= 0:
        return None
    return round(float(np.mean(arr) / std * math.sqrt(12)), 3)


def walk_forward_long_only(
    panel: pd.DataFrame,
    factor: str,
    *,
    top_n: int = DEFAULT_TOP_N,
    cost_bps: float = DEFAULT_COST_BPS,
    initial_capital: float = 10_000.0,
    invested: float = DEFAULT_INVESTED,
) -> Dict[str, Any]:
    """Equal-weight top-N each date vs SPY, net of costs. Research only — no orders."""
    empty = {
        "factor": factor,
        "total_return_pct": None,
        "spy_return_pct": None,
        "excess_vs_spy_net_pct": None,
        "sharpe_ratio": None,
        "max_drawdown_pct": None,
        "avg_turnover": None,
        "n_periods": 0,
        "beats_spy_net_of_costs": False,
        "transaction_cost_bps": cost_bps,
        "top_n": top_n,
        "places_orders": PLACES_ORDERS,
        "periods": [],
    }
    if panel is None or panel.empty or factor not in panel.columns:
        return empty

    portfolio = initial_capital
    spy_value = initial_capital
    prior_weights: Dict[str, float] = {}
    portfolio_values: List[float] = []
    period_rows: List[Dict[str, Any]] = []
    turnovers: List[float] = []
    net_returns_pct: List[float] = []

    for date, group in panel.groupby("date", sort=True):
        ranked = group.dropna(subset=[factor, "forward_return"]).sort_values(factor, ascending=False)
        picks = ranked.head(max(1, int(top_n)))
        if picks.empty:
            continue
        n = len(picks)
        weight = invested / n
        weights = {str(row["ticker"]): weight for _, row in picks.iterrows()}
        gross = float(sum(weights[str(row["ticker"])] * float(row["forward_return"]) for _, row in picks.iterrows()))
        spy_fwd = float(picks["spy_forward_return"].iloc[0]) if "spy_forward_return" in picks.columns else 0.0
        turnover = _turnover(prior_weights, weights)
        cost_rate = turnover * float(cost_bps) / 10000.0
        net = (1.0 - cost_rate) * (1.0 + gross) - 1.0
        portfolio *= 1.0 + net
        spy_value *= 1.0 + spy_fwd
        prior_weights = dict(weights)
        portfolio_values.append(portfolio)
        turnovers.append(turnover)
        net_returns_pct.append(net * 100)
        period_rows.append({
            "date": _date_key(date),
            "picks": [str(t) for t in picks["ticker"].tolist()],
            "gross_return_pct": round(gross * 100, 4),
            "net_return_pct": round(net * 100, 4),
            "spy_return_pct": round(spy_fwd * 100, 4),
            "turnover": round(turnover, 4),
            "cost_rate_bps": round(cost_rate * 10000, 4),
        })

    if not portfolio_values:
        return empty

    total_ret = (portfolio / initial_capital - 1.0) * 100
    spy_ret = (spy_value / initial_capital - 1.0) * 100
    excess = total_ret - spy_ret
    beats = bool(excess > 0)
    return {
        "factor": factor,
        "total_return_pct": round(total_ret, 2),
        "spy_return_pct": round(spy_ret, 2),
        "excess_vs_spy_net_pct": round(excess, 2),
        "sharpe_ratio": _sharpe_monthly(net_returns_pct),
        "max_drawdown_pct": _max_drawdown_pct(portfolio_values, initial_capital),
        "avg_turnover": round(float(np.mean(turnovers)), 4) if turnovers else None,
        "n_periods": len(period_rows),
        "beats_spy_net_of_costs": beats,
        "transaction_cost_bps": cost_bps,
        "top_n": top_n,
        "places_orders": PLACES_ORDERS,
        "periods": period_rows,
    }


def sku_b_positioning(*, beats_spy_net: bool, source: str) -> Dict[str, Any]:
    """If the factory loses to SPY net of costs, SKU B is not an alpha product."""
    if beats_spy_net:
        narrative = (
            "This research panel beat SPY net of stated costs. That is not a paper/live seal "
            "and does not change the default paper strategy. Do not market SKU B as alpha "
            "until Polygon + multi-month shadow seal (P3)."
        )
        positioning = "provisional_panel_edge"
    else:
        narrative = (
            "Factors did not beat SPY net of costs. SKU B remains risk-gated research-list "
            "automation (Scan top-N through RiskEngine), not an alpha product."
        )
        positioning = "research_list_automation"
    return {
        "sku_b_positioning": positioning,
        "sku_b_narrative": narrative,
        "alpha_claimed": False,
        "places_orders": PLACES_ORDERS,
        "default_paper_strategy_unchanged": True,
        "beats_spy_net_of_costs": beats_spy_net,
        "source": source,
        "shared_fund_score_field": PRIMARY_FACTOR,
    }


def make_demo_panel(
    *,
    n_months: int = 12,
    tickers: Sequence[str] = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF"),
    spy_monthly: float = 0.02,
    seed: int = 7,
    start: str = "2023-01-31",
) -> pd.DataFrame:
    """Offline fixture panel: quality ranks next-month returns; SPY drift is strong.

    Default SPY +2%/month so a modest quality long book loses net of costs —
    matching the honest SKU B narrative until a sealed edge exists.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start, periods=n_months, freq="ME")
    qualities = np.linspace(20.0, 95.0, len(tickers))
    rows: List[Dict[str, Any]] = []
    for i, date in enumerate(dates):
        order = np.roll(np.arange(len(tickers)), i)
        for j, ticker in enumerate(tickers):
            q = float(qualities[order[j]])
            fwd = (q - 50.0) / 50.0 * 0.03 + float(rng.normal(0, 0.018))
            growth_comp = float(np.clip(50 + (q - 50) * 0.3 + rng.normal(0, 4), 0, 100))
            roe = float(np.clip(q + rng.normal(0, 3), 0, 100))
            margin = float(np.clip(q + rng.normal(0, 4), 0, 100))
            leverage = float(np.clip(q + rng.normal(0, 5), 0, 100))
            growth_score = round(0.5 * q + 0.5 * growth_comp, 1)
            risk_adj = round(growth_score - 1.5, 1)
            rows.append({
                "date": date.strftime("%Y-%m-%d"),
                "ticker": ticker,
                "quality_score": round(q, 1),
                "quality_roe_score": round(roe, 1),
                "quality_margin_score": round(margin, 1),
                "quality_leverage_score": round(leverage, 1),
                "growth_component_score": round(growth_comp, 1),
                "growth_score": growth_score,
                "value_component_score": round(float(np.clip(40 + rng.normal(0, 8), 0, 100)), 1),
                PRIMARY_FACTOR: risk_adj,
                "forward_return": round(fwd, 6),
                "spy_forward_return": spy_monthly,
            })
    return pd.DataFrame(rows)


def load_panel(path: Path) -> pd.DataFrame:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    frame = pd.DataFrame(rows)
    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"])
    return frame


def dump_panel(panel: pd.DataFrame, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for _, row in panel.iterrows():
        item = dict(row)
        if hasattr(item.get("date"), "strftime"):
            item["date"] = pd.Timestamp(item["date"]).strftime("%Y-%m-%d")
        rows.append(item)
    path.write_text(json.dumps({"rows": rows}, indent=2), encoding="utf-8")
    return path


def build_performance_research(
    panel: pd.DataFrame,
    *,
    source: str = "fixture",
    top_n: int = DEFAULT_TOP_N,
    cost_bps: float = DEFAULT_COST_BPS,
    as_of: Optional[str] = None,
    factors: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Assemble dated IC/IR + walk-forward vs SPY net of costs."""
    generated = datetime.now(timezone.utc)
    as_of = as_of or generated.date().isoformat()
    factor_names = list(factors or ALL_FACTORS)
    factor_reports: Dict[str, Any] = {}
    for name in factor_names:
        if name not in panel.columns:
            continue
        ic = factor_ic_series(panel, name)
        wf = walk_forward_long_only(panel, name, top_n=top_n, cost_bps=cost_bps)
        factor_reports[name] = {
            "ic_mean": ic["ic_mean"],
            "ic_std": ic["ic_std"],
            "ir": ic["ir"],
            "ic_hit_rate": ic["ic_hit_rate"],
            "n_ic_periods": ic["n_periods"],
            "walk_forward": {k: v for k, v in wf.items() if k != "periods"},
            "ic_series": ic["series"],
        }

    primary = factor_reports.get(PRIMARY_FACTOR) or next(iter(factor_reports.values()), {})
    wf = primary.get("walk_forward") or {}
    beats = bool(wf.get("beats_spy_net_of_costs"))
    positioning = sku_b_positioning(beats_spy_net=beats, source=source)
    dates = sorted({_date_key(d) for d in panel["date"].tolist()}) if "date" in panel.columns else []
    return {
        "generated_at": generated.isoformat(),
        "as_of": as_of,
        "wave": 4,
        "slice": "C",
        "strategy_adoption": "P4",
        "source": source,
        "universe_size": int(panel["ticker"].nunique()) if "ticker" in panel.columns else 0,
        "n_rebalance_dates": len(dates),
        "start": dates[0] if dates else None,
        "end": dates[-1] if dates else None,
        "transaction_cost_bps": cost_bps,
        "top_n": top_n,
        "primary_factor": PRIMARY_FACTOR,
        "factors": factor_reports,
        "headline": {
            "sharpe_ratio": wf.get("sharpe_ratio"),
            "max_drawdown_pct": wf.get("max_drawdown_pct"),
            "turnover": wf.get("avg_turnover"),
            "spy_excess_net_pct": wf.get("excess_vs_spy_net_pct"),
            "total_return_pct": wf.get("total_return_pct"),
            "spy_return_pct": wf.get("spy_return_pct"),
            "beats_spy_net_of_costs": beats,
        },
        **positioning,
        "disclaimer": (
            "Research-only IC/IR slice. Never places orders. Fixture/offline panels are not a "
            "P3 Polygon+shadow seal. Do not change default paper strategy from this file."
        ),
    }


def write_performance_research(
    report: Dict[str, Any],
    *,
    data_dir: Optional[Path] = None,
) -> List[Path]:
    """Write ``performance_research.json`` plus a dated copy."""
    folder = Path(data_dir) if data_dir is not None else Path(DATA_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    as_of = str(report.get("as_of") or datetime.now(timezone.utc).date().isoformat())
    latest = folder / "performance_research.json"
    dated = folder / f"performance_research_{as_of}.json"
    payload = json.dumps(report, indent=2, default=str)
    latest.write_text(payload, encoding="utf-8")
    dated.write_text(payload, encoding="utf-8")
    return [latest, dated]


def run_fixture_research(
    *,
    data_dir: Optional[Path] = None,
    top_n: int = DEFAULT_TOP_N,
    cost_bps: float = DEFAULT_COST_BPS,
    panel_path: Optional[Path] = None,
) -> Tuple[Dict[str, Any], List[Path]]:
    if panel_path:
        panel = load_panel(panel_path)
        source = f"panel:{panel_path}"
    else:
        panel = make_demo_panel()
        source = "fixture"
    report = build_performance_research(panel, source=source, top_n=top_n, cost_bps=cost_bps)
    paths = write_performance_research(report, data_dir=data_dir)
    return report, paths
