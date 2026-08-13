"""
Trading Dashboard UI — v2

New in v2:
  - Strategy selector (Stable / Aggressive / Hybrid)
  - Worker start/stop toggle with live status indicator
  - Auto-trade panel showing last signal summary
  - Position table with trailing stop and P&L
  - All existing manual signal injection and ledger preserved
"""
from __future__ import annotations

import html
import uuid
from typing import Optional

import streamlit as st

from backend.trading.broker import BrokerAdapter
from backend.trading.engine.order_manager import OrderManager, OrderStore
from backend.trading.engine.shadow import ShadowTradingEngine
from backend.trading.engine.signal_processor import SignalProcessor
from backend.trading.engine.worker import TradingWorker
from backend.trading.models import Order, OrderSide, OrderStatus, OrderType
from backend.trading.reconciliation.service import ReconciliationService
from backend.trading.risk.gates import RiskEngine, RiskLimits
from backend.trading.strategies.registry import STRATEGY_REGISTRY
from backend.i18n import t


# ---------------------------------------------------------------------------
# Small UI helpers
# ---------------------------------------------------------------------------

def _status_pill(status: OrderStatus) -> str:
    label = status.value.replace("_", " ").upper()
    return f'<span class="status-pill st-{status.value}">{label}</span>'


def _empty_state(icon: str, title: str, desc: str = "") -> None:
    st.markdown(
        f"<div class='empty-state'><div class='empty-icon'>{icon}</div>"
        f"<div>{html.escape(title)}</div>"
        + (f"<div style='margin-top:.25rem;font-size:.74rem;'>{html.escape(desc)}</div>" if desc else "")
        + "</div>",
        unsafe_allow_html=True,
    )


def _dot(color: str) -> str:
    return f"<span style='display:inline-block;width:8px;height:8px;border-radius:50%;background:{color};box-shadow:0 0 6px {color};margin-right:.4rem;'></span>"


# ---------------------------------------------------------------------------
# Worker singleton helpers stored in Streamlit session state
# ---------------------------------------------------------------------------

def _get_or_create_worker(
    broker: BrokerAdapter,
    store: OrderStore,
    reconciler: ReconciliationService,
    strategy_id: str,
) -> TradingWorker:
    if "trading_worker" not in st.session_state or st.session_state.trading_worker is None:
        manager = OrderManager(broker=broker, store=store)
        st.session_state.trading_worker = TradingWorker(
            broker=broker,
            store=store,
            manager=manager,
            reconciler=reconciler,
            strategy_id=strategy_id,
        )
    return st.session_state.trading_worker


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render_trading_dashboard(
    broker: BrokerAdapter,
    store: OrderStore,
    reconciler: ReconciliationService,
    lang: str = "zh_tw",
) -> None:

    # ── Header ──────────────────────────────────────────────────────────────
    st.markdown(
        f"<div class='brand-kicker'>{t('nav.trading', lang)}</div>"
        f"<p class='app-title' style='font-size:1.55rem;margin-bottom:.2rem;'>{t('trading.title', lang)}</p>"
        f"<p class='app-subtitle' style='margin-bottom:1.1rem;'>{t('trading.subtitle', lang)}</p>",
        unsafe_allow_html=True,
    )

    is_paper = getattr(broker, "is_paper", True)
    banner_class = "paper" if is_paper else "live"
    banner_text  = t("trading.mode.paper", lang) if is_paper else t("trading.mode.live", lang)
    st.markdown(
        f"<div class='trading-banner {banner_class}'>"
        f"<span class='banner-dot'></span>{html.escape(banner_text)}</div>",
        unsafe_allow_html=True,
    )

    # ── Tab layout ───────────────────────────────────────────────────────────
    tab_auto, tab_manual, tab_ledger, tab_reconcile = st.tabs([
        "🤖 " + t("trading.tab.auto", lang),
        "✍️ " + t("trading.tab.manual", lang),
        "📋 " + t("trading.tab.ledger", lang),
        "🔍 " + t("trading.tab.reconcile", lang),
    ])

    # ── Fetch account once ───────────────────────────────────────────────────
    summary = None
    try:
        summary = broker.get_account_summary()
    except Exception as e:
        st.error(t("trading.account.error", lang, msg=str(e)))

    # ====================================================================
    # TAB 1: AUTO TRADING
    # ====================================================================
    with tab_auto:
        _render_auto_tab(broker, store, reconciler, summary, lang)

    # ====================================================================
    # TAB 2: MANUAL SIGNAL INJECTION
    # ====================================================================
    with tab_manual:
        _render_manual_tab(broker, store, summary, lang)

    # ====================================================================
    # TAB 3: ORDER LEDGER
    # ====================================================================
    with tab_ledger:
        _render_ledger_tab(store, lang)

    # ====================================================================
    # TAB 4: RECONCILIATION
    # ====================================================================
    with tab_reconcile:
        _render_reconcile_tab(reconciler, summary, lang)


