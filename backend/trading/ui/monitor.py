"""
Streamlit trading monitor — dashboard for shadow/paper trading activity.

Launch: streamlit run backend/trading/ui/monitor.py

Displays:
- Current positions (symbol, qty, entry price, current P&L)
- Recent order history (last 50 orders)
- Daily P&L chart (trailing 30 days)
- Kill switch status & manual trip/reset
- Mandate summary

Reads from shadow/paper OrderStore JSON files (read-only).
Kill switch control writes to data/KILL_SWITCH.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st
import pandas as pd

from backend.trading.storage import JSONOrderStore
from backend.trading.models import Order, OrderStatus, OrderSide
from backend.trading.safety import kill_switch
from backend.trading.safety.mandate import load_mandate
from backend.utils.constants import DATA_DIR

# ============================================================================
# Configuration
# ============================================================================
SHADOW_STORE_PATH = Path(DATA_DIR) / "trading" / "shadow_orders.json"
PAPER_STORE_PATH = Path(DATA_DIR) / "trading" / "paper_orders.json"
MANDATE_PATH = Path(DATA_DIR).parent / "config" / "mandate.json"


def _inject_terminal_css():
    """Match the Apple terminal theme from app.py."""
    st.markdown(
        """<style>
    :root {
        --bg: #070b12; --panel: #0d1420; --panel-2: #111b2a; --line: #1d2a3c;
        --line-hot: #2a405b; --text: #e8f0f8; --muted: #7e91a8; --faint: #4c6077;
        --cyan: #22d3c5; --cyan-soft: rgba(34,211,197,.11); --green: #34d399;
        --red: #fb7185; --amber: #fbbf24; --blue: #60a5fa;
        --sans: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        --mono: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
    }
    html, body, [class*="css"] { font-family: var(--sans); }
    .stApp { background: radial-gradient(circle at 75% -10%, #102239 0, transparent 35%), var(--bg); color: var(--text); }
    .main > div { padding: 1.4rem 2rem 3rem; }
    header[data-testid="stHeader"] { background: transparent; }
    #MainMenu, footer { visibility: hidden; }
    
    section[data-testid="stSidebar"] { background: #09101a; border-right: 1px solid var(--line); min-width: 260px; }
    section[data-testid="stSidebar"] > div { padding: 1rem .85rem; }
    .sidebar-section { color:var(--cyan); font-family:var(--mono); font-size:.65rem; font-weight:700; letter-spacing:.14em; text-transform:uppercase; margin:1rem 0 .35rem; padding-bottom:.35rem; border-bottom:1px solid var(--line); }
    
    .brand-kicker { color:var(--cyan); font-family:var(--mono); font-size:.66rem; font-weight:700; letter-spacing:.18em; text-transform:uppercase; margin-bottom:.3rem; }
    .app-title { font-size:2rem; line-height:1; font-weight:760; letter-spacing:-.045em; color:var(--text); margin:0; }
    .app-title span { color:var(--cyan); }
    
    .metric-card { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:1rem; margin-bottom:.85rem; }
    .metric-label { color:var(--muted); font-size:.72rem; text-transform:uppercase; letter-spacing:.08em; margin-bottom:.3rem; }
    .metric-value { font-size:1.65rem; font-weight:700; line-height:1; }
    .metric-value.green { color:var(--green); }
    .metric-value.red { color:var(--red); }
    .metric-delta { font-size:.8rem; color:var(--muted); margin-top:.25rem; }
    
    .status-badge { display:inline-block; padding:.25rem .7rem; border-radius:999px; font-size:.72rem; font-weight:700; letter-spacing:.06em; text-transform:uppercase; }
    .status-badge.active { background:rgba(52,211,153,.15); color:var(--green); border:1px solid rgba(52,211,153,.3); }
    .status-badge.halted { background:rgba(251,113,133,.15); color:var(--red); border:1px solid rgba(251,113,133,.3); }
    .status-badge.warning { background:rgba(251,191,36,.15); color:var(--amber); border:1px solid rgba(251,191,36,.3); }
    
    div[data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:6px; overflow:hidden; }
    div[data-testid="stDataFrame"] table { background:var(--panel); color:var(--text); }
    div[data-testid="stDataFrame"] thead th { background:var(--panel-2); color:var(--cyan); font-size:.7rem; text-transform:uppercase; letter-spacing:.08em; border-bottom:1px solid var(--line-hot); }
    div[data-testid="stDataFrame"] tbody td { border-color:var(--line); }
</style>""",
        unsafe_allow_html=True,
    )


def _format_timestamp(ts: Optional[datetime]) -> str:
    if not ts:
        return "—"
    try:
        return ts.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ts)


def _load_orders(mode: str) -> List[Order]:
    """Load orders from the selected mode's store."""
    path = SHADOW_STORE_PATH if mode == "shadow" else PAPER_STORE_PATH
    if not path.exists():
        return []
    store = JSONOrderStore(path)
    # get_recent_orders returns List[Order], sorted by created_at desc
    return store.get_recent_orders(limit=500)


def _compute_positions(orders: List[Order]) -> pd.DataFrame:
    """
    Aggregate filled orders into current positions.
    BUY adds, SELL subtracts. Returns a DataFrame with:
    symbol, quantity, avg_entry_price, total_cost.
    """
    positions: Dict[str, Dict[str, float]] = {}
    for order in orders:
        if order.status != OrderStatus.FILLED:
            continue
        symbol = order.symbol
        side = order.side
        qty = order.quantity
        price = order.filled_avg_price or order.limit_price or 0.0
        if symbol not in positions:
            positions[symbol] = {"quantity": 0.0, "total_cost": 0.0}
        if side == OrderSide.BUY:
            positions[symbol]["quantity"] += qty
            positions[symbol]["total_cost"] += qty * price
        elif side == OrderSide.SELL:
            # Proportional cost reduction
            if positions[symbol]["quantity"] > 0:
                cost_per_share = positions[symbol]["total_cost"] / positions[symbol]["quantity"]
                positions[symbol]["quantity"] -= qty
                positions[symbol]["total_cost"] -= qty * cost_per_share
            else:
                positions[symbol]["quantity"] -= qty  # short (unlikely in shadow mode)
    # Filter out closed positions
    active = {sym: pos for sym, pos in positions.items() if abs(pos["quantity"]) > 1e-6}
    if not active:
        return pd.DataFrame(columns=["Symbol", "Quantity", "Avg Entry", "Total Cost"])
    rows = []
    for sym, pos in active.items():
        qty = pos["quantity"]
        avg_price = pos["total_cost"] / qty if qty != 0 else 0.0
        rows.append({
            "Symbol": sym,
            "Quantity": qty,
            "Avg Entry": avg_price,
            "Total Cost": pos["total_cost"],
        })
    return pd.DataFrame(rows).sort_values("Symbol")


def _compute_daily_pnl(orders: List[Order], days: int = 30) -> pd.DataFrame:
    """
    Compute daily filled SELL notional over the trailing *days* as a rough
    activity proxy. (A full realized-P&L view needs cost basis from the BUY
    side; the PerformanceTracker handles that. Here we surface sell activity.)
    Returns a DataFrame with columns: date, pnl.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    daily_pnl: Dict[str, float] = {}
    for order in orders:
        if order.status != OrderStatus.FILLED or order.side != OrderSide.SELL:
            continue
        filled_at = order.filled_at
        if not filled_at:
            continue
        dt = filled_at
        # Normalize to aware UTC for comparison
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt < cutoff:
            continue
        date_key = dt.date().isoformat()
        filled_price = order.filled_avg_price or order.limit_price or 0.0
        limit_price = order.limit_price or 0.0
        qty = order.quantity
        pnl = (filled_price - limit_price) * qty  # slippage-based rough estimate
        daily_pnl[date_key] = daily_pnl.get(date_key, 0.0) + pnl
    if not daily_pnl:
        return pd.DataFrame(columns=["date", "pnl"])
    rows = [{"date": d, "pnl": p} for d, p in sorted(daily_pnl.items())]
    return pd.DataFrame(rows)


def render_header():
    st.markdown(
        """<div class="brand-kicker">TRADING ENGINE</div>
<h1 class="app-title"><span>◼</span> Shadow/Paper Monitor</h1>
<p class="app-subtitle">Real-time trading activity, positions, and kill-switch control</p>
<hr style="border:0; border-top:1px solid var(--line); margin:1.5rem 0;">""",
        unsafe_allow_html=True,
    )


def render_kill_switch_panel():
    st.markdown('<div class="sidebar-section">Kill Switch</div>', unsafe_allow_html=True)
    if kill_switch.is_halted():
        st.markdown(
            '<span class="status-badge halted">🛑 HALTED</span>',
            unsafe_allow_html=True,
        )
        st.caption(f"**Reason:** {kill_switch.get_reason() or 'Manual halt'}")
        if st.button("🔓 Resume Trading", use_container_width=True):
            kill_switch.disengage()
            st.rerun()
    else:
        st.markdown(
            '<span class="status-badge active">✓ ACTIVE</span>',
            unsafe_allow_html=True,
        )
        if st.button("🛑 Emergency Stop", use_container_width=True, type="primary"):
            kill_switch.engage(reason="Manual emergency stop via UI")
            st.rerun()


def render_mandate_panel():
    st.markdown('<div class="sidebar-section">Mandate</div>', unsafe_allow_html=True)
    mandate = load_mandate(MANDATE_PATH if MANDATE_PATH.exists() else None)
    if mandate.allowed_symbols:
        st.caption(f"**Allowed symbols:** {len(mandate.allowed_symbols)} tickers")
    else:
        st.caption("**Allowed symbols:** All (no whitelist)")
    if mandate.blocked_symbols:
        st.caption(f"**Blocked:** {', '.join(mandate.blocked_symbols)}")
    if mandate.max_notional_per_order:
        st.caption(f"**Max notional/order:** ${mandate.max_notional_per_order:,.0f}")
    if mandate.max_daily_orders:
        st.caption(f"**Max daily orders:** {mandate.max_daily_orders}")


def main():
    st.set_page_config(
        page_title="Trading Monitor",
        page_icon="◼",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_terminal_css()
    
    # Sidebar
    with st.sidebar:
        st.markdown('<div class="sidebar-section">Mode</div>', unsafe_allow_html=True)
        mode = st.radio("Trading mode", ["shadow", "paper"], index=0, label_visibility="collapsed")
        
        render_kill_switch_panel()
        render_mandate_panel()
        
        st.markdown('<div class="sidebar-section">Refresh</div>', unsafe_allow_html=True)
        if st.button("🔄 Reload Data", use_container_width=True):
            st.rerun()
    
    # Main
    render_header()
    
    orders = _load_orders(mode)
    if not orders:
        st.info(f"No {mode} orders found. Start the worker with `--mode {mode}` to generate activity.")
        return
    
    # Metrics row
    filled_orders = [o for o in orders if o.status == OrderStatus.FILLED]
    num_filled = len(filled_orders)
    num_buys = sum(1 for o in filled_orders if o.side == OrderSide.BUY)
    num_sells = sum(1 for o in filled_orders if o.side == OrderSide.SELL)
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(
            f"""<div class="metric-card">
<div class="metric-label">Total Orders</div>
<div class="metric-value">{len(orders)}</div>
<div class="metric-delta">{num_filled} filled</div>
</div>""",
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            f"""<div class="metric-card">
<div class="metric-label">Buys</div>
<div class="metric-value green">{num_buys}</div>
</div>""",
            unsafe_allow_html=True,
        )
    with col3:
        st.markdown(
            f"""<div class="metric-card">
<div class="metric-label">Sells</div>
<div class="metric-value red">{num_sells}</div>
</div>""",
            unsafe_allow_html=True,
        )
    with col4:
        # Net position count
        positions = _compute_positions(orders)
        st.markdown(
            f"""<div class="metric-card">
<div class="metric-label">Open Positions</div>
<div class="metric-value">{len(positions)}</div>
</div>""",
            unsafe_allow_html=True,
        )
    
    # Positions table
    st.subheader("Current Positions")
    if len(positions) > 0:
        st.dataframe(
            positions.style.format({
                "Quantity": "{:.4f}",
                "Avg Entry": "${:,.2f}",
                "Total Cost": "${:,.2f}",
            }),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No open positions.")
    
    # Recent orders
    st.subheader("Recent Orders (last 50)")
    if orders:
        rows = [
            {
                "Symbol": o.symbol,
                "Side": o.side.value,
                "Quantity": o.quantity,
                "Limit Price": o.limit_price,
                "Status": o.status.value,
                "Created At": _format_timestamp(o.created_at),
            }
            for o in orders[:50]
        ]
        df_display = pd.DataFrame(rows)
        st.dataframe(df_display, use_container_width=True, hide_index=True)
    
    # Daily P&L chart (trailing 30 days)
    st.subheader("Daily Realized P&L (trailing 30 days)")
    daily_pnl = _compute_daily_pnl(orders, days=30)
    if len(daily_pnl) > 0:
        st.line_chart(daily_pnl.set_index("date")["pnl"], use_container_width=True)
    else:
        st.caption("No realized P&L in the trailing 30 days.")


if __name__ == "__main__":
    main()