# ---------------------------------------------------------------------------
# Auto-trading tab
# ---------------------------------------------------------------------------

def _render_auto_tab(broker, store, reconciler, summary, lang: str) -> None:
    st.markdown(f"### {t('trading.section.account', lang)}")

    # Account metrics
    if summary:
        col1, col2, col3 = st.columns(3)
        col1.metric(t("trading.metric.portfolio_value", lang), f"${summary.portfolio_value:,.2f}")
        col2.metric(t("trading.metric.cash", lang),            f"${summary.cash:,.2f}")
        col3.metric(t("trading.metric.buying_power", lang),    f"${summary.buying_power:,.2f}")

    st.divider()

    # ── Strategy selector ────────────────────────────────────────────────
    st.markdown(f"### 🎯 {t('trading.section.strategy', lang)}")

    strategy_options = {sid: strat.display_name for sid, strat in STRATEGY_REGISTRY.items()}
    selected_strategy = st.selectbox(
        t("trading.strategy.select", lang),
        options=list(strategy_options.keys()),
        format_func=lambda k: strategy_options[k],
        index=0,
        key="trading_strategy_select",
    )

    strat = STRATEGY_REGISTRY[selected_strategy]
    col_r, col_wl = st.columns(2)
    col_r.metric("预期胜率 Win Rate",   f"{strat.expected_win_rate*100:.0f}%")
    col_wl.metric("风险档位 Risk Profile", strat.risk_profile.upper())

    strategy_descriptions = {
        "stable": "均值回归 · 只买超跌优质股 (RSI<32 + BB下轨 + 基本面>65) · 目标胜率62%",
        "aggressive": "VCP突破 · Minervini形态 + 放量突破 + SMA50确认 · 目标胜率44%，盈亏比2.6",
        "hybrid": "混合型 · 稳健信号优先，无信号时触发激进策略扫描",
    }
    st.caption(strategy_descriptions.get(selected_strategy, ""))

    st.divider()

    # ── Worker start/stop ────────────────────────────────────────────────
    st.markdown(f"### ⚙️ {t('trading.section.worker', lang)}")

    worker: Optional[TradingWorker] = st.session_state.get("trading_worker")
    worker_running = worker is not None and worker.running

    status_color = "#34d399" if worker_running else "#64748b"
    status_label = t("trading.worker.running", lang) if worker_running else t("trading.worker.stopped", lang)
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:.5rem;font-family:var(--mono);font-size:.78rem;'>"
        f"{_dot(status_color)}<span style='color:{status_color};font-weight:700;'>{html.escape(status_label)}</span>"
        + (f"<span style='color:var(--muted);margin-left:.5rem;'>· {html.escape(strat.display_name)}</span>" if worker_running else "")
        + "</div>",
        unsafe_allow_html=True,
    )

    if worker_running:
        market_label = "📈 市场开盘 Market Hours" if (worker and worker.is_market_hours) else "💤 非交易时段 Off-Hours"
        st.caption(market_label)
        if worker and worker.last_run_utc:
            st.caption(f"最近运行 Last run: {worker.last_run_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC")

    col_start, col_stop = st.columns(2)
    with col_start:
        if st.button(
            t("trading.worker.start", lang),
            type="primary",
            disabled=worker_running,
            width="stretch",
            key="worker_start_btn",
        ):
            manager = OrderManager(broker=broker, store=store)
            new_worker = TradingWorker(
                broker=broker, store=store, manager=manager,
                reconciler=reconciler, strategy_id=selected_strategy,
            )
            new_worker.start(interval_seconds=60)
            st.session_state.trading_worker = new_worker
            st.success(t("trading.worker.started_msg", lang, strategy=strat.display_name))
            st.rerun()

    with col_stop:
        if st.button(
            t("trading.worker.stop", lang),
            type="secondary",
            disabled=not worker_running,
            width="stretch",
            key="worker_stop_btn",
        ):
            if worker:
                worker.stop()
                st.session_state.trading_worker = None
            st.warning(t("trading.worker.stopped_msg", lang))
            st.rerun()

    # Hot-swap strategy while worker is running
    if worker_running and worker and worker.strategy.strategy_id != selected_strategy:
        worker.set_strategy(selected_strategy)
        st.info(f"Strategy updated to: {strat.display_name}")

    st.divider()

    # ── Last signal summary ──────────────────────────────────────────────
    st.markdown(f"### 📊 {t('trading.section.last_signals', lang)}")
    if worker and worker.last_signal_summary:
        for row in worker.last_signal_summary:
            action = row.get("action", "")
            color = "#34d399" if "BUY" in action else "#fb7185" if "EXIT" in action else "#64748b"
            st.markdown(
                f"<div class='rec-card' style='padding:.55rem .85rem;min-height:0;margin-bottom:.35rem;"
                f"display:flex;align-items:center;justify-content:space-between;gap:.6rem;flex-wrap:wrap;'>"
                f"<span style='font-family:var(--mono);font-weight:800;color:var(--text);'>{html.escape(row.get('ticker','?'))}</span>"
                f"<span style='font-family:var(--mono);font-size:.7rem;font-weight:700;color:{color};'>{html.escape(action)}</span>"
                f"<span style='font-family:var(--mono);font-size:.68rem;color:var(--muted);'>"
                f"{'$'+str(round(row['price'],2)) if 'price' in row else ''}"
                f"{'  ×'+str(row['qty']) if 'qty' in row else ''}"
                f"</span></div>",
                unsafe_allow_html=True,
            )
            if "reason" in row:
                st.caption(row["reason"][:120])
    else:
        _empty_state("🔍", t("trading.signals.empty", lang), t("trading.signals.empty_desc", lang))

    # ── Open positions with trailing stops ───────────────────────────────
    if summary and summary.positions:
        st.divider()
        st.markdown(f"### {t('trading.positions.title', lang)}")
        for p in summary.positions:
            meta = (worker._position_meta.get(p.symbol, {}) if worker else {})
            stop  = meta.get("stop_loss_price")
            strat_id = meta.get("strategy_id", "—")
            st.markdown(
                "<div class='rec-card' style='padding:.65rem .85rem;min-height:0;margin-bottom:.4rem;"
                "display:flex;align-items:center;justify-content:space-between;gap:.75rem;flex-wrap:wrap;'>"
                f"<span style='font-family:var(--mono);font-weight:800;color:var(--text);font-size:.95rem;'>{html.escape(p.symbol)}</span>"
                f"<span style='font-family:var(--mono);font-size:.75rem;color:var(--muted);'>{p.quantity} @ ${p.average_entry_price:,.2f}</span>"
                + (f"<span style='font-family:var(--mono);font-size:.68rem;color:#fb7185;'>Stop: ${stop:,.2f}</span>" if stop else "")
                + f"<span style='font-family:var(--mono);font-size:.64rem;color:var(--faint);'>{strat_id}</span>"
                "</div>",
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# Manual signal tab  (preserved from v1)
# ---------------------------------------------------------------------------

def _render_manual_tab(broker, store, summary, lang: str) -> None:
    st.markdown(f"### {t('trading.section.signal', lang)}")
    st.caption(t("trading.signal.desc", lang))

    manager = OrderManager(broker=broker, store=store)

    with st.form("signal_injection_form"):
        col1, col2 = st.columns(2)
        with col1:
            sym   = st.text_input(t("trading.signal.symbol", lang), value="AAPL").upper().strip()
            side  = st.selectbox(t("trading.signal.side", lang), ["buy", "sell"],
                                 format_func=lambda v: t(f"trading.signal.side.{v}", lang))
            qty   = st.number_input(t("trading.signal.quantity", lang), min_value=1, value=1, step=1)
        with col2:
            order_type = st.selectbox(t("trading.signal.order_type", lang), ["limit", "market"],
                                      format_func=lambda v: t(f"trading.signal.order_type.{v}", lang))
            limit_price = st.number_input(t("trading.signal.limit_price", lang),
                                          min_value=0.0, value=150.0, step=0.5,
                                          disabled=order_type != "limit")
            use_shadow = st.checkbox(t("trading.signal.shadow_mode", lang), value=True)

        st.markdown(f"<div class='risk-note'>🛡️ {html.escape(t('trading.risk.note', lang))}</div>",
                    unsafe_allow_html=True)
        if use_shadow:
            st.caption(t("trading.signal.shadow_hint", lang))

        submitted = st.form_submit_button(t("trading.signal.submit", lang), type="primary", width="stretch")

        if submitted and sym:
            limits      = RiskLimits()
            risk_engine = RiskEngine(limits)
            resolved    = limit_price if order_type == "limit" else None
            idem        = str(uuid.uuid4())

            if use_shadow:
                o = Order(id=str(uuid.uuid4()), symbol=sym,
                          side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                          order_type=OrderType.LIMIT if order_type == "limit" else OrderType.MARKET,
                          quantity=qty, limit_price=resolved, idempotency_key=idem)
                decision = risk_engine.evaluate_order(o, summary) if summary else None
                if decision is not None and not decision.approved:
                    st.error(t("trading.signal.rejected", lang, reason=decision.reason))
                else:
                    o.status = OrderStatus.RISK_APPROVED
                    shadow   = ShadowTradingEngine(manager)
                    sim      = shadow.simulate_submission(o)
                    st.success(t("trading.signal.shadow_complete", lang, status=sim.status.value))
            else:
                processor = SignalProcessor(broker, risk_engine, manager)
                signal = {"symbol": sym, "side": side, "quantity": qty,
                          "limit_price": resolved, "idempotency_key": idem}
                with st.spinner(t("trading.signal.processing", lang)):
                    result = processor.process_signal(signal)
                if result.status == OrderStatus.REJECTED:
                    st.error(t("trading.signal.rejected", lang, reason=result.error_message))
                else:
                    st.success(t("trading.signal.submitted", lang,
                                 status=result.status.value, id=result.id))


# ---------------------------------------------------------------------------
# Ledger tab
# ---------------------------------------------------------------------------

def _render_ledger_tab(store, lang: str) -> None:
    hdr_col, btn_col = st.columns([3, 1])
    with hdr_col:
        st.markdown(f"### {t('trading.section.ledger', lang)}")
    with btn_col:
        st.button(t("trading.ledger.refresh", lang), width="stretch", key="trading_refresh_ledger")

    try:
        recent = store.get_recent_orders(limit=30) if hasattr(store, "get_recent_orders") else []
        if recent:
            for o in recent:
                side_color = "var(--green)" if o.side.value == "buy" else "var(--red)"
                price_txt  = f"${o.limit_price:,.2f}" if o.limit_price else t("trading.signal.order_type.market", lang)
                note       = f" · {html.escape(str(o.error_message)[:80])}" if o.error_message else ""
                st.markdown(
                    "<div class='rec-card' style='padding:.65rem .85rem;min-height:0;margin-bottom:.45rem;"
                    "display:flex;align-items:center;justify-content:space-between;gap:.75rem;flex-wrap:wrap;'>"
                    "<div style='display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;'>"
                    f"<span style='font-family:var(--mono);font-weight:800;color:var(--text);font-size:.95rem;'>{html.escape(o.symbol)}</span>"
                    f"<span style='font-family:var(--mono);font-size:.68rem;font-weight:700;color:{side_color};'>{o.side.value.upper()}</span>"
                    f"<span style='font-family:var(--mono);font-size:.72rem;color:var(--muted);'>{o.quantity} @ {html.escape(price_txt)}</span>"
                    "</div>"
                    "<div style='display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;'>"
                    f"{_status_pill(o.status)}"
                    f"<span style='font-family:var(--mono);font-size:.64rem;color:var(--faint);'>{o.created_at.strftime('%m-%d %H:%M')} UTC</span>"
                    "</div></div>",
                    unsafe_allow_html=True,
                )
                if note:
                    st.caption(note.lstrip(" ·"))
        else:
            _empty_state("🗒️", t("trading.ledger.empty", lang), t("trading.ledger.empty_desc", lang))
    except Exception as e:
        st.error(t("trading.ledger.load_error", lang, msg=str(e)))


# ---------------------------------------------------------------------------
# Reconciliation tab
# ---------------------------------------------------------------------------

def _render_reconcile_tab(reconciler, summary, lang: str) -> None:
    st.markdown(f"### {t('trading.section.reconciliation', lang)}")
    st.caption(t("trading.reconciliation.desc", lang))
    if st.button(t("trading.reconciliation.run", lang), width="stretch", key="trading_run_reconciliation"):
        with st.spinner(t("trading.reconciliation.running", lang)):
            local_positions = summary.positions if summary else []
            report = reconciler.reconcile_positions(local_positions)
            if report.is_match:
                st.success(f"✅ {t('trading.reconciliation.match', lang)}")
            else:
                st.error(f"❌ {t('trading.reconciliation.mismatch', lang)}")
                for d in report.discrepancies:
                    st.warning(d)
