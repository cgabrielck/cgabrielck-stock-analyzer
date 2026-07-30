import json
import shutil
import os
import io
import html
import time
from typing import Any, Dict, List, Optional

import streamlit as st
import pandas as pd
import altair as alt

from utils.constants import (
    STOCK_UNIVERSE,
    SCORING_WEIGHTS,
    SECTOR_CN_MAP,
    SECTOR_EN_MAP,
    SECTOR_TW_MAP,
    VALUATION_LABELS,
    VALUATION_RANGES,
)
from i18n import t
from account_ui import render_account_panel
from accounts.factory import get_account_repository
from accounts.session import hydrate_account_state
from saved_plan_ui import render_saved_plan_controls

from agents.data_fetcher import fetch_industry_news

CUSTOM_TICKERS_PATH = os.path.join(os.path.dirname(__file__), "data", "custom_tickers.json")
WEIGHT_LABELS: Dict[str, str] = {
    "revenue_growth": "营收增长",
    "eps_growth": "EPS增长",
    "profit_margin": "净利润率",
    "peg_ratio": "PEG比率",
    "roe": "ROE",
    "debt_equity": "负债/权益比",
}


def _load_custom_tickers() -> List[str]:
    # Public deployments keep user-specific symbols in Streamlit session state only.
    return []


def _save_custom_tickers(tickers: List[str]) -> None:
    return None


def _stock_name(s: Dict[str, Any], lang: str) -> str:
    if lang == "en":
        return s.get("name_en", s.get("longName", s["ticker"]))
    elif lang == "zh_tw":
        return s.get("name_tw", s.get("name_cn", s["ticker"]))
    return s.get("name_cn", s.get("longName", s["ticker"]))


def _sector_name(sec: str, lang: str) -> str:
    if lang == "en":
        return SECTOR_EN_MAP.get(sec, sec)
    elif lang == "zh_tw":
        return SECTOR_TW_MAP.get(sec, sec)
    return SECTOR_CN_MAP.get(sec, sec)


def _inject_apple_css() -> None:
    css = """<style>
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
    .stApp::before { content:""; position:fixed; inset:0; pointer-events:none; opacity:.22; background-image:linear-gradient(rgba(96,165,250,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(96,165,250,.025) 1px,transparent 1px); background-size:32px 32px; }
    .stApp .main { max-width: 1440px; margin: 0 auto; }
    .main > div { padding: 1.4rem 2rem 3rem; }
    header[data-testid="stHeader"] { background: transparent; }
    #MainMenu, footer { visibility: hidden; }

    section[data-testid="stSidebar"] { background: #09101a; border-right: 1px solid var(--line); min-width: 282px; }
    section[data-testid="stSidebar"] > div { padding: 1rem .85rem; }
    section[data-testid="stSidebar"] .stMarkdown p { font-size:.78rem; color:var(--muted); }
    .sidebar-section { color:var(--cyan); font-family:var(--mono); font-size:.65rem; font-weight:700; letter-spacing:.14em; text-transform:uppercase; margin:1rem 0 .35rem; padding-bottom:.35rem; border-bottom:1px solid var(--line); }

    .terminal-header { display:flex; justify-content:space-between; gap:1.5rem; align-items:flex-end; padding:1.1rem 0 1.25rem; border-bottom:1px solid var(--line); margin-bottom:1rem; }
    .brand-kicker { color:var(--cyan); font-family:var(--mono); font-size:.66rem; font-weight:700; letter-spacing:.18em; text-transform:uppercase; margin-bottom:.3rem; }
    .app-title { font-size:2rem; line-height:1; font-weight:760; letter-spacing:-.045em; color:var(--text); margin:0; }
    .app-title span { color:var(--cyan); }
    .app-subtitle { color:var(--muted); font-size:.78rem; letter-spacing:.02em; margin:.5rem 0 0; }
    .terminal-live { display:flex; align-items:center; gap:.55rem; color:var(--muted); font-family:var(--mono); font-size:.67rem; text-transform:uppercase; letter-spacing:.1em; }
    .live-dot { width:7px; height:7px; border-radius:50%; background:var(--green); box-shadow:0 0 12px var(--green); animation:pulse 2s infinite; }
    @keyframes pulse { 50% { opacity:.45; } }

    h1,h2,h3 { color:var(--text) !important; letter-spacing:-.02em; }
    h2 { font-size:1.25rem !important; }
    h3 { font-size:1rem !important; }
    p, label, .stMarkdown { color:var(--text); }
    [data-testid="stCaptionContainer"], .stCaption { color:var(--muted) !important; font-size:.78rem; }
    hr { border:0; height:1px; background:var(--line); margin:1.1rem 0; }

    .stTabs [data-baseweb="tab-list"] { gap:.35rem; overflow-x:auto; scrollbar-width:none; border-bottom:1px solid var(--line); padding-bottom:.5rem; margin-bottom:1.2rem; }
    .stTabs [data-baseweb="tab"] { flex:0 0 auto; border:1px solid transparent; border-radius:6px; color:var(--muted); font-family:var(--mono); font-size:.69rem; font-weight:700; letter-spacing:.04em; padding:.42rem .8rem; }
    .stTabs [aria-selected="true"] { color:var(--cyan) !important; background:var(--cyan-soft); border-color:rgba(34,211,197,.28); }

    .rec-card, .feature-card { background:linear-gradient(145deg,var(--panel-2),var(--panel)); border:1px solid var(--line); border-radius:10px; position:relative; overflow:hidden; }
    .rec-card::before { content:""; position:absolute; inset:0 auto 0 0; width:2px; background:var(--cyan); }
    .rec-card { padding:.95rem .8rem; text-align:left; min-height:180px; transition:border-color .18s,transform .18s; }
    .rec-card:hover { transform:translateY(-2px); border-color:var(--line-hot); }
    .rec-card .rc-rank { color:var(--cyan); font-family:var(--mono); font-size:.62rem; font-weight:700; letter-spacing:.13em; margin:0; }
    .rec-card .rc-ticker { color:var(--text); font-family:var(--mono); font-size:1.55rem; font-weight:800; margin:.35rem 0 0; }
    .rec-card .rc-name { color:var(--muted); font-size:.73rem; font-weight:600; margin:.1rem 0; min-height:1.8em; }
    .rec-card .rc-sector { color:var(--faint); font-family:var(--mono); font-size:.6rem; text-transform:uppercase; margin:0 0 .6rem; }
    .rec-card .rc-divider { border:0; height:1px; background:var(--line); margin:.55rem 0; }
    .rec-card .rc-price { color:var(--text); font-family:var(--mono); font-size:1.25rem; font-weight:700; margin:0; white-space:nowrap; }
    .rec-card .rc-score { color:var(--green); font-family:var(--mono); font-size:.7rem; font-weight:700; margin:.2rem 0 0; }
    .feature-card { padding:1rem; height:100%; }
    .feature-card h3 { color:var(--text); margin:0 0 .3rem; }
    .feature-card p { color:var(--muted); font-size:.76rem; margin:0; }

    div[data-testid="stMetric"] { background:linear-gradient(145deg,var(--panel-2),var(--panel)); border:1px solid var(--line); border-radius:8px; padding:.72rem .85rem; min-height:82px; }
    div[data-testid="stMetric"] label { color:var(--muted) !important; font-family:var(--mono); font-size:.72rem !important; letter-spacing:.05em; text-transform:uppercase; }
    div[data-testid="stMetric"] [data-testid="stMetricValue"] { color:var(--text); font-family:var(--mono); font-size:1.12rem; font-weight:700; }
    [class*="st-key-execution_plan_"] [data-testid="stMetricValue"] {
        color:var(--text) !important; font-variant-numeric:tabular-nums; white-space:nowrap;
        font-size:1rem !important; letter-spacing:-.02em;
    }
    [class*="st-key-execution_plan_"] [data-testid="stMetricDelta"] { display:none; }
    [class*="st-key-risk_context_"] [data-testid="stMetricValue"] {
        color:var(--text) !important; font-variant-numeric:tabular-nums;
        font-size:.96rem !important; line-height:1.25; white-space:normal;
        overflow-wrap:anywhere;
    }
    div[data-testid="stMetricDelta"] { font-family:var(--mono); font-size:.66rem; }

    .stExpander { background:var(--panel); border:1px solid var(--line) !important; border-radius:8px !important; margin-bottom:.55rem; overflow:hidden; }
    .stExpander summary { color:var(--text); font-size:.8rem; font-weight:650; padding:.7rem .9rem; }
    .stContainer { background:var(--panel); border:1px solid var(--line) !important; border-radius:8px !important; padding:.75rem !important; }
    .stDataFrame { border:1px solid var(--line); border-radius:8px; overflow:hidden; font-family:var(--mono); font-size:.73rem; }

    .stButton > button { min-height:36px; border-radius:6px !important; border:1px solid var(--line-hot); background:var(--panel-2); color:var(--text); font-size:.74rem; font-weight:700; transition:all .15s; }
    .stButton > button:hover { border-color:var(--cyan); color:var(--cyan); background:var(--cyan-soft); }
    .stButton > button[kind="primary"] { color:#031210; background:linear-gradient(135deg,#2de3d3,#16a99d); border-color:#45f0df; box-shadow:0 0 18px rgba(34,211,197,.14); }
    .stButton > button[kind="primary"]:hover { color:#031210; filter:brightness(1.08); }
    .stDownloadButton > button { background:var(--panel-2); border:1px solid var(--line-hot); color:var(--text); border-radius:6px; }

    input, textarea, [data-baseweb="select"] > div { background:var(--panel) !important; color:var(--text) !important; border-color:var(--line-hot) !important; border-radius:6px !important; }
    [data-baseweb="popover"] { color:var(--text); }
    .stCheckbox label,.stSelectbox label,.stNumberInput label,.stSlider label { color:var(--muted) !important; font-size:.72rem; font-weight:600; }
    .stSlider [data-baseweb="slider"] > div { background:var(--line-hot) !important; }
    .stSlider [data-baseweb="slider"] > div > div,.stProgress > div > div > div { background:var(--cyan) !important; }
    .stSlider [role="slider"] { background:var(--cyan) !important; border:2px solid var(--panel) !important; }
    .stAlert { background:var(--panel-2); border:1px solid var(--line-hot); border-radius:7px; color:var(--text); font-size:.78rem; }
    .stock-tag { display:inline-flex; align-items:center; background:var(--panel-2); border:1px solid var(--line-hot); border-radius:4px; padding:3px 8px; color:var(--text); font-family:var(--mono); font-size:.68rem; font-weight:700; margin:2px; }
    .landing-hero { padding:2.2rem 0 1.2rem; max-width:900px; }
    .landing-eyebrow { color:var(--cyan); font:700 .68rem var(--mono); letter-spacing:.16em; text-transform:uppercase; }
    .landing-title { color:var(--text); font-size:2.5rem; line-height:1.08; letter-spacing:-.045em; font-weight:780; margin:.45rem 0 .7rem; }
    .landing-copy { color:var(--muted); font-size:.95rem; line-height:1.6; max-width:680px; }
    .entry-card { min-height:235px; padding:1.25rem; border:1px solid var(--line-hot); border-radius:12px; background:linear-gradient(145deg,rgba(34,211,197,.08),var(--panel)); }
    .entry-label { color:var(--cyan); font:700 .64rem var(--mono); letter-spacing:.12em; text-transform:uppercase; }
    .entry-title { color:var(--text); font-size:1.35rem; font-weight:750; margin:.5rem 0; }
    .entry-copy { color:var(--muted); font-size:.78rem; line-height:1.55; min-height:76px; }
    .entry-meta { color:var(--faint); font:.66rem var(--mono); margin-top:.9rem; }
    .proof-strip { display:grid; grid-template-columns:repeat(4,1fr); gap:.65rem; margin:1.2rem 0; }
    .proof-item { padding:.9rem; border:1px solid var(--line); border-radius:8px; background:var(--panel); }
    .proof-value { color:var(--text); font:750 1.2rem var(--mono); }
    .proof-label { color:var(--muted); font:.62rem var(--mono); text-transform:uppercase; letter-spacing:.07em; }
    .primary-nav { display:flex; gap:.5rem; margin-bottom:1rem; }
    .st-key-primary_analysis_action { display:block; max-width:420px; margin:.2rem 0 1rem; padding:0 !important; border:0 !important; background:transparent !important; }
    .st-key-primary_analysis_action .stButton > button { min-height:48px; width:100%; font-size:.88rem; }

    @media (max-width: 1024px) { .main > div { padding:1rem; } .app-title{font-size:1.65rem;} }
    @media (max-width: 768px) {
        .main > div { padding:3.8rem .65rem 2rem; }
        .terminal-header { align-items:flex-start; flex-direction:column; gap:.65rem; }
        .app-title { font-size:1.45rem; }
        .app-subtitle { font-size:.7rem; }
        .terminal-live { font-size:.6rem; }
        .st-key-primary_analysis_action { max-width:none; margin:0 0 1rem; }
        .st-key-primary_analysis_action .stButton { margin:0; }
        .st-key-primary_analysis_action .stButton > button { width:100%; min-height:56px; font-size:1rem; letter-spacing:.02em; box-shadow:0 0 24px rgba(34,211,197,.22); }
        .rec-card { min-height:0; padding:.8rem; }
        .landing-hero { padding:.6rem 0; }
        .landing-title { font-size:1.8rem; }
        .landing-copy { font-size:.8rem; }
        .entry-card { min-height:0; }
        .entry-copy { min-height:0; }
        .proof-strip { grid-template-columns:repeat(2,1fr); }
        div[data-testid="stHorizontalBlock"] { flex-wrap:wrap; }
        div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] { min-width:calc(50% - .5rem) !important; flex:1 1 calc(50% - .5rem) !important; }
        .stTabs [data-baseweb="tab-list"] { position:sticky; top:0; z-index:20; background:rgba(7,11,18,.94); backdrop-filter:blur(12px); padding:.45rem 0; }
        .stTabs [data-baseweb="tab"] { font-size:.67rem; padding:.48rem .62rem; min-height:42px; }
        .stButton > button { min-height:44px; }
        div[data-testid="stMetric"] { min-height:72px; padding:.6rem .68rem; }
        div[data-testid="stMetric"] [data-testid="stMetricValue"] { font-size:1rem; }
        .stDataFrame { max-height:70vh; }
        [data-testid="stSidebarCollapsedControl"] { position:fixed !important; top:.65rem !important; left:.65rem !important; z-index:999999 !important; width:auto !important; }
        [data-testid="stSidebarCollapsedControl"] button { width:132px !important; height:46px !important; padding:0 14px !important; border:2px solid var(--cyan) !important; border-radius:8px !important; background:linear-gradient(135deg,#123b3b,#0d2027) !important; color:var(--cyan) !important; box-shadow:0 0 24px rgba(34,211,197,.3) !important; }
        [data-testid="stSidebarCollapsedControl"] button::before { content:""; width:20px; height:16px; margin-right:8px; background:linear-gradient(var(--cyan),var(--cyan)) 0 2px/20px 2px no-repeat,linear-gradient(var(--cyan),var(--cyan)) 0 7px/20px 2px no-repeat,linear-gradient(var(--cyan),var(--cyan)) 0 12px/20px 2px no-repeat; }
        [data-testid="stSidebarCollapsedControl"] button::after { content:"PARAMETERS"; color:var(--cyan); font:750 .72rem var(--mono); letter-spacing:.05em; white-space:nowrap; }
        [data-testid="stSidebarCollapsedControl"] button svg { display:none !important; }
        section[data-testid="stSidebar"] { min-width:0; }
    }
    @media (prefers-reduced-motion: reduce) { .live-dot { animation:none; } .rec-card { transition:none; } }
</style>"""
    st.markdown(css, unsafe_allow_html=True)


def init_state() -> None:
    defaults: Dict[str, Any] = {
        "app_route": "home",
        "recommendations": [],
        "all_rankings": [],
        "scored_data": [],
        "source_health": {},
        "upgrade_logs": [],
        "analysis_running": False,
        "analysis_done": False,
        "custom_tickers": _load_custom_tickers(),
        "lang": "zh_tw",
        "portfolio": {},
        "portfolio_state": {},
        "portfolio_journal": [],
        "market_regime": {},
        "picks_status": "idle",
        "picks_results": {},
        "picks_errors": {},
        "picks_selection_widget": [],
        "picks_analyzed_tickers": [],
        "picks_successful_tickers": [],
        "picks_recent_successful_tickers": [],
        "picks_analyzed_at": None,
        "picks_run_error": None,
        "picks_news_selection_widget": [],
        "picks_news_results": {},
        "picks_news_analyzed_tickers": [],
        "picks_news_analyzed_at": None,
        "picks_news_status": "idle",
        "deep_workspace": "research",
        "scan_view": "overview",
        "scan_filter_tickers": [],
        "backtest_import_signature": None,
        "backtest_ticker_widget": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def build_sidebar() -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    lang = st.session_state.get("lang", "zh_tw")
    selected_lang = lang

    st.sidebar.markdown(
        f'<div class="sidebar-section">{t("sidebar.control", lang)}</div>',
        unsafe_allow_html=True)

    lang_opts = {"zh_cn": "简体中文", "zh_tw": "繁體中文", "en": "English"}
    selected_lang = st.sidebar.selectbox(
        "Language", options=list(lang_opts.keys()),
        format_func=lambda k: lang_opts[k],
        index=list(lang_opts.keys()).index(selected_lang),
        key="lang_selector", label_visibility="collapsed",
    )
    if selected_lang != lang:
        st.session_state.lang = selected_lang
        st.rerun()
    params["lang"] = selected_lang
    render_account_panel(get_account_repository(), selected_lang, st.session_state)

    st.sidebar.markdown(
        f'<div class="sidebar-section">{t("sidebar.sector_set", selected_lang)}</div>',
        unsafe_allow_html=True)
    st.sidebar.caption(t("sidebar.sector_prompt", selected_lang))

    sectors = sorted(set(s["sector"] for s in STOCK_UNIVERSE))
    all_checked = st.sidebar.checkbox(t("sidebar.select_all", selected_lang), value=True, key="select_all")
    selected_sectors: List[str] = []
    for sec in sectors:
        sec_name = _sector_name(sec, selected_lang)
        checked = st.sidebar.checkbox(
            f"{sec_name}",
            value=all_checked,
            key=f"sector_set_{sec}",
        )
        if checked:
            selected_sectors.append(sec)

    st.sidebar.markdown(
        f'<div class="sidebar-section">{t("sidebar.stock_pool", selected_lang)}</div>',
        unsafe_allow_html=True)

    selected_tickers: List[str] = []
    for sec in sectors:
        if sec not in selected_sectors:
            continue
        sec_name = _sector_name(sec, selected_lang)
        stocks_in_sec = [s for s in STOCK_UNIVERSE if s["sector"] == sec]
        with st.sidebar.expander(f"{sec_name} ({len(stocks_in_sec)})", expanded=False):
            sec_all = st.checkbox(
                t("sidebar.select_sector", selected_lang, sector=sec_name),
                value=True, key=f"sec_{sec}",
            )
            for s in stocks_in_sec:
                s_name = _stock_name(s, selected_lang)
                checked = st.checkbox(
                    f"{s['ticker']} — {s_name}",
                    value=sec_all, key=f"stock_{s['ticker']}",
                )
                if checked:
                    selected_tickers.append(s["ticker"])

    st.sidebar.markdown(
        f'<div class="sidebar-section">{t("sidebar.custom_tickers", selected_lang)}</div>',
        unsafe_allow_html=True)

    bulk_tickers = st.sidebar.text_area(
        t("pool.bulk_add", selected_lang),
        placeholder=t("pool.bulk_placeholder", selected_lang),
        label_visibility="collapsed", key="bulk_custom",
        help=t("pool.bulk_placeholder", selected_lang),
    )
    add_col1, add_col2, add_col3 = st.sidebar.columns([2, 1, 1])
    with add_col1:
        st.caption(t("pool.bulk_add", selected_lang))
    with add_col2:
        add_clicked = st.button(t("sidebar.custom_add", selected_lang), width="stretch", key="add_custom_btn")
    with add_col3:
        clear_custom = st.button("", width="stretch", key="clear_custom_btn")

    if add_clicked and bulk_tickers.strip():
        extra = [t_.strip().upper() for t_ in bulk_tickers.replace("\n", ",").split(",") if t_.strip()]
        changed = False
        for t_ in extra:
            if t_ and t_ not in st.session_state.custom_tickers:
                st.session_state.custom_tickers.append(t_)
                changed = True
        if changed:
            _save_custom_tickers(st.session_state.custom_tickers)
            st.rerun()

    if clear_custom and st.session_state.custom_tickers:
        st.session_state.custom_tickers = []
        _save_custom_tickers([])
        st.rerun()

    if st.session_state.custom_tickers:
        tags_html = "".join(
            f'<span class="stock-tag">{html.escape(t_)}</span>'
            for t_ in st.session_state.custom_tickers
        )
        st.sidebar.markdown(f"<div style='margin:4px 0;'>{tags_html}</div>", unsafe_allow_html=True)
        for t_ in list(st.session_state.custom_tickers):
            col_a, col_b = st.sidebar.columns([5, 1])
            with col_a:
                st.markdown(f"<span style='font-size:0.75rem;'>{html.escape(t_)}</span>", unsafe_allow_html=True)
            with col_b:
                if st.button("Remove", key=f"remove_{t_}", help=t("sidebar.custom_remove", selected_lang), width="stretch"):
                    st.session_state.custom_tickers.remove(t_)
                    _save_custom_tickers(st.session_state.custom_tickers)
                    st.rerun()

    for t_ in st.session_state.custom_tickers:
        if t_ not in selected_tickers:
            selected_tickers.append(t_)

    if not selected_tickers:
        selected_tickers = [s["ticker"] for s in STOCK_UNIVERSE]
    params["selected_tickers"] = selected_tickers

    st.sidebar.markdown(
        f'<div class="sidebar-section">{t("sidebar.weights", selected_lang)}</div>',
        unsafe_allow_html=True)
    weights: Dict[str, float] = {}
    cols = st.sidebar.columns(2)
    for i, (key, label) in enumerate(WEIGHT_LABELS.items()):
        default_pct = int(SCORING_WEIGHTS[key] * 100)
        with cols[i % 2]:
            val = st.slider(label, 0, 100, default_pct, key=f"w_{key}")
            weights[key] = val / 100.0
    total_w = sum(weights.values())
    st.sidebar.caption(t("sidebar.weight_total", selected_lang, w=total_w))
    params["custom_weights"] = {k: v / total_w for k, v in weights.items()} if total_w > 0 else weights

    st.sidebar.markdown(
        f'<div class="sidebar-section">{t("sidebar.filters", selected_lang)}</div>',
        unsafe_allow_html=True)
    filters: Dict[str, Any] = {}
    filters["min_revenue_growth"] = st.sidebar.number_input(t("sidebar.min_revenue", selected_lang), value=0, key="f_rev")
    filters["max_peg"] = st.sidebar.number_input(t("sidebar.max_peg", selected_lang), value=100, key="f_peg")
    filters["min_roe"] = st.sidebar.number_input(t("sidebar.min_roe", selected_lang), value=0, key="f_roe")
    filters["max_debt_equity"] = st.sidebar.number_input(t("sidebar.max_de", selected_lang), value=100, key="f_de")
    params["filters"] = filters

    use_llm = st.session_state.get("use_llm", False)
    if use_llm:
        st.sidebar.markdown(
            f'<div class="sidebar-section"> LLM {t("sidebar.weights", selected_lang)}</div>',
            unsafe_allow_html=True)
        llm_w = st.sidebar.slider(
            t("sidebar.llm_weight", selected_lang, default=20),
            0, 50, int(getattr(st.session_state, "llm_weight", 20) * 100),
            key="llm_weight_slider",
        )
        st.session_state.llm_weight = llm_w / 100.0
        params["llm_weight"] = llm_w / 100.0
        st.sidebar.caption(t("sidebar.llm_hint", selected_lang))

    st.sidebar.markdown(f'<div class="sidebar-section">{t("portfolio.title", selected_lang)}</div>', unsafe_allow_html=True)
    portfolio_capital = st.sidebar.number_input(
        t("portfolio.capital", selected_lang),
        min_value=1000, max_value=10_000_000, value=100_000, step=10_000,
        key="portfolio_capital",
    )
    params["portfolio_capital"] = portfolio_capital

    st.sidebar.markdown("<hr style='margin:1.2rem 0;'>", unsafe_allow_html=True)
    params["run_clicked"] = False
    params["force_refresh"] = st.sidebar.checkbox(
        t("sidebar.refresh", selected_lang), value=False, key="force_refresh_cb"
    )

    return params


def show_source_health() -> None:
    lang = st.session_state.get("lang", "zh_tw")
    health = st.session_state.source_health or {}
    if health:
        st.sidebar.markdown(
            f'<div class="sidebar-section">{t("sidebar.source_status", lang)}</div>',
            unsafe_allow_html=True)
        for source, info in sorted(health.items()):
            total = info.get("success", 0) + info.get("failure", 0)
            rate = info.get("success", 0) / total * 100 if total > 0 else 0
            icon = "" if rate >= 80 else "" if rate >= 50 else ""
            st.sidebar.markdown(
                f"<div style='font-size:0.7rem;margin:1px 0;'>{icon} {source} — {rate:.0f}%</div>",
                unsafe_allow_html=True)
        from agents.data_fetcher import _SEED_DATA
        _seed_count = len(_SEED_DATA)
        _seed_txt = f" seed={_seed_count}" if _seed_count else " seed=0 "
        st.sidebar.caption(_seed_txt)
        st.sidebar.caption(t("sidebar.last_update", lang, time=pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")))
        st.sidebar.caption(t("sidebar.data_from", lang))
        st.sidebar.caption(t("sidebar.version", lang))


def run_analysis(params: Dict[str, Any]) -> None:
    lang = params.get("lang", "zh_tw")
    st.session_state.analysis_running = True
    progress_bar = st.progress(0, text=t("app.init_engine", lang))
    status_text = st.empty()

    def update_progress(completed: int, total: int) -> None:
        pct = completed / total
        progress_bar.progress(pct)
        status_text.markdown(t("app.fetching", lang, c=completed, t=total))

    from agents.recommender import run_full_analysis

    force_refresh = params.get("force_refresh", False)
    if force_refresh:
        from utils.cache import cache
        tickers = params.get("selected_tickers") or [s["ticker"] for s in STOCK_UNIVERSE]
        for tk in tickers:
            cache.delete(tk, "info")
            cache.delete(f"tech_{tk}", "info")
            cache.delete(f"news_v3_{tk}_7d", "info")

    try:
        results = run_full_analysis(
            progress_callback=update_progress,
            selected_tickers=params.get("selected_tickers"),
            custom_weights=params.get("custom_weights"),
            filters=params.get("filters"),
            lang=lang,
            llm_weight=params.get("llm_weight", 0.2),
            force_refresh=force_refresh,
            use_llm_analysis=False,
        )
    except Exception as exc:
        st.error(t("app.error", lang, msg=str(exc)))
        return
    finally:
        progress_bar.empty()
        status_text.empty()
        st.session_state.analysis_running = False

    st.session_state.recommendations = results.get("recommendations", [])
    st.session_state.all_rankings = results.get("all_rankings", [])
    st.session_state.scored_data = results.get("scored_data", [])
    st.session_state.source_health = results.get("source_health", {})
    st.session_state.upgrade_logs = results.get("upgrade_logs", [])
    st.session_state.agent_summary = results.get("agent_summary", {})
    st.session_state.use_llm = results.get("use_llm", False)
    st.session_state.market_regime = results.get("market_regime", {})
    valid_tickers = {stock["ticker"] for stock in st.session_state.scored_data}
    st.session_state.deep_selection_widget = [
        ticker for ticker in st.session_state.get("deep_selection_widget", []) if ticker in valid_tickers
    ]
    st.session_state.analysis_done = True

    from agents.portfolio_manager import build_portfolio
    st.session_state.portfolio = build_portfolio(
        st.session_state.recommendations,
        total_capital=params.get("portfolio_capital", 100000),
        target_allocation=st.session_state.market_regime.get("target_allocation", 0.90),
        previous_state=st.session_state.get("portfolio_state", {}),
        journal=st.session_state.get("portfolio_journal", []),
    )
    st.session_state.portfolio_state = st.session_state.portfolio.pop("session_state", {})
    st.session_state.portfolio_journal = st.session_state.portfolio.pop("journal", [])

    if results.get("error"):
        st.error(t("app.error", lang, msg=results["error"]))
    else:
        st.success(t("app.complete", lang, n=len(st.session_state.all_rankings)))
        regime = st.session_state.market_regime
        if regime:
            st.info(t(
                "regime.banner",
                lang,
                regime=t(f"regime.{regime.get('regime', 'neutral')}", lang),
                exposure=regime.get("target_allocation", 0.7) * 100,
                entry=regime.get("entry_threshold", 65),
                vix=regime.get("vix") if regime.get("vix") is not None else "N/A",
            ))

    debug_all = results.get("_debug_all_data", {})
    with st.expander(" Debug", expanded=False):
        sd = len(st.session_state.scored_data)
        ad = len(debug_all)
        st.write(f" Pipeline: selected={len(params.get('selected_tickers', []))} → fetched={ad} → scored={sd} → ranked={len(st.session_state.all_rankings)}")
        if debug_all:
            skipped = []
            for k, v in debug_all.items():
                if "error" in v:
                    skipped.append(f"{k}: error={str(v.get('error',''))[:30]}")
                elif v.get("sector") is None:
                    skipped.append(f"{k}: sector=None")
            if skipped:
                st.error(f"Skipped {len(skipped)} stocks:")
                for s in skipped:
                    st.write(f"  - {s}")
        st.write(f" Health keys: {len(results.get('source_health', {}))}")


def _find_local_extrema(series, window: int = 5):
    local_max = (series == series.rolling(window, center=True).max())
    local_min = (series == series.rolling(window, center=True).min())
    # Expose the first/last points
    return local_max, local_min


def _build_tech_chart(
    ticker: str,
    interval: str = "1d",
    extended_hours: bool = False,
    current_price: Optional[float] = None,
    trade_plan: Optional[Dict[str, Any]] = None,
    detail_mode: bool = False,
) -> Dict[str, Any]:
    from plotly.subplots import make_subplots
    import plotly.graph_objects as go
    from utils.chart_utils import fetch_chart_data

    result = fetch_chart_data(ticker, interval, extended_hours)
    if result.get("error"):
        return result
    try:
        df = result["data"].copy()
        is_intraday = result["interval"] != "1d"
        df["SMA20"] = df["Close"].rolling(20).mean()
        df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
        if not is_intraday:
            df["SMA50"] = df["Close"].rolling(50).mean()
            bb_std = df["Close"].rolling(20).std()
            df["BB_upper"] = df["SMA20"] + bb_std * 2
            df["BB_lower"] = df["SMA20"] - bb_std * 2
            delta = df["Close"].diff()
            gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
            loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
            df["RSI"] = 100 - (100 / (1 + gain / loss.replace(0, float("nan"))))
            ema_fast = df["Close"].ewm(span=12, adjust=False).mean()
            ema_slow = df["Close"].ewm(span=26, adjust=False).mean()
            df["MACD"] = ema_fast - ema_slow
            df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
            df["MACD_hist"] = df["MACD"] - df["MACD_signal"]

        rows = 4 if detail_mode and not is_intraday else 2
        fig = make_subplots(
            rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.025,
            row_heights=[0.76, 0.24] if rows == 2 else [0.52, 0.14, 0.14, 0.2],
            subplot_titles=(ticker, "Volume") if rows == 2 else (ticker, "Volume", "RSI (buy/sell pressure)", "MACD (momentum)"),
        )
        fig.add_trace(go.Candlestick(
            x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
            increasing_line_color="#34d399", decreasing_line_color="#fb7185", name="Price",
        ), row=1, col=1)
        trend = df["EMA20"] if is_intraday else df["SMA50"]
        trend_name = "20-period short trend" if is_intraday else "50-day trend"
        fig.add_trace(go.Scatter(x=df.index, y=trend, line=dict(color="#a78bfa", width=1.5), name=trend_name), row=1, col=1)
        if current_price and float(df["Low"].min()) * 0.8 <= current_price <= float(df["High"].max()) * 1.2:
            fig.add_hline(y=current_price, line_dash="dash", line_color="#60a5fa", annotation_text=f"Now ${current_price:.2f}", row=1, col=1)
        colors = ["#fb7185" if close < open_ else "#34d399" for open_, close in zip(df["Open"], df["Close"])]
        fig.add_trace(go.Bar(x=df.index, y=df["Volume"], marker_color=colors, opacity=0.55, name="Volume"), row=2, col=1)

        if detail_mode and not is_intraday:
            fig.add_trace(go.Scatter(x=df.index, y=df["SMA20"], line=dict(color="#fbbf24", width=1), name="SMA20"), row=1, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=df["EMA20"], line=dict(color="#22d3c5", width=1), name="EMA20"), row=1, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=df["SMA50"], line=dict(color="#a78bfa", width=1), name="SMA50"), row=1, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=df["BB_upper"], line=dict(color="#64748b", width=1, dash="dot"), name="BB Upper"), row=1, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=df["BB_lower"], line=dict(color="#64748b", width=1, dash="dot"), name="BB Lower"), row=1, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=df["RSI"], line=dict(color="#a78bfa", width=1), name="RSI"), row=3, col=1)
            fig.add_hline(y=70, line_dash="dot", line_color="#fb7185", row=3, col=1)
            fig.add_hline(y=30, line_dash="dot", line_color="#34d399", row=3, col=1)
            macd_colors = ["#fb7185" if value < 0 else "#34d399" for value in df["MACD_hist"]]
            fig.add_trace(go.Bar(x=df.index, y=df["MACD_hist"], marker_color=macd_colors, opacity=0.55, name="MACD Hist"), row=4, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=df["MACD"], line=dict(color="#60a5fa", width=1), name="MACD"), row=4, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=df["MACD_signal"], line=dict(color="#fbbf24", width=1), name="Signal"), row=4, col=1)
        _add_trade_layers(fig, trade_plan, include_all=detail_mode)
        if detail_mode and not is_intraday:
            _add_pattern_layers(fig, df)

        fig.update_layout(
            height=540 if rows == 2 else 760, template="plotly_dark", paper_bgcolor="#0d1420",
            plot_bgcolor="#0d1420", font=dict(color="#cbd5e1"), xaxis_rangeslider_visible=False,
            margin=dict(l=12, r=12, t=45, b=15), hovermode="x unified", dragmode="pan",
            showlegend=detail_mode, legend=dict(orientation="h", y=1.04, x=0),
        )
        fig.update_xaxes(showgrid=True, gridcolor="rgba(126,145,168,.12)", rangeslider_visible=False)
        fig.update_yaxes(showgrid=True, gridcolor="rgba(126,145,168,.12)", fixedrange=False)
        _focus_recent_candles(fig, df, is_intraday)
        if not extended_hours or not is_intraday:
            fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
        result["figure"] = fig
        result["rows"] = len(df)
        result["first_bar"] = df.index[0].isoformat()
        result["last_bar"] = df.index[-1].isoformat()
        return result
    except Exception as exc:
        return {**result, "error": f"{type(exc).__name__}: {exc}", "stage": "render"}


def _focus_recent_candles(fig: Any, df: pd.DataFrame, is_intraday: bool) -> None:
    visible_bars = 120 if is_intraday else 90
    if len(df) <= visible_bars:
        return
    start = df.index[-visible_bars]
    step = df.index[-1] - df.index[-2]
    fig.update_xaxes(range=[start, df.index[-1] + step * 5])


def _add_pattern_layers(fig: Any, df: pd.DataFrame) -> None:
    from agents.pattern_analyzer import analyze_patterns
    import plotly.graph_objects as go

    atr = float((df["High"] - df["Low"]).tail(14).mean()) if len(df) >= 14 else None
    analysis = analyze_patterns(df, atr=atr)
    fib = analysis.get("fibonacci")
    if fib:
        for ratio, level in fib["levels"].items():
            fig.add_hline(
                y=level, row=1, col=1, line_dash="dot", line_width=1,
                line_color="rgba(167,139,250,.55)", annotation_text=f"Fib {ratio}",
            )
    for pattern in analysis.get("patterns", []):
        anchors = pattern["anchors"]
        fig.add_trace(go.Scatter(
            x=[anchor["time"] for anchor in anchors], y=[anchor["price"] for anchor in anchors],
            mode="lines+markers", line=dict(color="#a78bfa", width=2, dash="dash"),
            marker=dict(size=7), name=pattern["kind"].replace("_", " ").title(),
        ), row=1, col=1)
        status = "confirmed" if pattern["status"] == "confirmed" else "watching"
        for label, value, color in (
            (f"{status} neckline", pattern["neckline"], "#60a5fa"),
            ("pattern target", pattern["target"], "#34d399"),
            ("pattern invalidation", pattern["invalidation"], "#fb7185"),
        ):
            fig.add_hline(y=value, row=1, col=1, line_dash="dash", line_color=color, annotation_text=label)
    fig.layout.meta = {"pattern_analysis": analysis}


def _add_trade_layers(fig: Any, trade_plan: Optional[Dict[str, Any]], include_all: bool) -> None:
    if not trade_plan:
        return
    entry = trade_plan.get("entry_zone", {})
    low, high = entry.get("low"), entry.get("high")
    if isinstance(low, (int, float)) and isinstance(high, (int, float)) and low < high and not include_all:
        fig.add_hrect(
            y0=low, y1=high, row=1, col=1, fillcolor="rgba(52,211,153,.16)",
            line_width=0, annotation_text="Entry zone", annotation_position="top left",
        )
    levels = (
        ("Stop", trade_plan.get("stop_loss"), "#fb7185"),
    )
    if include_all:
        levels = (("Entry low", low, "rgba(52,211,153,.55)"), ("Entry high", high, "rgba(52,211,153,.55)")) + levels
    targets = trade_plan.get("targets", [])
    levels += (("Target 1", targets[0], "#fbbf24"),) if targets else ()
    if include_all:
        levels += (("Confirmation", trade_plan.get("confirmation_price"), "#60a5fa"),)
        levels += tuple((f"Target {index + 1}", target, "#fbbf24") for index, target in enumerate(targets[1:], start=2))
    for label, value, color in levels:
        if isinstance(value, (int, float)):
            fig.add_hline(y=value, row=1, col=1, line_dash="dot", line_color=color, annotation_text=label)


def _render_kline_chart(
    ticker: str, lang: str, key_prefix: str, current_price: Optional[float] = None,
    trade_plan: Optional[Dict[str, Any]] = None,
) -> None:
    intervals = ["1d", "60m", "15m", "1m"]
    interval_labels = {
        "1d": t("chart.horizon_year", lang), "60m": t("chart.horizon_short", lang),
        "15m": t("chart.horizon_entry", lang), "1m": t("chart.horizon_today", lang),
    }
    controls = st.columns([2, 2, 1])
    with controls[0]:
        interval = st.selectbox(
            t("chart.horizon", lang), intervals, format_func=interval_labels.get,
            key=f"{key_prefix}_interval",
        )
    with controls[1]:
        detail_mode = st.toggle(t("chart.detail_mode", lang), value=False, key=f"{key_prefix}_detail")
    with controls[2]:
        if st.button(t("chart.refresh", lang), key=f"{key_prefix}_refresh", width="stretch"):
            from utils.chart_utils import fetch_chart_data
            fetch_chart_data.clear()
            st.rerun()
    session_mode = "regular"
    if interval != "1d":
        session_mode = st.radio(
            t("chart.session", lang), ["regular", "extended"], horizontal=True,
            format_func=lambda value: t(f"chart.{value}", lang), key=f"{key_prefix}_session",
        )
    st.caption(t("chart.detail_explainer" if detail_mode else "chart.simple_explainer", lang))
    st.caption(t("chart.gesture_hint", lang))
    if detail_mode:
        st.info(t("chart.indicator_guide", lang))

    with st.spinner(t("chart.loading", lang)):
        chart = _build_tech_chart(
            ticker,
            interval=interval,
            extended_hours=session_mode == "extended" and interval != "1d",
            current_price=current_price,
            trade_plan=trade_plan,
            detail_mode=detail_mode,
        )
    if chart.get("error"):
        st.error(t("chart.error", lang, stage=chart.get("stage", "history"), msg=chart["error"]))
        st.caption(t("chart.retry_hint", lang))
        return
    st.plotly_chart(
        chart["figure"], width="stretch", theme=None, key=f"{key_prefix}_plot",
        config={
            "modeBarButtonsToAdd": ["drawline", "eraseshape"] if detail_mode else [],
            "modeBarButtonsToRemove": ["sendDataToCloud", "lasso2d", "select2d", "zoom2d", "pan2d", "autoScale2d", "resetScale2d"],
            "displayModeBar": detail_mode,
            "displaylogo": False,
            "scrollZoom": True,
            "responsive": True,
        },
    )
    st.caption(t(
        "chart.metadata", lang, provider=chart.get("provider", "N/A"), rows=chart.get("rows", 0),
        first=pd.Timestamp(chart["first_bar"]).strftime("%Y-%m-%d %H:%M ET"),
        last=pd.Timestamp(chart["last_bar"]).strftime("%Y-%m-%d %H:%M ET"),
    ))
    if interval == "1d":
        patterns = chart["figure"].layout.meta.get("pattern_analysis", {}) if chart["figure"].layout.meta else {}
        if detail_mode and patterns.get("patterns"):
            pattern = patterns["patterns"][0]
            st.caption(t(
                "chart.pattern_status", lang,
                pattern=pattern["kind"].replace("_", " ").title(), status=pattern["status"],
            ))
        if trade_plan and detail_mode:
            from pine_export import build_pine_script

            st.download_button(
                t("chart.download_pine", lang),
                data=build_pine_script(ticker, trade_plan, patterns),
                file_name=f"{ticker.lower()}_research.pine", mime="text/plain",
                key=f"{key_prefix}_pine_download", width="stretch",
            )


def _strategy_fail_warning(strategy_id: str, tech_data: Dict[str, Any]) -> str:
    msg = ""
    if strategy_id == "breakout_momentum":
        vol_ratio = tech_data.get("volume_ratio_10_50")
        if vol_ratio and vol_ratio < 1.2:
            msg = " 突破策略在量能 < 1.2× 均量時有 ~60% 假突破率。確認放量再進。"
    elif strategy_id == "mean_reversion":
        rsi = tech_data.get("rsi_14")
        if rsi and rsi < 20:
            msg = " V 型反彈中均值回歸策略有 ~40% 鞭打率。等待第二隻腳確認。"
    return msg


def _build_trade_plan_text(data: Dict[str, Any], lang: str) -> str:
    lines = [f"=== Trade Plan ({data.get('id', '')}) ==="]
    ep = data.get("entry_price")
    if ep:
        lines.append(f"Entry: ${ep:.2f}")
    for tgt in data.get("targets", []):
        lines.append(f"Target ${tgt['price']:.2f} ({tgt.get('size_pct', 100)}%)")
    sp = data.get("stop_loss")
    if sp:
        lines.append(f"Stop: ${sp:.2f}")
    rr = data.get("risk_reward")
    if rr:
        lines.append(f"R:R = {rr:.1f}")
    ml = data.get("max_loss_usd")
    if ml:
        lines.append(f"Max Loss: ${ml:.0f}")
    return "\n".join(lines)


def render_recommendations_tab() -> None:
    lang = st.session_state.get("lang", "zh_tw")
    recs = st.session_state.recommendations
    st.subheader(t("deep.system_picks", lang))
    if not recs:
        st.warning(t("recommend.nodata", lang))
        return

    with st.expander(t("risk.title", lang), expanded=False):
        st.caption(t("risk.disclaimer", lang))

    cols = st.columns(len(recs))
    for i, (col, rec) in enumerate(zip(cols, recs)):
        with col:
            score = rec.get("risk_adjusted_score", rec.get("total_score", 0))
            price = rec.get("price")
            price_str = f"${price:.2f}" if price else "$N/A"
            sec_name = _sector_name(rec.get("sector", ""), lang)
            name = _stock_name(rec, lang)
            signal = rec.get("llm_key_signal", "")
            signal_emoji = {"bullish": "", "neutral": "", "bearish": ""}.get(signal, "")
            signal_html = f"""<p style="font-size:0.7rem;margin:2px 0 0;color:#86868b;">
                {t('llm.signal', lang)}: {signal_emoji} {t('llm.signal_' + signal, lang) if signal else ''}
            </p>""" if signal else ""
            st.markdown(
                f"""
                <div class="rec-card">
                    <p class="rc-rank">RANK #{i+1}</p>
                    <p class="rc-ticker">{rec['ticker']}</p>
                    <p class="rc-name">{name}</p>
                    <p class="rc-sector">{sec_name}</p>
                    <div class="rc-divider"></div>
                    <p class="rc-price">{price_str}</p>
                    <p class="rc-score">{t('recommend.score', lang, s=score)}</p>
                    {signal_html}
                </div>
                """,
                unsafe_allow_html=True,
            )


    st.markdown("<hr style='margin:1.5rem 0;'>", unsafe_allow_html=True)
    for i, rec in enumerate(recs):
        sec = rec.get("sec_insights") or {}
        form_type = sec.get("form_type") or "N/A"
        filing_date = sec.get("filing_date") or "N/A"
        sec_url = sec.get("url")
        insights = sec.get("insights")
        name = _stock_name(rec, lang)
        sec_cn = _sector_name(rec.get("sector", ""), lang)

        with st.expander(
            f" **#{i+1} {rec['ticker']} — {name}**"
            f" ({sec_cn}) | {t('recommend.score', lang, s=rec.get('total_score', 0))}"
        ):
            mcol1, mcol2, mcol3 = st.columns(3)
            with mcol1:
                price_val = rec.get("price")
                price_session = rec.get("price_session", "")
                if price_val:
                    session_badge = ""
                    if price_session == "Pre-Market Trading":
                        session_badge = ""
                    elif price_session == "After-Hours Trading":
                        session_badge = ""
                    elif price_session == "Overnight Trading":
                        session_badge = ""
                    elif price_session == "Regular Trading Hours":
                        session_badge = ""
                    price_label = f"${price_val:.2f}"
                    st.metric(t("metric.price", lang), price_label)
                    badge = ""
                    if price_session:
                        stale_label = " · STALE" if rec.get("price_stale") else ""
                        badge = f"<span style='font:600 .65rem var(--mono);color:var(--cyan);'>({html.escape(price_session)}{stale_label})</span>"
                    quote_time = rec.get("price_quote_time")
                    price_source = rec.get("price_source")
                    if quote_time or price_source:
                        quote_text = " · ".join(value for value in [price_source, quote_time] if value)
                        badge += f" <span style='font:500 .61rem var(--mono);color:var(--muted);'>{html.escape(quote_text)}</span>"
                    fetched = rec.get("data_quality", {}).get("fetched_at") or rec.get("fetched_at")
                    if fetched:
                        try:
                            ft = pd.Timestamp(fetched)
                            if ft.tzinfo is None:
                                ft = ft.tz_localize("UTC")
                            mins_ago = (pd.Timestamp.now(tz="UTC") - ft.tz_convert("UTC")).total_seconds() / 60
                            if mins_ago < 5:
                                dot = ""
                            elif mins_ago < 30:
                                dot = ""
                            else:
                                dot = ""
                            badge += f" <span style='font-size:0.7rem;color:#86868b;'>{dot} {_format_timestamp(fetched)}</span>"
                        except (TypeError, ValueError):
                            pass
                    if badge:
                        st.markdown(badge, unsafe_allow_html=True)
                else:
                    st.metric(t("metric.price", lang), "N/A")
                st.metric(t("metric.revenue", lang), f"{rec.get('revenue_growth', 0):.1f}%" if rec.get("revenue_growth") is not None else "N/A")
                st.metric(t("metric.profit_margin", lang), f"{rec.get('profit_margin', 0):.1f}%" if rec.get("profit_margin") is not None else "N/A")
            with mcol2:
                st.metric(t("metric.eps", lang), f"{rec.get('eps_growth', 0):.1f}%" if rec.get("eps_growth") is not None else "N/A")
                st.metric(t("metric.roe", lang), f"{rec.get('roe', 0):.1f}%" if rec.get("roe") is not None else "N/A")
                st.metric(t("metric.peg", lang), f"{rec.get('peg', 0):.2f}" if rec.get("peg") is not None else "N/A")
            with mcol3:
                st.metric(t("metric.pe", lang), f"{rec.get('pe_ratio', 0):.2f}" if rec.get("pe_ratio") is not None else "N/A")
                st.metric(t("metric.de", lang), f"{rec.get('debt_equity', 0):.2f}" if rec.get("debt_equity") is not None else "N/A")
                market_cap = rec.get("market_cap")
                cap_str = f"${market_cap/1e9:.2f}B" if market_cap and market_cap >= 1e9 else f"${market_cap/1e6:.2f}M" if market_cap else "N/A"
                st.metric(t("metric.mcap", lang), cap_str)

            score_cols = st.columns(3)
            with score_cols[0]:
                st.metric(t("risk.model_score", lang), f"{rec.get('total_score', 0):.1f}")
            with score_cols[1]:
                st.metric(t("risk.penalty", lang), f"-{rec.get('risk_penalty', 0):.0f}")
            with score_cols[2]:
                st.metric(t("risk.selection_score", lang), f"{rec.get('risk_adjusted_score', rec.get('total_score', 0)):.1f}")
            sentiment = rec.get("sentiment", {})
            if sentiment.get("composite_score") is not None:
                st.caption(t(
                    "recommend.sentiment_summary",
                    lang,
                    score=sentiment["composite_score"],
                    label=t(f"sentiment.{sentiment.get('composite_label', 'neutral')}", lang),
                    modifier=rec.get("sentiment_modifier_pct", 0),
                ))

            # Relative strength
            try:
                from agents.trading_strategies import _get_relative_strength as _rel_str
                _rs_data = _rel_str(rec["ticker"], rec.get("sector"))
                if _rs_data.get("vs_spy_pct") is not None:
                    _v = _rs_data["vs_spy_pct"]
                    _c = "color:#22c55e" if _v > 0 else "color:#ef4444"
                    st.markdown(
                        f"<span style='font-size:0.7rem;{_c};'> vs SPY: {'+' if _v > 0 else ''}{_v:.1f}%"
                        + (f" | vs 板塊: {'+' if _rs_data.get('vs_sector_pct', 0) > 0 else ''}{_rs_data.get('vs_sector_pct', 0):.1f}%"
                           if _rs_data.get("vs_sector_pct") is not None else "")
                        + "</span>",
                        unsafe_allow_html=True,
                    )
            except Exception:
                pass

            dq = rec.get("data_quality", {})
            fetched = dq.get("fetched_at", rec.get("fetched_at"))
            if fetched:
                try:
                    from datetime import datetime, timezone, timedelta
                    ft_str = str(fetched)[:19]
                    utc_time = datetime.strptime(ft_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    hk_time = utc_time.astimezone(timezone(timedelta(hours=8)))
                    fetched = hk_time.strftime("%Y-%m-%d %H:%M:%S HKT")
                except Exception:
                    pass
            st.caption(t("recommend.fetched_at", lang, time=fetched))
            source = dq.get("source") or rec.get("data_source") or "unknown"
            if dq.get("source_type") == "seed" or any(component.get("source") == "seed_data" for component in dq.get("source_components", [])):
                st.warning(t("provenance.seed_warning", lang))
            else:
                st.caption(f"Data source: {source}")
            risk = rec.get("risk_metrics", {})
            if risk.get("available"):
                level = risk.get("risk_level", "unknown").upper()
                st.caption(
                    f"Risk: {level} | Vol {risk.get('annual_volatility_pct', 'N/A')}% | "
                    f"VaR 95% {risk.get('var_95_daily_pct', 'N/A')}% | Beta {risk.get('beta', 'N/A')}"
                )
            else:
                st.caption("Risk: unavailable (insufficient price history)")
            avail = dq.get("metrics_available", 0)
            total_m = dq.get("metrics_total", 6)
            ratio = avail / total_m if total_m > 0 else 0
            if ratio >= 0.8:
                conf = t("conf.high", lang)
            elif ratio >= 0.5:
                conf = t("conf.medium", lang)
            else:
                conf = t("conf.low", lang)
            st.caption(t("recommend.confidence", lang, level=conf))

            extra_cols = st.columns(4)
            items = [
                (t("metric.beta", lang), f"{rec.get('beta', 0):.2f}" if rec.get("beta") is not None else "N/A"),
                (t("metric.div_yield", lang), f"{rec.get('dividend_yield', 0)*100:.2f}%" if rec.get("dividend_yield") is not None else "N/A"),
                (t("metric.rating", lang), rec.get("rating_label") or "N/A"),
                (t("metric.inst_own", lang), f"{rec.get('held_percent_institutions', 0)*100:.1f}%" if rec.get("held_percent_institutions") is not None else "N/A"),
            ]
            for extra_col, (lbl, val) in zip(extra_cols, items):
                extra_col.metric(lbl, val)

            use_llm = st.session_state.get("use_llm", False)
            if use_llm and rec.get("llm_reasoning"):
                llm_score = rec.get("llm_score")
                fund_score = rec.get("growth_score")
                key_signal = rec.get("llm_key_signal", "neutral")
                sig_emoji = {"bullish": "", "neutral": "", "bearish": ""}.get(key_signal, "")
                sig_label = t(f"llm.signal_{key_signal}", lang)
                st.subheader(t("llm.analysis", lang))

                score_col1, score_col2, score_col3 = st.columns(3)
                with score_col1:
                    st.metric(t("recommend.score", lang), f"{rec.get('total_score', 0):.0f}")
                with score_col2:
                    if fund_score is not None:
                        st.metric(t("llm.fundamental_score", lang), f"{fund_score:.0f}")
                with score_col3:
                    if llm_score is not None:
                        st.metric(t("llm.score", lang), f"{llm_score:.0f}/100")

                if fund_score is not None and llm_score is not None:
                    divergence = abs(fund_score - llm_score)
                    if divergence > 25:
                        st.warning(t("llm.divergence", lang, d=divergence))

                st.markdown(f"{sig_emoji} **{t('llm.signal', lang)}**: {sig_label}")
                tech_summary = rec.get("llm_technical_summary", "")
                if tech_summary:
                    st.markdown(f"**{t('llm.technical_summary', lang)}**: {tech_summary}")

            ticker = rec["ticker"]
            price = rec.get("price")
            pcol1, pcol2 = st.columns(2)
            with pcol1:
                if st.button(t("price_suggest.btn", lang), key=f"price_btn_{ticker}", width="stretch"):
                    with st.spinner(t("price_suggest.loading", lang)):
                        from agents.technical_analyzer import compute_technical_indicators
                        tech_data = compute_technical_indicators(ticker)
                        news_data = rec.get("news", [])
                        try:
                            from agents.llm_agent import suggest_price_targets
                            result = suggest_price_targets(ticker, tech_data, news_data, price or 0, lang)
                            if "error" in result:
                                from agents.data_fetcher import suggest_price_fallback
                                result = suggest_price_fallback(ticker, tech_data, price or 0)
                        except Exception:
                            from agents.data_fetcher import suggest_price_fallback
                            result = suggest_price_fallback(ticker, tech_data, price or 0)
                        st.session_state[f"price_result_{ticker}"] = result
            with pcol2:
                if st.button(t("options.btn", lang), key=f"opt_btn_{ticker}", width="stretch"):
                    with st.spinner(t("options.loading", lang)):
                        from agents.technical_analyzer import compute_technical_indicators
                        tech_data = compute_technical_indicators(ticker)
                        news_data = rec.get("news", [])
                        try:
                            from agents.llm_agent import suggest_options_strategy
                            result = suggest_options_strategy(ticker, tech_data, price or 0, news_data, lang)
                            if "error" in result:
                                from agents.data_fetcher import suggest_options_fallback
                                result = suggest_options_fallback(ticker, tech_data, price or 0)
                        except Exception:
                            from agents.data_fetcher import suggest_options_fallback
                            result = suggest_options_fallback(ticker, tech_data, price or 0)
                        st.session_state[f"opt_result_{ticker}"] = result

            price_result = st.session_state.get(f"price_result_{ticker}")
            if price_result:
                if "error" in price_result:
                    st.error(f" {price_result['error']}")
                else:
                    st.markdown(f"**{t('price_suggest.btn', lang)}**")
                    pc1, pc2, pc3, pc4 = st.columns(4)
                    buy = price_result.get("buy_price")
                    sell = price_result.get("sell_price")
                    stop = price_result.get("stop_loss")
                    conf = price_result.get("confidence")
                    with pc1:
                        st.metric(t("price_suggest.buy", lang), f"${buy:.2f}" if buy else t("price_suggest.na", lang))
                    with pc2:
                        st.metric(t("price_suggest.sell", lang), f"${sell:.2f}" if sell else "—")
                    with pc3:
                        st.metric(t("price_suggest.stop", lang), f"${stop:.2f}" if stop else "—")
                    with pc4:
                        st.metric(t("price_suggest.confidence", lang), f"{conf:.0f}%" if conf else "—")
                    reason = price_result.get("reasoning", "")
                    if reason:
                        st.markdown(reason)
                    st.markdown("<hr style='margin:0.5rem 0;'>", unsafe_allow_html=True)

            opt_result = st.session_state.get(f"opt_result_{ticker}")
            if opt_result:
                if "error" in opt_result:
                    st.error(f" {opt_result['error']}")
                else:
                    st.markdown(f"**{t('options.btn', lang)}**")
                    ot = opt_result.get("option_type", "none")
                    if ot == "call":
                        ot_label = t("options.call", lang)
                    elif ot == "put":
                        ot_label = t("options.put", lang)
                    else:
                        ot_label = t("options.none", lang)
                    oc1, oc2, oc3, oc4 = st.columns(4)
                    with oc1:
                        st.metric(t("options.type", lang), ot_label)
                    with oc2:
                        strike = opt_result.get("strike_price")
                        st.metric(t("options.strike", lang), f"${strike:.2f}" if strike else "—")
                    with oc3:
                        expiry = opt_result.get("expiration")
                        st.metric(t("options.expiry", lang), expiry or "—")
                    with oc4:
                        sup = opt_result.get("key_support")
                        res = opt_result.get("key_resistance")
                        st.metric(t("options.support", lang), f"${sup:.2f}" if sup else "—")
                    oreason = opt_result.get("reasoning", "")
                    if oreason:
                        st.markdown(oreason)
                    st.markdown("<hr style='margin:0.5rem 0;'>", unsafe_allow_html=True)

            # --- Trading Strategy ---
            strategy_key = f"strat_result_{ticker}"
            if st.button(t("strategy.btn", lang), key=f"strat_btn_{ticker}", width="stretch"):
                with st.spinner(t("strategy.loading", lang)):
                    from agents.trading_strategies import recommend_strategies
                    from agents.technical_analyzer import compute_technical_indicators
                    tech_data = compute_technical_indicators(ticker)
                    result = recommend_strategies(ticker, rec, tech_data)
                    try:
                        from agents.llm_agent import check_llm_health, suggest_trading_strategy
                        if check_llm_health():
                            opts = result.get("options_data", {})
                            news_data = rec.get("news", [])
                            llm_r = suggest_trading_strategy(
                                ticker, rec, tech_data, price or 0, opts, news_data, lang
                            )
                            if "error" not in llm_r:
                                result["llm"] = llm_r
                    except Exception:
                        pass
                    st.session_state[strategy_key] = {**result, "_tech_data": tech_data}

            strat_result = st.session_state.get(strategy_key)
            if strat_result:
                if "error" in strat_result:
                    st.error(f" {strat_result['error']}")
                else:
                    cal = strat_result.get("calendar", {})
                    if cal.get("has_conflict"):
                        st.warning(cal["warning"])
                    regime = strat_result.get("regime", {})
                    regime_str = f" 市況: {regime.get('trend', '?')} / {regime.get('volatility', '?')}波動"
                    rs = strat_result.get("relative_strength", {})
                    if rs.get("vs_spy_pct") is not None:
                        sp = rs["vs_spy_pct"]
                        regime_str += f" | vs SPY: {'+' if sp > 0 else ''}{sp:.1f}%"
                    if rs.get("vs_sector_pct") is not None:
                        sp2 = rs["vs_sector_pct"]
                        regime_str += f" | vs 板塊: {'+' if sp2 > 0 else ''}{sp2:.1f}%"
                    st.caption(regime_str)

                    llm_data = strat_result.get("llm")
                    top = strat_result.get("top", {})
                    rankings = strat_result.get("rankings", [])

                    if llm_data and llm_data.get("top_strategy"):
                        st.markdown(f"** {t('strategy.llm_title', lang)}**")
                        llm_sid = llm_data.get("top_strategy", "")
                        llm_name = t(f"strategy.{llm_sid}", lang) if llm_sid else ""
                        st.info(f"**{llm_name}** — {llm_data.get('reasoning', '')}")
                        conf = llm_data.get("confidence", {})
                        if conf:
                            cc1, cc2, cc3 = st.columns(3)
                            cc1.metric("技術信心", f"{conf.get('technical', 0)}%")
                            cc2.metric("基本面信心", f"{conf.get('fundamental', 0)}%")
                            cc3.metric("型態品質", f"{conf.get('setup_quality', 0)}%")
                        entry = llm_data.get("entry", {})
                        stop = llm_data.get("stop_loss", {})
                        targets = llm_data.get("targets", [])
                        rr = llm_data.get("risk_reward")
                        if entry.get("price"):
                            st.markdown(f"**{t('strategy.entry', lang)}**: ${entry['price']:.2f} ({entry.get('type', 'limit')})")
                        if targets:
                            tgt_str = " → ".join([f"${t['price']:.2f} ({t.get('size_pct', 100)}%)" for t in targets])
                            st.markdown(f"**{t('strategy.target', lang)}**: {tgt_str}")
                        if stop.get("price"):
                            st.markdown(f"**{t('strategy.stop', lang)}**: ${stop['price']:.2f} ({stop.get('type', 'fixed')})")
                        if rr:
                            st.markdown(f"**R:R** = {rr:.1f}")
                        sc = llm_data.get("scenario", {})
                        if sc:
                            with st.expander(t("strategy.scenario", lang)):
                                for label_key in ["best", "base", "worst"]:
                                    v = sc.get(label_key)
                                    if v:
                                        st.markdown(f"**{t(f'strategy.{label_key}', lang)}**: {v}")
                        st.markdown("<hr style='margin:0.5rem 0;'>", unsafe_allow_html=True)

                    if rankings:
                        st.markdown(f"** {t('strategy.title', lang)}**")
                        for rank_entry in rankings:
                            sid = rank_entry.get("id", "")
                            sname = t(rank_entry.get("name_key", ""), lang)
                            sscore = rank_entry.get("score", 0)
                            sd = t(rank_entry.get("difficulty_key", ""), lang)
                            sh = rank_entry.get("time_horizon", "")

                            risk_map = {"trend_following": "strategy.risk_low", "mean_reversion": "strategy.risk_mid",
                                        "breakout_momentum": "strategy.risk_mid", "value_entry": "strategy.risk_low",
                                        "income_defensive": "strategy.risk_low"}
                            risk_label = t(risk_map.get(sid, "strategy.risk_mid"), lang) if sid else ""

                            bar_color = "#22c55e" if sscore >= 70 else "#eab308" if sscore >= 40 else "#ef4444"
                            st.markdown(
                                f"<div style='display:flex;justify-content:space-between;"
                                f"align-items:center;padding:2px 0;'>"
                                f"<span><b>{sname}</b> | {sh} | {sd} | {risk_label}</span>"
                                f"<span style='font-weight:bold;color:{bar_color};'>{sscore}%</span>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )
                            if sscore > 0 and rank_entry.get("reasoning"):
                                st.caption(rank_entry["reasoning"])

                        st.markdown("<hr style='margin:0.5rem 0;'>", unsafe_allow_html=True)
                        st.markdown(f"** {t('strategy.top_title', lang)}**")

                        if top:
                            if top.get("entry_price"):
                                stop_p = top.get("stop_loss")
                                st.info(
                                    f"**{t('strategy.entry', lang)}**: ${top['entry_price']:.2f}"
                                    + (f" → **{t('strategy.target', lang)}**: ${top['targets'][0]['price']:.2f}" if top.get('targets') else "")
                                    + (f" | **{t('strategy.stop', lang)}**: ${stop_p:.2f}" if stop_p else "")
                                    + (f" | **R:R** = {top.get('risk_reward', '?')}" if top.get('risk_reward') else "")
                                )
                            if top.get("targets"):
                                tgt_line = "  |  ".join([f" ${t['price']:.2f} (出 {t['size_pct']}%)" for t in top["targets"]])
                                st.markdown(tgt_line)
                            pos_size = top.get("max_loss_usd", 0)
                            shares = top.get("shares", 0)
                            if pos_size or shares:
                                st.caption(f" {t('strategy.max_loss', lang)}: ${pos_size:.0f} ({t('strategy.shares', lang)} {shares})")
                            conf_t = top.get("technical_confidence")
                            conf_f = top.get("fundamental_confidence")
                            conf_s = top.get("setup_quality")
                            if any([conf_t, conf_f, conf_s]):
                                r1, r2, r3 = st.columns(3)
                                r1.metric(" 技術信心", f"{conf_t}%" if conf_t else "—")
                                r2.metric(" 基本面信心", f"{conf_f}%" if conf_f else "—")
                                r3.metric(" 型態品質", f"{conf_s}%" if conf_s else "—")
                            sc = top.get("scenario", {})
                            if sc:
                                with st.expander(t("strategy.scenario", lang)):
                                    for label_key in ["best", "base", "worst"]:
                                        v = sc.get(label_key)
                                        if v:
                                            st.markdown(f"**{t(f'strategy.{label_key}', lang)}**: {v}")
                            reason = top.get("reasoning", "")
                            if reason:
                                st.caption(reason)
                            fail_warning = _strategy_fail_warning(top.get("id", ""), strat_result.get("_tech_data", {}))
                            if fail_warning:
                                st.caption(fail_warning)
                            if st.button(t("strategy.copy", lang), key=f"copy_strat_{ticker}"):
                                plan = _build_trade_plan_text(top, lang)
                                st.code(plan, language="text")

            reasoning = rec.get("reasoning", "")
            if reasoning:
                st.subheader(t("recommend.reasoning", lang))
                st.markdown(reasoning)

            score_details = rec.get("score_details", {})
            if score_details:
                st.subheader(t("recommend.details", lang))
                sd_list = [
                    {t("recommend.metric", lang): k, t("recommend.value", lang): v.get("value", ""),
                     t("recommend.score_label", lang): v.get("score", "")}
                    for k, v in score_details.items()
                ]
                st.dataframe(pd.DataFrame(sd_list), hide_index=True, width="stretch")

            news = rec.get("news", [])
            if news:
                st.subheader(t("recommend.news", lang))
                for n in news:
                    emoji = "" if n.get("sentiment") == "positive" else "" if n.get("sentiment") == "negative" else ""
                    st.markdown(f"{emoji} **{n.get('title', '')}**")
                    if n.get("summary"):
                        st.markdown(n["summary"])
                    st.caption(f"{n.get('publisher', '')} | [{t('news.view', lang)}]({n.get('link', '')})")

            st.subheader(t("recommend.sec", lang))
            if sec_url:
                st.markdown(t("recommend.sec_file", lang, form=form_type, date=filing_date))
                st.markdown(f"[{t('recommend.sec_view', lang)}]({sec_url})")
            else:
                st.markdown(f"*{t('recommend.sec_fail', lang)}*")

            if insights:
                llm_summary = insights.get("llm_summary")
                if llm_summary and llm_summary.get("summary") and llm_summary["summary"] != "N/A":
                    st.markdown(f"** {_sector_name(rec.get('sector', ''), lang)} 摘要**")
                    st.info(llm_summary["summary"])
                    if llm_summary.get("key_positives"):
                        for kp in llm_summary["key_positives"]:
                            st.markdown(f" {kp}")
                    if llm_summary.get("key_risks"):
                        for kr in llm_summary["key_risks"]:
                            st.markdown(f" {kr}")
                else:
                    sections = insights.get("sections") or {}
                    if sections:
                        for section_title, content in sections.items():
                            if section_title != "ticker":
                                with st.container():
                                    st.markdown(f"**{section_title}**")
                                    st.text(content[:800])
                                    st.caption(t("recommend.truncated", lang))
                    else:
                        st.markdown(f"*{t('recommend.sec_na', lang)}*")
            else:
                st.markdown(f"*{t('recommend.sec_na', lang)}*")

            chart_visible_key = f"chart_visible_{ticker}"
            if st.button(t("chart.show", lang), key=f"chart_btn_{ticker}", width="stretch"):
                st.session_state[chart_visible_key] = not st.session_state.get(chart_visible_key, False)

            if st.session_state.get(chart_visible_key, False):
                _render_kline_chart(ticker, lang, f"recommend_chart_{ticker}", rec.get("price"))


def _render_deep_research_result(
    ticker: str, result: Dict[str, Any], lang: str, force_refresh: bool = False,
) -> None:
    if result.get("error"):
        st.error(f"{ticker}: {result['error']}")
        return
    short = result.get("short_term", {})
    long = result.get("long_term", {})
    avoid = result.get("avoid", {})
    strategy = result.get("strategy", {})
    trade = result.get("trade_plan", {})
    option = result.get("options_plan", {})
    technical = result.get("technical", {})
    validation = result.get("validation", {})
    with st.expander(f"{ticker} — {t('deep.research', lang)}", expanded=True):
        if result.get("enrichment_errors"):
            st.warning(t("deep.partial_enrichment", lang))
        cols = st.columns(4)
        cols[0].metric(t("deep.short", lang), f"{short.get('score', 0):.1f}", short.get("view", "neutral").upper())
        cols[1].metric(t("deep.long", lang), f"{long.get('score', 0):.1f}", long.get("view", "neutral").upper())
        cols[2].metric(t("deep.stance", lang), str(trade.get("stance", "neutral")).upper())
        cols[3].metric(t("deep.action", lang), str(trade.get("action", "watch")).replace("_", " ").upper())

        if avoid.get("reasons"):
            reason_codes = avoid.get("reason_codes", [])
            reason_lines = (
                [t(f"deep.avoid_{code}", lang) for code in reason_codes]
                if reason_codes else [str(reason) for reason in avoid.get("reasons", [])]
            )
            st.warning(
                "**" + t("deep.avoid_reasons", lang) + "**\n\n" +
                "\n".join(f"- {reason}" for reason in reason_lines)
            )
        decision_basis = trade.get("decision_basis", {})
        explanation_cols = st.columns(2)
        with explanation_cols[0]:
            st.markdown("**" + t("deep.why_stance", lang, stance=str(trade.get("stance", "neutral")).upper()) + "**")
            st.write(t(
                f"deep.stance_reason_{trade.get('stance', 'neutral')}", lang,
                short=decision_basis.get("short_score", short.get("score", 0)),
                long=decision_basis.get("long_score", long.get("score", 0)),
                technical=decision_basis.get("technical_score", 0) or 0,
            ))
        with explanation_cols[1]:
            action_key = str(trade.get("action", "watch"))
            st.markdown("**" + t("deep.why_action", lang, action=action_key.replace("_", " ").upper()) + "**")
            st.write(t(f"deep.action_reason_{action_key}", lang))

        quote_time = technical.get("price_quote_time") or t("deep.unavailable", lang)
        stale = f" · {t('deep.stale', lang)}" if technical.get("price_stale") else ""
        st.caption(
            f"{t('deep.live_quote', lang)}: {_currency(technical.get('price'))} · "
            f"{technical.get('price_session', t('deep.unavailable', lang))} · {quote_time}{stale}"
        )
        provenance = result.get("provenance", {})
        fundamental_source = provenance.get("fundamentals", {})
        technical_source = provenance.get("technical", {})
        if fundamental_source:
            source_flags = []
            if fundamental_source.get("from_cache"):
                source_flags.append(t("provenance.cached", lang))
            if fundamental_source.get("is_fallback"):
                source_flags.append(t("provenance.fallback", lang))
            if fundamental_source.get("stale"):
                source_flags.append(t("provenance.stale", lang))
            st.caption(t(
                "provenance.fundamentals", lang,
                source=fundamental_source.get("source", "N/A"),
                as_of=_format_timestamp(fundamental_source.get("as_of")),
                status=" · ".join(source_flags) or t("provenance.live", lang),
            ))
            if fundamental_source.get("source_type") == "seed" or any(
                component.get("source") == "seed_data" for component in fundamental_source.get("source_components", [])
            ):
                st.warning(t("provenance.seed_warning", lang))
        if technical_source:
            st.caption(t(
                "provenance.technical", lang,
                source=technical_source.get("source", "N/A"),
                as_of=_format_timestamp(technical_source.get("as_of")),
                period=technical_source.get("period", "N/A"),
            ))

        st.markdown("**" + t("deep.execution_plan", lang) + "**")
        entry = trade.get("entry_zone", {})
        targets = trade.get("targets", [])
        bearish_plan = trade.get("stance") == "bearish"
        neutral_plan = trade.get("stance") == "neutral"
        if bearish_plan:
            entry_label, confirmation_label = t("deep.short_entry_zone", lang), t("deep.bearish_confirmation", lang)
            stop_label, targets_label = t("deep.cover_stop", lang), t("deep.downside_targets", lang)
        elif neutral_plan:
            entry_label, confirmation_label = t("deep.watch_range", lang), t("deep.buy_trigger", lang)
            stop_label, targets_label = t("deep.invalidation_price", lang), t("deep.potential_targets", lang)
        else:
            entry_label, confirmation_label = t("deep.buy_zone", lang), t("deep.buy_trigger", lang)
            stop_label, targets_label = t("deep.sell_stop", lang), t("deep.take_profit_prices", lang)
        with st.container(key=f"execution_plan_{ticker}"):
            plan_cols = st.columns(4)
            plan_cols[0].metric(entry_label, _price_range(entry.get("low"), entry.get("high")))
            plan_cols[1].metric(confirmation_label, _currency(trade.get("confirmation_price")))
            plan_cols[2].metric(stop_label, _currency(trade.get("stop_loss")))
            plan_cols[3].metric(targets_label, " / ".join(_currency(value) for value in targets) or "N/A")
        if bearish_plan:
            st.warning(t("deep.bearish_stop_note", lang))
        st.caption(f"{t('deep.execution_window', lang)}: {trade.get('execution_window', 'N/A')}")
        render_saved_plan_controls(ticker, result, lang, get_account_repository())
        timing = result.get("timing", {}).get("stages", {})
        if timing:
            st.caption(t(
                "deep.source_timing", lang,
                fundamental=timing.get("fundamental", {}).get("duration_ms", 0),
                technical=timing.get("technical", {}).get("duration_ms", 0),
                market=timing.get("market_data", {}).get("duration_ms", 0),
                llm=timing.get("strategy", {}).get("duration_ms", 0),
            ))
        entry_reference = trade.get("entry_reference")
        risk_per_share = trade.get("risk_per_share")
        account_risk = float(st.session_state.get("picks_account_capital", 100000)) * float(st.session_state.get("picks_risk_budget_pct", 1.0)) / 100
        shares = int(account_risk / risk_per_share) if risk_per_share else 0
        with st.container(key=f"risk_context_{ticker}"):
            context_cols = st.columns(4)
            context_cols[0].metric(t("deep.horizon", lang), short.get("horizon", "N/A"))
            rr_targets = " / ".join(_currency(target) for target in targets[:2]) or "N/A"
            context_cols[1].metric(t("deep.risk_reward_targets", lang), rr_targets)
            context_cols[2].metric(t("deep.risk_per_share", lang), _currency(risk_per_share))
            context_cols[3].metric(t("deep.max_shares", lang), str(shares) if shares else "N/A")
        if risk_per_share and entry_reference and targets:
            st.caption(t(
                "deep.rr_exact", lang,
                stop=_currency(trade.get("stop_loss")),
                entry=_currency(entry_reference),
                risk=_currency(risk_per_share),
                target1=_currency(targets[0]),
                target2=_currency(targets[1]) if len(targets) > 1 else "N/A",
            ))

        chart_visible_key = f"deep_chart_visible_{ticker}"
        if st.button(t("chart.show", lang), key=f"deep_chart_button_{ticker}", width="stretch"):
            st.session_state[chart_visible_key] = not st.session_state.get(chart_visible_key, False)
        if st.session_state.get(chart_visible_key, False):
            _render_kline_chart(
                ticker, lang, f"deep_chart_{ticker}", technical.get("price"), trade_plan=trade,
            )

        section = st.segmented_control(
            t("deep.detail_section", lang),
            options=["validation", "sessions", "options", "evidence", "reasoner"],
            format_func=lambda key: t(f"deep.section_{key}", lang),
            default="validation",
            key=f"picks_detail_{ticker}",
        ) or "sessions"

        if section == "validation":
            if not validation.get("available"):
                if validation.get("reason") == "not_run":
                    st.info(t("validation.on_demand", lang))
                    if st.button(t("validation.run", lang), key=f"validation_run_{ticker}", width="stretch"):
                        from backtesting.our_picks import run_our_picks_validation

                        with st.spinner(t("validation.running", lang)):
                            validation = run_our_picks_validation(ticker, force_refresh=force_refresh)
                            result["validation"] = validation
                            st.session_state.picks_results[ticker]["validation"] = validation
                        st.rerun()
                else:
                    st.warning(t("validation.unavailable", lang, reason=validation.get("reason", "N/A")))
            else:
                stance_result = validation.get("by_stance", {}).get(trade.get("stance"), {})
                confidence = validation.get("historical_evidence_grade") or stance_result.get("confidence", {})
                effective = validation.get("effective_sample_size", {})
                intervals = validation.get("confidence_intervals", {})
                aligned = validation.get("benchmark_aligned_count", 0)
                entered = validation.get("entered_count", 0)
                validation_cols = st.columns(4)
                validation_cols[0].metric(t("validation.evidence_grade", lang), t(f"validation.{confidence.get('level', 'insufficient')}", lang))
                validation_cols[1].metric(t("validation.effective_samples", lang), f"{effective.get('effective', 0):g} / {effective.get('raw', entered)}")
                validation_cols[2].metric(t("validation.win_rate", lang), _percent(stance_result.get("win_rate_pct")))
                validation_cols[3].metric(t("validation.avg_return", lang), _percent(stance_result.get("average_return_pct")))
                quality_cols = st.columns(4)
                quality_cols[0].metric(t("validation.excess_return", lang), _percent(stance_result.get("average_excess_return_pct")))
                quality_cols[1].metric(t("validation.max_drawdown", lang), _percent(validation.get("max_drawdown_pct")))
                quality_cols[2].metric(t("validation.target1_rate", lang), _percent(stance_result.get("target1_hit_rate_pct")))
                quality_cols[3].metric(t("validation.stop_rate", lang), _percent(stance_result.get("stop_hit_rate_pct")))
                st.caption(t(
                    "validation.current_stance", lang,
                    stance=str(trade.get("stance", "neutral")).upper(),
                    samples=stance_result.get("entered_count", 0),
                    win=_percent(stance_result.get("win_rate_pct")),
                    avg=_percent(stance_result.get("average_return_pct")),
                    excess=_percent(stance_result.get("average_excess_return_pct")),
                ))
                st.caption(t("validation.method", lang, days=validation.get("holding_days", 20)))
                st.warning(t("validation.limitations", lang))
                st.caption(t(
                    "validation.statistics_summary", lang,
                    aligned=aligned,
                    entered=entered,
                    seed=validation.get("metadata", {}).get("bootstrap_seed", "N/A"),
                    iterations=validation.get("metadata", {}).get("bootstrap_iterations", "N/A"),
                ))
                ci_rows = []
                for key, label_key in (
                    ("net_return_pct", "validation.avg_return"),
                    ("directional_alpha_pct", "validation.excess_return"),
                    ("win_rate_pct", "validation.win_rate"),
                ):
                    interval = intervals.get(key, {})
                    if interval.get("available"):
                        ci_rows.append({
                            t("validation.metric", lang): t(label_key, lang),
                            t("validation.estimate", lang): f"{interval['estimate']:.2f}%",
                            t("validation.ci_95", lang): f"{interval['lower']:.2f}% – {interval['upper']:.2f}%",
                        })
                if ci_rows:
                    st.dataframe(pd.DataFrame(ci_rows), hide_index=True, width="stretch")
                sensitivity = validation.get("cost_sensitivity", [])
                if sensitivity:
                    with st.expander(t("validation.cost_sensitivity", lang)):
                        st.dataframe(pd.DataFrame(sensitivity), hide_index=True, width="stretch")
        elif section == "sessions":
            sessions = result.get("session_ranges", {}).get("sessions", {})
            session_rows = []
            dates = set()
            for key in ("overnight", "pre_market", "regular", "after_hours"):
                session = sessions.get(key, {})
                if session.get("date"):
                    dates.add(session["date"])
                coverage = "N/A"
                if session.get("start_time") and session.get("end_time"):
                    coverage = f"{pd.Timestamp(session['start_time']).strftime('%H:%M')}–{pd.Timestamp(session['end_time']).strftime('%H:%M')} ET"
                session_rows.append({
                    t("deep.session", lang): session.get("name", key.replace("_", " ").title()),
                    t("deep.date", lang): session.get("date", "N/A"),
                    t("deep.coverage", lang): coverage,
                    t("deep.low", lang): _currency(session.get("low")),
                    t("deep.high", lang): _currency(session.get("high")),
                    t("deep.last", lang): _currency(session.get("last")),
                    t("deep.bars", lang): session.get("bars", 0) if session.get("available") else "N/A",
                })
            if len(dates) > 1:
                st.warning(t("deep.mixed_session_dates", lang))
            st.dataframe(pd.DataFrame(session_rows), hide_index=True, width="stretch")
            st.caption(t("deep.session_note", lang))
        elif section == "options":
            if option.get("action") in {"buy_to_open", "research_only"}:
                contract = option.get("contract", {})
                expiry = option.get("expiry")
                dte = max(0, (pd.Timestamp(expiry).date() - pd.Timestamp.now().date()).days) if expiry else None
                option_cols = st.columns(4)
                option_cols[0].metric(t("deep.contract", lang), contract.get("contract_symbol") or f"{option.get('option_type', '').upper()} {contract.get('strike', 'N/A')}")
                option_cols[1].metric(t("deep.expiry", lang), f"{expiry} · {dte} DTE" if dte is not None else "N/A")
                option_cols[2].metric(t("deep.bid_ask", lang), f"{_currency(contract.get('bid'))} / {_currency(contract.get('ask'))}")
                option_cols[3].metric(
                    t("deep.total_debit", lang),
                    _currency(float(option.get("max_entry_premium", contract.get("ask") or 0)) * 100),
                )
                option_rows = [{
                    t("deep.mid_spread", lang): f"{_currency(contract.get('mid'))} / {contract.get('spread_pct', 'N/A')}%",
                    t("deep.iv", lang): f"{float(contract.get('implied_volatility')) * 100:.1f}%" if contract.get("implied_volatility") is not None else "N/A",
                    t("deep.open_interest", lang): contract.get("open_interest", 0),
                    t("deep.volume", lang): contract.get("volume", 0),
                    t("deep.max_entry", lang): _currency(option.get("max_entry_premium")),
                    t("deep.premium_stop", lang): _currency(option.get("stop_premium")),
                    t("deep.premium_targets", lang): " / ".join(_currency(value) for value in option.get("take_profit_premiums", [])),
                    t("deep.underlying_invalidation", lang): _currency(option.get("underlying_invalidation")),
                }]
                st.dataframe(pd.DataFrame(option_rows), hide_index=True, width="stretch")
                if option.get("delayed_data"):
                    st.warning(t("deep.options_delayed_source", lang, source=option.get("data_source", "Cboe")))
                    st.info(t("deep.options_research_only", lang))
                    if option.get("snapshot_stale"):
                        st.warning(t("deep.options_prior_session", lang))
                st.caption(option.get("exit_rule", ""))
                trace = option.get("agent_trace", [])
                if trace:
                    with st.expander(t("deep.option_agent_trace", lang), expanded=False):
                        trace_rows = []
                        for stage in trace:
                            gaps = stage.get("data_gaps", [])
                            warnings = stage.get("warnings", [])
                            trace_rows.append({
                                t("deep.option_stage", lang): t(f"option.stage.{stage.get('stage')}", lang),
                                t("deep.option_stage_status", lang): stage.get("status", "N/A"),
                                t("deep.option_stage_as_of", lang): stage.get("as_of") or "N/A",
                                t("deep.option_stage_notes", lang): ", ".join([*warnings, *gaps]) or "-",
                            })
                        st.dataframe(pd.DataFrame(trace_rows), hide_index=True, width="stretch")
            else:
                error_code = option.get("error_code")
                if error_code == "rate_limit":
                    st.warning(t("deep.options_rate_limited", lang, minutes=5))
                elif error_code == "provider_incomplete":
                    st.warning(t("deep.options_provider_incomplete", lang, minutes=5))
                elif error_code:
                    st.warning(t("deep.options_provider_unavailable", lang))
                else:
                    st.info(f"{t('deep.no_options_trade', lang)}: {option.get('reason', 'N/A')}")
        elif section == "evidence":
            tech_cols = st.columns(4)
            tech_cols[0].metric("RSI 14", _number(technical.get("rsi_14")))
            tech_cols[1].metric("EMA 9 / 21", _pair(technical.get("ema_9"), technical.get("ema_21")))
            tech_cols[2].metric("ADX 14", _number(technical.get("adx_14")))
            tech_cols[3].metric("ATR 14", _number(technical.get("atr_14")))
            sec = result.get("sec_evidence", {})
            st.markdown("**" + t("deep.sec_evidence", lang) + "**")
            if sec.get("available") and sec.get("url"):
                st.write(t(
                    "deep.sec_filing_meta", lang, form=sec.get("form_type") or "N/A",
                    filed=sec.get("filing_date") or "N/A", period=sec.get("report_date") or "N/A",
                ))
                st.link_button(t("deep.sec_view_filing", lang), sec["url"])
                for title, excerpt in list(((sec.get("insights") or {}).get("sections") or {}).items())[:3]:
                    st.markdown(f"**{title}**")
                    st.write(str(excerpt)[:800])
                st.caption(t("deep.sec_excerpt_note", lang))
                provenance = sec.get("provenance", {})
                st.caption(t(
                    "deep.sec_source_caption", lang,
                    accession=sec.get("accession_number") or "N/A",
                    fetched=_format_timestamp(provenance.get("fetched_at")),
                    status=t("provenance.cached", lang) if provenance.get("from_cache") else t("provenance.live", lang),
                ))
            else:
                status = sec.get("status", "unavailable")
                status = status if status in {"timeout", "not_found"} else "unavailable"
                st.info(t(f"deep.sec_{status}", lang))
        elif strategy.get("error"):
            error_code = strategy.get("error_code", "provider_error")
            st.warning(t(f"deep.llm_unavailable_{error_code}", lang))
            st.caption(t("deep.llm_quant_still_valid", lang))
        else:
            explanation = strategy.get("decision_explanation", {})
            if explanation:
                st.markdown("**" + t("deep.llm_decision_explanation", lang, label=str(explanation.get("label", "neutral")).upper()) + "**")
                if explanation.get("why"):
                    st.write(explanation["why"])
                view_explanations = explanation.get("view_explanations", {})
                if view_explanations.get("short_term"):
                    st.write(f"**{t('deep.short', lang)}:** {view_explanations['short_term']}")
                if view_explanations.get("long_term"):
                    st.write(f"**{t('deep.long', lang)}:** {view_explanations['long_term']}")
                for key, title_key in (
                    ("supporting_evidence", "deep.llm_supporting_evidence"),
                    ("counter_evidence", "deep.llm_counter_evidence"),
                    ("change_conditions", "deep.llm_change_conditions"),
                ):
                    if explanation.get(key):
                        st.markdown("**" + t(title_key, lang) + "**")
                        for item in explanation[key]:
                            st.markdown(f"- {item}")
            st.write(strategy.get("reasoning", ""))
            scenario = strategy.get("scenario", {})
            if scenario:
                st.write(f"{t('deep.base_case', lang)}: {scenario.get('base', 'N/A')}")

        st.caption(t("deep.disclaimer", lang))


def _number(value: Any, decimals: int = 2) -> str:
    return f"{value:.{decimals}f}" if isinstance(value, (int, float)) else "N/A"


def _pair(first: Any, second: Any) -> str:
    return f"{_number(first)} / {_number(second)}"


def _currency(value: Any) -> str:
    return f"${value:,.2f}" if isinstance(value, (int, float)) else "N/A"


def _percent(value: Any) -> str:
    return f"{value:.1f}%" if isinstance(value, (int, float)) else "N/A"


def _format_timestamp(value: Any) -> str:
    if not value:
        return "N/A"
    try:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        return timestamp.tz_convert("UTC").strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        return str(value)


def _price_range(low: Any, high: Any) -> str:
    return f"{_currency(low)} – {_currency(high)}" if low is not None and high is not None else "N/A"


def render_rankings_tab() -> None:
    lang = st.session_state.get("lang", "zh_tw")
    rankings = st.session_state.all_rankings
    if not rankings:
        st.warning(t("ranking.nodata", lang))
        return
    visible_tickers = set(st.session_state.get("scan_filter_tickers") or [])
    if visible_tickers:
        rankings = [row for row in rankings if row.get("ticker") in visible_tickers]
    df = pd.DataFrame(rankings).dropna(axis=1, how="all")
    if df.empty:
        st.warning(t("ranking.nodata", lang))
        return
    use_llm = st.session_state.get("use_llm", False)
    if use_llm and "llm_score" in df.columns:
        df = df.sort_values("llm_score", ascending=False)
        df["rank"] = range(1, len(df) + 1)
    highlight = st.checkbox(t("ranking.highlight", lang), value=True)

    def color_top_rows(row: pd.Series) -> List[str]:
        rank_value = row.get("rank", row.get(t("ranking.rank", lang)))
        if highlight and rank_value is not None and rank_value <= 5:
            return ["background-color: rgba(34,211,197,.12); color: #e7f7f5"] * len(row)
        return [""] * len(row)

    if "name" in df:
        universe = {stock["ticker"]: stock for stock in STOCK_UNIVERSE}
        df["name"] = df["ticker"].map(lambda ticker: _stock_name(universe.get(ticker, {"ticker": ticker}), lang))
    if "sector" in df:
        df["sector"] = df["sector"].map(lambda sector: _sector_name(sector, lang))
    column_labels = {
        "rank": t("ranking.rank", lang), "ticker": t("ranking.ticker", lang),
        "name": t("ranking.name", lang), "sector": t("ranking.sector", lang),
        "price": t("ranking.price", lang), "growth_score": t("ranking.growth_score", lang),
        "model_score": t("ranking.model_score", lang), "risk_penalty": t("ranking.risk_penalty", lang),
        "risk_adjusted_score": t("ranking.risk_adjusted_score", lang), "risk_level": t("ranking.risk_level", lang),
        "timing_score": t("ranking.timing_score", lang), "sentiment_score": t("ranking.sentiment_score", lang),
        "sentiment_modifier_pct": t("ranking.sentiment_modifier", lang),
        "dollar_volume_10d_avg": t("ranking.dollar_volume", lang),
        "dollar_volume_ratio": t("ranking.dollar_volume_ratio", lang),
        "revenue_growth": t("ranking.revenue_growth", lang), "eps_growth": t("ranking.eps_growth", lang),
        "profit_margin": t("ranking.profit_margin", lang), "peg": "PEG", "roe": "ROE",
        "debt_equity": t("ranking.debt_equity", lang), "llm_score": t("ranking.llm_score", lang),
        "technical_signal": t("ranking.technical_signal", lang),
    }
    styled = df.style.apply(color_top_rows, axis=1).set_table_styles([])
    styled.data.columns = [column_labels.get(column, column) for column in styled.data.columns]
    st.dataframe(styled, hide_index=True, width="stretch", height=600)


def render_compare_tab() -> None:
    lang = st.session_state.get("lang", "zh_tw")
    scored = st.session_state.scored_data
    if not scored:
        st.warning(t("compare.prompt", lang))
        return

    options = {}
    for s in scored:
        label = f"{s['ticker']} ({(s.get('longName') or '')[:20]})"
        options[s["ticker"]] = label

    selected = st.multiselect(
        t("compare.select", lang), options=sorted(options.keys()),
        format_func=lambda t_: options[t_],
        max_selections=4,
        default=[s["ticker"] for s in scored[:2]] if len(scored) >= 2 else None,
    )

    if len(selected) < 2:
        st.info(t("compare.min_select", lang))
        return

    compare_data = {s["ticker"]: s for s in scored if s["ticker"] in selected}
    metrics = [
        ("total_score", t("compare.indicator", lang), "{:.1f}"),
        ("price", t("metric.price", lang), "${:.2f}"),
        ("market_cap", t("metric.mcap", lang), lambda v: f"${v/1e9:.2f}B" if v and v >= 1e9 else f"${v/1e6:.2f}M" if v else "N/A"),
        ("revenue_growth", t("metric.revenue", lang), "{:.1f}%"),
        ("eps_growth", t("metric.eps", lang), "{:.1f}%"),
        ("profit_margin", t("metric.profit_margin", lang), "{:.1f}%"),
        ("roe", t("metric.roe", lang), "{:.1f}%"),
        ("peg", t("metric.peg", lang), "{:.2f}"),
        ("pe_ratio", t("metric.pe", lang), "{:.2f}"),
        ("debt_equity", t("metric.de", lang), "{:.2f}"),
        ("beta", t("metric.beta", lang), "{:.2f}"),
    ]

    rows: List[Dict[str, Any]] = []
    for key, label, fmt in metrics:
        row: Dict[str, Any] = {t("compare.indicator", lang): label}
        for ticker in selected:
            data = compare_data.get(ticker, {})
            val = data.get(key)
            if val is None:
                row[ticker] = "N/A"
            elif callable(fmt):
                row[ticker] = fmt(val)
            else:
                row[ticker] = fmt.format(val)
        rows.append(row)

    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    chart_metrics = [
        ("total_score", "Score"), ("revenue_growth", "Rev%"), ("eps_growth", "EPS%"),
        ("profit_margin", "Margin%"), ("roe", "ROE%"),
    ]
    chart_data: Dict[str, List[float]] = {t("compare.indicator", lang): [m[1] for m in chart_metrics]}
    for ticker in selected:
        chart_data[ticker] = []
    for key, _ in chart_metrics:
        for ticker in selected:
            data = compare_data.get(ticker, {})
            val = data.get(key)
            chart_data[ticker].append(val if val is not None else 0)

    dfc = pd.DataFrame(chart_data)
    dfm = dfc.melt(id_vars=[t("compare.indicator", lang)], var_name=t("compare.stock", lang), value_name="Value")
    chart = alt.Chart(dfm).mark_bar(opacity=0.85).encode(
        x=alt.X(f"{t('compare.indicator', lang)}:N", title=None),
        y=alt.Y("Value:Q", title=None),
        color=alt.Color(f"{t('compare.stock', lang)}:N", legend=alt.Legend(orient="top")),
        column=alt.Column(f"{t('compare.stock', lang)}:N", title=None),
    ).properties(height=300)
    st.altair_chart(chart, width="stretch")


def _valuation_label(value: Optional[float], ranges: List[float], lang: str) -> str:
    if value is None:
        return "N/A"
    if value <= ranges[1]:
        return t("valuation.undervalued", lang)
    elif value <= ranges[2]:
        return t("valuation.fair", lang)
    return t("valuation.overvalued", lang)


def render_valuation_tab() -> None:
    lang = st.session_state.get("lang", "zh_tw")
    scored = st.session_state.scored_data
    if not scored:
        st.warning(t("valuation.prompt", lang))
        return

    rows: List[Dict[str, Any]] = []
    for s in scored:
        row: Dict[str, Any] = {t("valuation.ticker", lang): s["ticker"],
                                t("valuation.name", lang): (s.get("longName") or "")[:20]}
        for key, label in VALUATION_LABELS.items():
            val = s.get(key)
            row[label] = f"{val:.2f}" if val is not None else "N/A"
        row[t("valuation.growth", lang)] = f"{s.get('total_score', 0):.1f}"
        rows.append(row)

    df = pd.DataFrame(rows)

    cols = st.columns(2)
    metrics_to_judge = [
        ("pe_ratio", t("metric.pe", lang), t("valuation.pe_note", lang)),
        ("peg", t("metric.peg", lang), t("valuation.peg_note", lang)),
        ("ps_ratio", t("metric.ps", lang), t("valuation.ps_note", lang)),
        ("pb_ratio", t("metric.pb", lang), t("valuation.pb_note", lang)),
        ("ev_ebitda", "EV/EBITDA", t("valuation.ev_note", lang)),
    ]
    for col, (key, label, note) in zip(cols * 3, metrics_to_judge):
        with col:
            st.markdown(f"**{label}**")
            st.caption(note)
            tickers = [s["ticker"] for s in scored]
            vals = [s.get(key) for s in scored]
            jdg = [_valuation_label(v, VALUATION_RANGES[key], lang) for v in vals]
            jdg_df = pd.DataFrame({
                t("valuation.ticker", lang): tickers,
                "Value": [f"{v:.2f}" if v else "N/A" for v in vals],
                t("valuation.judgment", lang): jdg,
            })
            st.dataframe(jdg_df, hide_index=True, width="stretch", height=200)

    st.subheader(t("valuation.full_table", lang))
    display_cols = [t("valuation.ticker", lang), t("valuation.name", lang)] + list(VALUATION_LABELS.values()) + [t("valuation.growth", lang)]
    st.dataframe(df[display_cols], hide_index=True, width="stretch", height=600)


def render_charts_tab() -> None:
    lang = st.session_state.get("lang", "zh_tw")
    recs = st.session_state.recommendations
    if not recs:
        st.warning(t("charts.nodata", lang))
        return

    ticker_options = {r["ticker"]: f"{r['ticker']} — {_stock_name(r, lang)}" for r in recs}
    selected = st.selectbox(t("charts.select", lang), options=list(ticker_options.keys()), format_func=lambda t_: ticker_options[t_])

    selected_rec = next((rec for rec in recs if rec["ticker"] == selected), {})
    st.subheader(t("chart.title", lang))
    _render_kline_chart(selected, lang, "charts_workspace", selected_rec.get("price"))
    st.markdown("---")
    st.subheader(t("charts.title", lang))

    from agents.data_fetcher import fetch_financials_history
    data = fetch_financials_history(selected)
    if not data:
        st.warning(t("charts.no_history", lang))
        return

    years = list(range(2026 - len(next(iter(data.values()))), 2026))

    for key, label, color in [
        ("revenue", t("charts.revenue", lang), "#0071e3"),
        ("net_income", t("charts.net_income", lang), "#34c759"),
        ("eps", t("charts.eps", lang), "#ff3b30"),
    ]:
        vals = data.get(key)
        if not vals:
            continue
        df = pd.DataFrame({"Year": years[-len(vals):], label: vals})
        chart = alt.Chart(df).mark_bar(color=color, opacity=0.85).encode(
            x=alt.X("Year:N", title=None),
            y=alt.Y(f"{label}:Q", title=None),
        ).properties(height=250)
        st.subheader(label)
        st.altair_chart(chart, width="stretch")

    if "revenue" in data and "net_income" in data:
        rev = data["revenue"]
        ni = data["net_income"]
        margin = [(ni[i] / rev[i] * 100) if rev[i] else 0 for i in range(min(len(ni), len(rev)))]
        dfm = pd.DataFrame({"Year": years[-len(margin):], t("charts.margin", lang): margin})
        cm = alt.Chart(dfm).mark_line(point=True, color="#af52de").encode(
            x=alt.X("Year:N", title=None),
            y=alt.Y(f"{t('charts.margin', lang)}:Q", title=None),
        ).properties(height=250)
        st.subheader(t("charts.margin", lang))
        st.altair_chart(cm, width="stretch")


def render_news_tab() -> None:
    lang = st.session_state.get("lang", "zh_tw")
    recs = st.session_state.recommendations
    if not recs:
        st.warning(t("news.nodata", lang))
        return

    all_news: List[Dict[str, Any]] = []
    for rec in recs:
        for n in rec.get("news", []):
            all_news.append(n)

    if not all_news:
        st.info(t("news.nodata", lang))
        return

    sentiment_order = {"positive": 0, "neutral": 1, "negative": 2}
    all_news.sort(key=lambda n: sentiment_order.get(n.get("sentiment", "neutral"), 1))

    for n in all_news:
        emoji = "" if n.get("sentiment") == "positive" else "" if n.get("sentiment") == "negative" else ""
        label = t("news.positive", lang) if n.get("sentiment") == "positive" else t("news.negative", lang) if n.get("sentiment") == "negative" else t("news.neutral", lang)
        with st.container(border=True):
            cols = st.columns([1, 6, 1])
            with cols[0]:
                st.markdown(f"**{n.get('ticker', '')}**")
            with cols[1]:
                st.markdown(f"{emoji} **{n.get('title', '')}**")
                if n.get("summary"):
                    st.caption(n["summary"][:200])
            with cols[2]:
                st.markdown(f"`{label}`")
                if n.get("link"):
                    st.markdown(f"[{t('news.view', lang)}]({n['link']})")


def render_ai_status_tab() -> None:
    lang = st.session_state.get("lang", "zh_tw")
    col1, col2 = st.columns(2)

    with col1:
        st.subheader(t("ai.source_health", lang))
        health = st.session_state.source_health or {}
        if health:
            health_rows = []
            for source, info in sorted(health.items()):
                total = info.get("success", 0) + info.get("failure", 0)
                rate = info.get("success", 0) / total * 100 if total > 0 else 0
                health_rows.append({
                    t("ai.source", lang): source,
                    t("ai.success", lang): info.get("success", 0),
                    t("ai.failure", lang): info.get("failure", 0),
                    t("ai.rate", lang): f"{rate:.0f}%",
                    t("ai.status", lang): info.get("last_status", t("ai.unknown", lang)),
                })
            st.dataframe(pd.DataFrame(health_rows), hide_index=True, width="stretch")
        else:
            st.info(t("ai.no_source", lang))

    with col2:
        st.subheader(t("ai.logs", lang))
        logs = st.session_state.upgrade_logs or []
        if logs:
            for log_entry in logs[-10:]:
                msg = log_entry.get("message", "")
                st.markdown(f"- {msg}")
        else:
            st.info(t("ai.no_logs", lang))

    st.subheader(t("ai.cache", lang))
    from utils.cache import cache
    st.json(cache.get_cache_status() or {})
    summary = st.session_state.get("agent_summary", {})
    if summary:
        st.subheader(t("ai.summary", lang))
        st.json(summary)


def render_stock_pool_tab() -> None:
    lang = st.session_state.get("lang", "zh_tw")
    st.subheader(t("tab.pool", lang))

    search = st.text_input("", placeholder="Search ticker or name...", label_visibility="collapsed", key="pool_search")

    all_stocks = list(STOCK_UNIVERSE)
    custom = st.session_state.get("custom_tickers", [])
    for t_ in custom:
        if t_ not in {s["ticker"] for s in all_stocks}:
            all_stocks.append({"ticker": t_, "name_cn": t_, "name_en": t_, "name_tw": t_, "sector": "Custom"})

    if search:
        s = search.upper()
        all_stocks = [s_ for s_ in all_stocks if s in s_["ticker"].upper() or s in s_["name_cn"].upper()]

    rows = []
    for s in all_stocks:
        is_custom = s["ticker"] in custom
        sec = s.get("sector", "")
        rows.append({
            "Ticker": s["ticker"],
            "Name": _stock_name(s, lang),
            "Sector": _sector_name(sec, lang),
            "Tier": t(f"universe.{s.get('universe_tier', 'satellite' if is_custom else 'core')}", lang),
            "Custom": "" if is_custom else "",
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, width="stretch", height=500)

    st.subheader(" " + t("sidebar.stock_pool", lang))
    sec_counts = df.groupby("Sector").size().reset_index(name="Count")
    st.dataframe(sec_counts, hide_index=True, width="stretch")


def render_home_tab(lang: str) -> None:
    st.markdown(
        f"<div class='landing-hero'><div class='landing-eyebrow'>{t('landing.eyebrow', lang)}</div>"
        f"<div class='landing-title'>{t('landing.title', lang)}</div>"
        f"<div class='landing-copy'>{t('landing.copy', lang)}</div></div>",
        unsafe_allow_html=True,
    )
    deep_col, scan_col, portfolio_col = st.columns(3)
    with deep_col:
        st.markdown(
            f"<div class='entry-card'><div class='entry-label'>01 · {t('landing.primary', lang)}</div><div class='entry-title'>{t('landing.picks_title', lang)}</div>"
            f"<div class='entry-copy'>{t('landing.picks_copy', lang)}</div><div class='entry-meta'>{t('landing.picks_meta', lang)}</div></div>",
            unsafe_allow_html=True,
        )
        if st.button(t("landing.picks_action", lang), type="primary", width="stretch", key="landing_picks"):
            st.session_state.deep_workspace = "research"
            st.session_state.app_route = "picks"
            st.rerun()
    with scan_col:
        st.markdown(
            f"<div class='entry-card'><div class='entry-label'>02</div><div class='entry-title'>{t('landing.scan_title', lang)}</div>"
            f"<div class='entry-copy'>{t('landing.scan_copy', lang, n=len(STOCK_UNIVERSE))}</div><div class='entry-meta'>{t('landing.scan_meta', lang, n=len(STOCK_UNIVERSE))}</div></div>",
            unsafe_allow_html=True,
        )
        if st.button(t("landing.scan_action", lang), type="secondary", width="stretch", key="landing_scan"):
            st.session_state.app_route = "scan"
            st.rerun()
    with portfolio_col:
        st.markdown(
            f"<div class='entry-card'><div class='entry-label'>03</div><div class='entry-title'>{t('landing.portfolio_title', lang)}</div>"
            f"<div class='entry-copy'>{t('landing.portfolio_copy', lang)}</div><div class='entry-meta'>{t('landing.portfolio_meta', lang)}</div></div>",
            unsafe_allow_html=True,
        )
        if st.button(t("landing.portfolio_action", lang), type="secondary", width="stretch", key="landing_portfolio"):
            st.session_state.deep_workspace = "portfolio"
            st.session_state.app_route = "picks"
            st.rerun()
    st.markdown(
        f"<div class='proof-strip'><div class='proof-item'><div class='proof-value'>{len(STOCK_UNIVERSE)}</div><div class='proof-label'>{t('landing.stocks', lang)}</div></div>"
        f"<div class='proof-item'><div class='proof-value'>14</div><div class='proof-label'>{t('landing.sectors', lang)}</div></div>"
        f"<div class='proof-item'><div class='proof-value'>5</div><div class='proof-label'>{t('landing.max_picks', lang)}</div></div>"
        f"<div class='proof-item'><div class='proof-value'>{t('landing.session_value', lang)}</div><div class='proof-label'>{t('landing.session_private', lang)}</div></div></div>",
        unsafe_allow_html=True,
    )


def _clear_picks_selection() -> None:
    st.session_state["picks_selection_widget"] = []
    st.session_state["picks_results"] = {}
    st.session_state["picks_errors"] = {}
    st.session_state["picks_analyzed_tickers"] = []
    st.session_state["picks_successful_tickers"] = []
    st.session_state["picks_recent_successful_tickers"] = []
    st.session_state["picks_analyzed_at"] = None
    st.session_state["picks_run_error"] = None


def _remember_successful_picks(selected: List[str], results: Dict[str, Dict[str, Any]]) -> List[str]:
    successful = [ticker for ticker in selected if ticker in results and not results[ticker].get("error")]
    st.session_state.picks_successful_tickers = successful
    if successful:
        remembered = st.session_state.get("picks_recent_successful_tickers", [])
        recent = list(dict.fromkeys([*remembered, *successful]))[-5:]
        st.session_state.picks_recent_successful_tickers = recent
        st.session_state.picks_news_selection_widget = list(recent)
    return successful


def _current_backtest_tickers() -> List[str]:
    analyzed = st.session_state.get("picks_analyzed_tickers") or []
    selected = st.session_state.get("picks_selection_widget") or []
    return list(dict.fromkeys(analyzed or selected))


def _backtest_request_signature(
    tickers: List[str], start: str, end: str, use_fundamentals: bool,
    weighting: str, transaction_cost_bps: float,
) -> Dict[str, Any]:
    return {
        "tickers": sorted(set(tickers)),
        "start": start,
        "end": end,
        "use_fundamentals": bool(use_fundamentals),
        "weighting": weighting,
        "transaction_cost_bps": float(transaction_cost_bps),
    }


def render_our_picks_page(lang: str, force_refresh: bool = False) -> None:
    st.subheader(t("deep.pool_title", lang))
    st.caption(t("deep.pool_desc_independent", lang, n=len(STOCK_UNIVERSE)))
    stock_by_ticker = {stock["ticker"]: stock for stock in STOCK_UNIVERSE}
    for ticker in st.session_state.get("account_favorites", []):
        stock_by_ticker.setdefault(
            ticker,
            {"ticker": ticker, "name_cn": ticker, "name_tw": ticker, "name_en": ticker, "sector": "Favorites"},
        )
    selected = st.multiselect(
        t("deep.selector", lang),
        list(stock_by_ticker),
        max_selections=5,
        format_func=lambda ticker: f"{ticker} — {_stock_name(stock_by_ticker[ticker], lang)} · {_sector_name(stock_by_ticker[ticker]['sector'], lang)}",
        key="picks_selection_widget",
    )
    account_col, risk_col = st.columns(2)
    with account_col:
        st.number_input(
            t("portfolio.capital", lang), min_value=1000, max_value=10_000_000,
            value=100_000, step=10_000, key="picks_account_capital",
        )
    with risk_col:
        st.number_input(
            t("deep.risk_budget_pct", lang), min_value=0.1, max_value=5.0,
            value=1.0, step=0.1, key="picks_risk_budget_pct",
        )
    st.button(
        t("deep.clear", lang),
        width="stretch",
        key="picks_clear",
        on_click=_clear_picks_selection,
    )
    if st.button(t("deep.run", lang), type="primary", width="stretch", disabled=not selected, key="picks_run"):
        from agents.deep_research import analyze_tickers

        st.session_state.picks_status = "running"
        st.session_state.picks_run_error = None
        started_at = time.monotonic()
        total_units = max(1, len(selected) * 4)
        completed_units = set()
        active_stages: Dict[str, str] = {}
        progress = st.progress(0, text=t("deep.progress_starting", lang))

        def update(event: Dict[str, Any]) -> None:
            ticker = event.get("ticker")
            stage = event.get("stage", "heartbeat")
            state = event.get("state")
            if ticker and stage in {"fundamental", "technical", "market_data", "strategy"}:
                active_stages[ticker] = stage
                if state in {"completed", "failed", "skipped"}:
                    completed_units.add((ticker, stage))
            if ticker and stage == "ticker" and state == "completed":
                active_stages.pop(ticker, None)
            fraction = min(0.98, len(completed_units) / total_units)
            elapsed = int(time.monotonic() - started_at)
            status = " · ".join(
                f"{symbol}: {t(f'deep.stage_{active}', lang)}" for symbol, active in active_stages.items()
            ) or t("deep.progress_finishing", lang)
            progress.progress(fraction, text=t("deep.progress_status", lang, elapsed=elapsed, status=status))

        try:
            results = analyze_tickers(selected, lang=lang, force_refresh=force_refresh, progress_callback=update)
            st.session_state.picks_results = results
            st.session_state.picks_errors = {ticker: result["error"] for ticker, result in results.items() if result.get("error")}
            st.session_state.picks_analyzed_tickers = list(selected)
            _remember_successful_picks(selected, results)
            st.session_state.picks_analyzed_at = pd.Timestamp.now(tz="UTC").isoformat()
            st.session_state.picks_status = "success" if not st.session_state.picks_errors else "partial"
            elapsed = int(time.monotonic() - started_at)
            progress.progress(1.0, text=t("deep.progress_complete", lang, elapsed=elapsed))
        except Exception as exc:
            st.session_state.picks_status = "error"
            st.session_state.picks_run_error = str(exc)
            st.error(t("app.error", lang, msg=str(exc)))
        finally:
            progress.empty()
    results = st.session_state.get("picks_results", {})
    if results:
        analyzed = st.session_state.get("picks_analyzed_tickers", [])
        if set(selected) != set(analyzed):
            st.warning(t("deep.results_stale", lang))
        st.markdown("---")
        st.subheader(t("deep.results", lang))
        if st.session_state.get("picks_analyzed_at"):
            st.caption(t("deep.analyzed_at", lang, time=st.session_state.picks_analyzed_at))
        for ticker, result in results.items():
            _render_deep_research_result(ticker, result, lang, force_refresh=force_refresh)


def _import_picks_to_news() -> None:
    tickers = (
        st.session_state.get("picks_recent_successful_tickers")
        or st.session_state.get("picks_successful_tickers")
        or st.session_state.get("picks_analyzed_tickers")
        or st.session_state.get("picks_selection_widget")
        or []
    )
    st.session_state["picks_news_selection_widget"] = list(tickers)[:5]


def render_picks_news_page(lang: str, force_refresh: bool = False) -> None:
    st.subheader(t("picks_news.title", lang))
    st.caption(t("picks_news.desc", lang))
    stock_by_ticker = {stock["ticker"]: stock for stock in STOCK_UNIVERSE}
    if not st.session_state.get("picks_news_selection_widget"):
        remembered = (
            st.session_state.get("picks_recent_successful_tickers")
            or st.session_state.get("picks_successful_tickers")
            or st.session_state.get("picks_analyzed_tickers")
            or st.session_state.get("picks_selection_widget")
            or []
        )
        st.session_state.picks_news_selection_widget = list(remembered)[:5]
    selected = st.multiselect(
        t("picks_news.selector", lang), list(stock_by_ticker), max_selections=5,
        format_func=lambda ticker: f"{ticker} — {_stock_name(stock_by_ticker[ticker], lang)}",
        key="picks_news_selection_widget",
    )
    import_col, run_col = st.columns(2)
    with import_col:
        st.button(t("picks_news.import", lang), width="stretch", on_click=_import_picks_to_news, key="picks_news_import")
    with run_col:
        run_news = st.button(t("picks_news.run", lang), type="primary", width="stretch", disabled=not selected, key="picks_news_run")
    include_ai = st.checkbox(t("picks_news.include_ai", lang), value=True, key="picks_news_include_ai")
    if run_news:
        from agents.picks_news import analyze_picks_news

        st.session_state.picks_news_status = "running"
        progress = st.progress(0, text=t("picks_news.running", lang))

        def update(ticker: str, completed: int, total: int) -> None:
            progress.progress(completed / total if total else 0, text=f"{ticker} ({completed + 1}/{total})")

        try:
            results = analyze_picks_news(selected, lang, force_refresh, include_ai, progress_callback=update)
            st.session_state.picks_news_results = results
            st.session_state.picks_news_analyzed_tickers = list(selected)
            st.session_state.picks_news_analyzed_at = pd.Timestamp.now(tz="UTC").isoformat()
            st.session_state.picks_news_status = "success"
            progress.progress(1.0, text=t("picks_news.complete", lang))
        except Exception as exc:
            st.session_state.picks_news_status = "error"
            st.error(t("app.error", lang, msg=str(exc)))
        finally:
            progress.empty()
    results = st.session_state.get("picks_news_results", {})
    if results:
        if set(selected) != set(st.session_state.get("picks_news_analyzed_tickers", [])):
            st.warning(t("picks_news.stale", lang))
        st.markdown("---")
        for ticker, result in results.items():
            _render_picks_news_result(ticker, result, lang)


def render_deep_workspace(lang: str, force_refresh: bool = False) -> None:
    workspaces = {
        "research": t("deep.workspace_research", lang),
        "news": t("deep.workspace_news", lang),
        "backtest": t("deep.workspace_backtest", lang),
        "portfolio": t("deep.workspace_portfolio", lang),
    }
    workspace = st.segmented_control(
        t("deep.workspace", lang),
        options=list(workspaces),
        format_func=workspaces.get,
        key="deep_workspace",
        label_visibility="collapsed",
    ) or "research"
    if workspace == "research":
        render_our_picks_page(lang, force_refresh=force_refresh)
    elif workspace == "news":
        render_picks_news_page(lang, force_refresh=force_refresh)
    elif workspace == "backtest":
        render_backtest_tab(_current_backtest_tickers())
    else:
        render_portfolio_tab()


def _render_picks_news_result(ticker: str, result: Dict[str, Any], lang: str) -> None:
    with st.expander(f"{ticker} — {t('picks_news.latest', lang)}", expanded=True):
        earnings = result.get("earnings", {})
        if earnings.get("available"):
            date_text = earnings.get("next_date", "N/A")
            if earnings.get("precision") == "range":
                date_text = f"{earnings.get('date_start')} – {earnings.get('date_end')}"
            st.info(t(
                "picks_news.earnings_event", lang, date=date_text,
                days=earnings.get("days_until", "N/A"), precision=t(f"picks_news.{earnings.get('precision', 'unknown')}", lang),
            ))
        else:
            st.caption(t("picks_news.no_earnings", lang))
        st.caption(t(
            "picks_news.provenance", lang,
            fetched=_format_timestamp(result.get("fetched_at")),
            cutoff=_format_timestamp(result.get("news_cutoff_at")),
            earnings=result.get("earnings_source") or "N/A",
        ))
        errors = result.get("errors", {})
        if result.get("status") == "partial":
            st.warning(t("picks_news.partial", lang))
        items = result.get("items", [])
        if not items:
            st.info(t("picks_news.no_articles", lang))
            return
        for article in items:
            impact = article.get("impact", {})
            published = article.get("published_at")
            published_text = pd.Timestamp(published).strftime("%Y-%m-%d %H:%M UTC") if published else t("deep.unavailable", lang)
            st.markdown(f"### {html.escape(article.get('title', ''))}")
            st.caption(f"{article.get('publisher') or 'Yahoo'} · {published_text} · {t('picks_news.source', lang)}: {article.get('source', 'yahoo')}")
            if article.get("summary"):
                st.write(article["summary"][:900])
            impact_cols = st.columns(4)
            impact_cols[0].metric(t("picks_news.direction", lang), t(f"picks_news.{impact.get('direction', 'neutral')}", lang))
            impact_cols[1].metric(t("picks_news.magnitude", lang), t(f"picks_news.{impact.get('magnitude', 'low')}", lang))
            impact_cols[2].metric(t("picks_news.horizon", lang), t(f"picks_news.{impact.get('horizon', 'short_term')}", lang))
            impact_cols[3].metric(t("picks_news.confidence", lang), f"{impact.get('confidence', 0)}%")
            st.caption(f"{t('picks_news.event_type', lang)}: {t(f'picks_news.event_{impact.get("event_type", "other")}', lang)} · {t('picks_news.analysis_source', lang)}: {t(f'picks_news.source_{article.get("analysis_source", "rules")}', lang)}")
            if impact.get("thesis"):
                st.write(impact["thesis"])
            if article.get("link"):
                st.link_button(t("news.view", lang), article["link"])
            st.markdown("---")
        if errors.get("ai"):
            st.caption(t("picks_news.ai_fallback", lang))
        st.caption(t("picks_news.disclaimer", lang))



def export_csv() -> None:
    lang = st.session_state.get("lang", "zh_tw")
    if not st.session_state.all_rankings:
        return
    df = pd.DataFrame(st.session_state.all_rankings)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    st.sidebar.download_button(
        t("sidebar.export", lang),
        data=buf.getvalue(),
        file_name=f"stock_analysis_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
        width="stretch",
    )


def render_industry_news_tab() -> None:
    lang = st.session_state.get("lang", "zh_tw")
    st.subheader(t("industry_news.title", lang))
    st.caption(t("recommend.desc", lang) + " | " + t("industry_news.source", lang, source="Yahoo Finance"))
    try:
        news_data = fetch_industry_news()
    except Exception as e:
        st.warning(t("industry_news.no_data", lang) + ": " + str(e))
        return
    if not news_data or not news_data.get("all_headlines"):
        st.warning(t("industry_news.no_data", lang))
        return
    climate_items = news_data.get("climate_items", [])
    by_sector = news_data.get("by_sector", {})
    all_headlines = news_data.get("all_headlines", [])
    if climate_items:
        c_label = t("industry_news.warming", lang) + " (" + str(len(climate_items)) + ")"
        with st.expander(c_label, expanded=True):
            for item in climate_items:
                sec_cn = _sector_name(item.get("sector", ""), lang)
                st.markdown("**" + item.get("title", "") + "**")
                if item.get("summary"):
                    st.markdown(item["summary"][:200])
                pub = item.get("publisher", "")
                link = item.get("link", "")
                st.caption(pub + " | " + sec_cn + " | [" + t("news.view", lang) + "](" + link + ")")
                st.markdown("---")
    sector_names = sorted(by_sector.keys())
    tab_labels = [_sector_name(s, lang) for s in sector_names]
    tab_labels.append(t("news.title", lang) + " (" + str(len(all_headlines)) + ")")
    sector_tabs = st.tabs(tab_labels)
    for i, sec_en in enumerate(sector_names):
        with sector_tabs[i]:
            for item in by_sector[sec_en]:
                st.markdown("**" + item.get("title", "") + "**")
                if item.get("summary"):
                    st.markdown(item["summary"][:200])
                pub = item.get("publisher", "")
                link = item.get("link", "")
                st.caption(pub + " | [" + t("news.view", lang) + "](" + link + ")")
                st.markdown("---")
    with sector_tabs[-1]:
        for item in all_headlines:
            sec_cn = _sector_name(item.get("sector", ""), lang)
            st.markdown("**" + item.get("title", "") + "**")
            if item.get("summary"):
                st.markdown(item["summary"][:200])
            pub = item.get("publisher", "")
            link = item.get("link", "")
            st.caption(pub + " | " + sec_cn + " | [" + t("news.view", lang) + "](" + link + ")")
            st.markdown("---")


def render_backtest_tab(selected_tickers: Optional[List[str]] = None) -> None:
    lang = st.session_state.get("lang", "zh_tw")
    st.subheader(t("backtest.title", lang))
    st.caption(t("backtest.desc", lang))

    imported_tickers = list(dict.fromkeys(selected_tickers or []))
    import_signature = tuple(imported_tickers)
    if st.session_state.get("backtest_import_signature") != import_signature:
        st.session_state.backtest_import_signature = import_signature
        st.session_state.backtest_ticker_widget = imported_tickers
    universe_tickers = [stock["ticker"] for stock in STOCK_UNIVERSE]
    ticker_options = list(dict.fromkeys([*imported_tickers, *universe_tickers]))
    active_tickers = st.multiselect(
        t("backtest.selected_tickers", lang), ticker_options,
        key="backtest_ticker_widget",
        placeholder=t("backtest.selected_tickers_placeholder", lang),
    )
    if active_tickers:
        st.info(t("backtest.selected_tickers_summary", lang, tickers=", ".join(active_tickers)))
    else:
        st.warning(t("backtest.no_selected_tickers", lang))

    col1, col2, col3, col4 = st.columns([2, 1.2, 1.5, 1])
    with col1:
        use_fund = st.checkbox(
            t("backtest.use_fundamentals", lang), value=False,
            help=t("backtest.fundamentals_help", lang), key="backtest_use_fundamentals",
        )
    with col2:
        years = st.selectbox(t("backtest.years", lang), [2, 3], index=1)
    with col3:
        weighting_label = st.selectbox(
            t("backtest.weighting", lang),
            [t("backtest.equal", lang), t("backtest.calibrated_kelly", lang)],
        )
        weighting = "equal" if weighting_label == t("backtest.equal", lang) else "calibrated_kelly"
    with col4:
        cost_bps = st.number_input(t("backtest.cost_bps", lang), min_value=0.0, max_value=100.0, value=15.0, step=1.0)
        run = st.button(t("backtest.run", lang), type="primary", width="stretch", disabled=not active_tickers)

    import datetime as dt
    end_str = dt.datetime.now().strftime("%Y-%m-%d")
    start_str = (dt.datetime.now() - dt.timedelta(days=years * 365)).strftime("%Y-%m-%d")
    request_signature = _backtest_request_signature(
        active_tickers, start_str, end_str, use_fund, weighting, cost_bps,
    )

    if run:
        status = st.status(t("backtest.running", lang), expanded=True)

        def progress(i: int, n: int, msg: str = ""):
            if n > 0:
                status.progress(i / n, text=f"{msg} ({i}/{n})")
            else:
                status.text(msg)

        from backtesting.engine import run_backtest, format_backtest_summary

        result = run_backtest(
            start=start_str,
            end=end_str,
            use_fundamentals=use_fund,
            progress_callback=progress,
            selected_tickers=active_tickers,
            weighting=weighting,
            transaction_cost_bps=cost_bps,
            persist_calibration=True,
        )
        summary = format_backtest_summary(result)
        summary["request"] = request_signature

        st.session_state.backtest_summary = summary
        status.update(label=t("backtest.complete", lang), state="complete", expanded=False)

    summary = st.session_state.get("backtest_summary")
    if not summary:
        st.info(t("backtest.no_data", lang))
        return
    if summary.get("request") != request_signature:
        st.info(t("backtest.inputs_changed", lang))
        return

    for warning in summary.get("warnings", []):
        warning_key = f"backtest.warning.{warning.get('code', '')}"
        translated = t(warning_key, lang)
        st.warning(warning.get("message", "") if translated == warning_key else translated)

    coverage = summary.get("coverage", {})
    if coverage:
        st.caption(t(
            "backtest.coverage_summary",
            lang,
            prices=f"{coverage.get('tickers_with_prices', 0)}/{coverage.get('requested_tickers', 0)}",
            technical=coverage.get("avg_technical_pct", 0),
            fundamental=coverage.get("avg_fundamental_pct", 0),
            costs=summary.get("total_transaction_cost", 0),
        ))

    statistics = summary.get("statistics", {})
    evidence = summary.get("historical_evidence_grade", {})
    if statistics:
        effective = statistics.get("effective_periods", {})
        alignment = statistics.get("benchmark_alignment", {})
        universe_coverage = coverage.get("universe", {}).get("coverage_pct", 0)
        stat_cols = st.columns(4)
        stat_cols[0].metric(t("backtest.evidence_grade", lang), t(f"validation.{evidence.get('level', 'insufficient')}", lang))
        stat_cols[1].metric(t("backtest.effective_periods", lang), f"{effective.get('effective', 0):g} / {effective.get('raw', 0)}")
        stat_cols[2].metric(t("backtest.benchmark_alignment", lang), f"{alignment.get('pct', 0):.1f}%")
        stat_cols[3].metric(t("backtest.universe_coverage", lang), f"{universe_coverage:.1f}%")
        alpha_ci = statistics.get("alpha_ci", {})
        if alpha_ci.get("available"):
            st.caption(t(
                "backtest.alpha_ci", lang,
                estimate=alpha_ci.get("estimate", 0),
                lower=alpha_ci.get("lower", 0),
                upper=alpha_ci.get("upper", 0),
            ))
        if evidence.get("reason_codes"):
            translated_reasons = [
                t(f"backtest.reason.{reason}", lang)
                for reason in evidence["reason_codes"]
            ]
            st.warning(t("backtest.grade_limited", lang, reasons="；".join(translated_reasons)))
        sensitivity = summary.get("cost_sensitivity", [])
        if sensitivity:
            with st.expander(t("backtest.cost_sensitivity", lang)):
                st.dataframe(pd.DataFrame(sensitivity), hide_index=True, width="stretch")

    st.markdown("---")
    cols = st.columns(4)
    with cols[0]:
        st.metric(t("backtest.total_return", lang), f"{summary['total_return']:+.1f}%",
                  delta=f"{summary['avg_alpha']:+.1f}% vs SPY" if summary.get('avg_alpha') else None,
                  delta_color="normal")
    with cols[1]:
        st.metric(t("backtest.spy_return", lang), f"{summary['spy_return']:+.1f}%")
    with cols[2]:
        st.metric(t("backtest.win_rate", lang), f"{summary['win_rate']:.1f}%",
                  delta=f"{summary['wins']}W / {summary['losses']}L")
    with cols[3]:
        st.metric(t("backtest.sharpe", lang), f"{summary['sharpe']:.2f}")

    cols2 = st.columns(4)
    with cols2[0]:
        st.metric(t("backtest.max_dd", lang), f"{summary['max_drawdown']:.1f}%")
    with cols2[1]:
        st.metric(t("backtest.volatility", lang), f"{summary['volatility']:.1f}%")
    with cols2[2]:
        st.metric(t("backtest.best_month", lang), f"{summary['best_month']:+.1f}%")
    with cols2[3]:
        st.metric(t("backtest.worst_month", lang), f"{summary['worst_month']:+.1f}%")

    st.markdown("---")
    import plotly.graph_objects as go
    import numpy as np

    periods = summary.get("periods", [])

    tab1, tab2, tab3, tab4 = st.tabs([
        t("backtest.equity_curve", lang),
        t("backtest.monthly_returns", lang),
        t("backtest.top_picks", lang),
        t("backtest.period_detail", lang),
    ])

    with tab1:
        equity_curve = summary.get("equity_curve", [])
        label_dates = [point["date"] for point in equity_curve]
        strategy_vals = [point["portfolio"] for point in equity_curve]
        spy_vals = [point["spy"] for point in equity_curve]

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=label_dates, y=strategy_vals, mode="lines",
                                 name="Strategy", line=dict(color="#00d4aa", width=2)))
        fig.add_trace(go.Scatter(x=label_dates, y=spy_vals, mode="lines",
                                 name="SPY", line=dict(color="#888", width=2, dash="dash")))
        fig.update_layout(
            height=400, margin=dict(l=40, r=20, t=20, b=40),
            yaxis_title="Portfolio Value ($)",
            hovermode="x unified",
            legend=dict(orientation="h", y=1.1),
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(7,11,18,.55)",
            font=dict(color="#9fb0c3", family="SFMono-Regular, Consolas, monospace"),
        )
        st.plotly_chart(fig, width="stretch")

    with tab2:
        monthly = []
        for p in periods:
            if p.get("avg_return") is not None:
                monthly.append({
                    "date": str(p["date"].date()),
                    "strategy_return": p["avg_return"],
                    "spy_return": p.get("spy_return"),
                    "alpha": p.get("alpha"),
                    "beat_spy": p.get("beat_spy", False),
                })
        if monthly:
            df_m = pd.DataFrame(monthly)
            fig2 = go.Figure()
            colors = ["#00d4aa" if r else "#ff4b4b" for r in df_m["beat_spy"]]
            fig2.add_trace(go.Bar(
                x=df_m["date"], y=df_m["strategy_return"],
                marker_color=colors,
                name="Strategy",
            ))
            fig2.add_trace(go.Scatter(
                x=df_m["date"], y=df_m["spy_return"],
                mode="lines+markers",
                name="SPY", line=dict(color="#888", width=1.5, dash="dash"),
            ))
            fig2.update_layout(
                height=400, margin=dict(l=40, r=20, t=20, b=40),
                yaxis_title="Return (%)",
                hovermode="x unified",
                showlegend=True,
                legend=dict(orientation="h", y=1.1),
                template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(7,11,18,.55)",
                font=dict(color="#9fb0c3", family="SFMono-Regular, Consolas, monospace"),
            )
            st.plotly_chart(fig2, width="stretch")

    with tab3:
        top_picks = summary.get("tickers_picked", {})
        sector_counts = summary.get("sector_counts", {})

        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader(t("backtest.top_picks", lang))
            if top_picks:
                df_picks = pd.DataFrame({
                    "Ticker": list(top_picks.keys()),
                    "Picks": list(top_picks.values()),
                }).sort_values("Picks", ascending=True)
                fig3 = go.Figure(go.Bar(
                    x=df_picks["Picks"], y=df_picks["Ticker"],
                    orientation="h",
                    marker_color="#00d4aa",
                ))
                fig3.update_layout(height=400, margin=dict(l=20, r=20, t=10, b=20), xaxis_title="Times Picked", template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(7,11,18,.55)", font=dict(color="#9fb0c3"))
                st.plotly_chart(fig3, width="stretch")

        with col_b:
            st.subheader(t("backtest.sector_breakdown", lang))
            if sector_counts:
                df_sec = pd.DataFrame({
                    "Sector": list(sector_counts.keys()),
                    "Picks": list(sector_counts.values()),
                })
                fig4 = go.Figure(go.Pie(
                    labels=df_sec["Sector"], values=df_sec["Picks"],
                    hole=0.4,
                ))
                fig4.update_layout(height=400, margin=dict(l=20, r=20, t=10, b=20), template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(7,11,18,.55)", font=dict(color="#9fb0c3"))
                st.plotly_chart(fig4, width="stretch")

    with tab4:
        if periods:
            rows = []
            has_fund = any(p.get("avg_fund_score") is not None for p in periods)
            for p in periods:
                row = {
                    "Date": str(p["date"].date()),
                    "Entry": p.get("entry_date") or "",
                    "Exit": p.get("exit_date") or "",
                    "Picks": ", ".join(p.get("picks", [])),
                    "Model": p.get("model_scope", ""),
                    "Eligible": p.get("coverage", {}).get("eligible", ""),
                    "Tech Score": p.get("avg_tech_score", ""),
                    "Fund Score": f"{p.get('avg_fund_score', ''):.1f}" if p.get("avg_fund_score") is not None else "",
                    "Total Score": p.get("avg_score", ""),
                    "Return %": f"{p.get('avg_return', ''):+.1f}" if p.get("avg_return") is not None else "",
                    "SPY %": f"{p.get('spy_return', ''):+.1f}" if p.get("spy_return") is not None else "",
                    "Alpha %": f"{p.get('alpha', ''):+.1f}" if p.get("alpha") is not None else "",
                    "Beat": "" if p.get("beat_spy") else "",
                }
                if not has_fund:
                    del row["Fund Score"]
                rows.append(row)
            df_detail = pd.DataFrame(rows)
            st.dataframe(df_detail, hide_index=True, width="stretch", height=500)


def render_portfolio_tab() -> None:
    lang = st.session_state.get("lang", "zh_tw")
    portfolio = st.session_state.get("portfolio", {})
    if not portfolio or not portfolio.get("positions"):
        st.info(t("portfolio.no_data", lang))
        return

    positions = portfolio["positions"]
    st.subheader(t("portfolio.title", lang))
    st.caption(t("portfolio.desc", lang))

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric(t("portfolio.capital", lang), f"${portfolio['total_capital']:,.0f}")
    with col2:
        st.metric(t("portfolio.invested", lang), f"${portfolio['total_invested']:,.0f}")
    with col3:
        st.metric(t("portfolio.cash", lang), f"${portfolio['cash']:,.0f}")
    with col4:
        pnl = portfolio["total_pnl"]
        st.metric(t("portfolio.pnl", lang), f"${pnl:+,.0f}", delta_color="normal" if pnl >= 0 else "inverse")
    with col5:
        pnl_pct = portfolio["total_pnl_pct"]
        st.metric(t("portfolio.pnl_pct", lang), f"{pnl_pct:+.2f}%",
                  delta=f"{pnl_pct:+.2f}%", delta_color="normal" if pnl_pct >= 0 else "inverse")

    st.markdown(
        f'<p style="color:#86868b;font-size:0.8rem;">{t("portfolio.alloc_hint", lang)}</p>',
        unsafe_allow_html=True,
    )

    portfolio_risk = portfolio.get("portfolio_risk", {})
    if portfolio_risk:
        st.subheader(t("portfolio.risk_overview", lang))
        risk_cols = st.columns(4)
        with risk_cols[0]:
            st.metric(t("portfolio.portfolio_volatility", lang), f"{portfolio_risk.get('annual_volatility_pct', 0):.1f}%" if portfolio_risk.get("available") else "N/A")
        with risk_cols[1]:
            st.metric(t("portfolio.daily_var_95", lang), f"{portfolio_risk.get('var_95_daily_pct', 0):.2f}%" if portfolio_risk.get("available") else "N/A")
        with risk_cols[2]:
            st.metric(t("portfolio.history_coverage", lang), f"{portfolio_risk.get('equity_coverage_pct', 0):.1f}%")
        with risk_cols[3]:
            st.metric(t("portfolio.cash_weight", lang), f"{portfolio_risk.get('cash_weight_pct', 0):.1f}%")
        if portfolio_risk.get("coverage_status") == "partial":
            excluded = ", ".join(
                f"{ticker} ({reason})" for ticker, reason in portfolio_risk.get("excluded_tickers", {}).items()
            )
            st.warning(t("portfolio.coverage_warning", lang, tickers=excluded or "N/A"))
        stress_tests = portfolio_risk.get("stress_tests", [])
        if stress_tests:
            st.markdown("**" + t("portfolio.stress_tests", lang) + "**")
            st.dataframe(pd.DataFrame([{
                t("portfolio.scenario", lang): t(f"portfolio.scenario.{row['scenario']}", lang),
                t("portfolio.equity_shock", lang): f"{row['equity_shock_pct']:.0f}%",
                t("portfolio.stress_loss", lang): f"${row['pnl']:,.0f}",
                t("portfolio.portfolio_change", lang): f"{row['portfolio_change_pct']:.1f}%",
                t("portfolio.stressed_value", lang): f"${row['stressed_value']:,.0f}",
            } for row in stress_tests]), hide_index=True, width="stretch")

    high_corr = portfolio.get("high_corr_pairs", [])
    correlation_df = portfolio.get("correlation_df")
    if correlation_df is not None and not correlation_df.empty:
        st.subheader(t("portfolio.correlation_heatmap", lang))
        correlation_long = (
            correlation_df.rename_axis("ticker_y")
            .reset_index()
            .melt(id_vars="ticker_y", var_name="ticker_x", value_name="correlation")
        )
        heatmap = alt.Chart(correlation_long).mark_rect(cornerRadius=2).encode(
            x=alt.X("ticker_x:N", title=None, sort=list(correlation_df.columns)),
            y=alt.Y("ticker_y:N", title=None, sort=list(correlation_df.index)),
            color=alt.Color(
                "correlation:Q",
                scale=alt.Scale(domain=[-1, 0, 1], range=["#2563eb", "#111827", "#ef4444"]),
                title="ρ",
            ),
            tooltip=[
                alt.Tooltip("ticker_x:N", title="Ticker"),
                alt.Tooltip("ticker_y:N", title="Ticker"),
                alt.Tooltip("correlation:Q", title="ρ", format=".3f"),
            ],
        ).properties(height=max(240, 42 * len(correlation_df.index)))
        st.altair_chart(heatmap, width="stretch")
    if high_corr:
        with st.expander(t("portfolio.high_corr", lang), expanded=True):
            st.caption(t("portfolio.high_corr_desc", lang))
            for t1, t2, val in high_corr:
                st.warning(f"**{t1}**  **{t2}**: ρ = {val:.3f}")

    stop_hit = [p for p in positions if p.get("stop_hit")]
    if stop_hit:
        for p in stop_hit:
            st.error(f"{t('portfolio.stop_hit', lang)} — **{p['ticker']}** @ ${p['current_price']:.2f} (SL: ${p['stop_loss']:.2f})")

    st.subheader(t("portfolio.positions", lang))
    pos_rows = []
    for p in positions:
        pos_rows.append({
            "Ticker": p["ticker"],
            t("portfolio.score", lang): p["total_score"],
            t("risk.selection_score", lang): p.get("risk_adjusted_score", p["total_score"]),
            t("risk.penalty", lang): f"-{p.get('risk_penalty', 0):.0f}",
            t("portfolio.target_weight", lang): f"{p['weight']*100:.1f}%",
            t("portfolio.actual_weight", lang): f"{p.get('actual_weight', 0)*100:.1f}%",
            t("portfolio.market_value", lang): f"${p.get('market_value', 0):,.0f}",
            t("portfolio.shares", lang): p["shares"],
            t("portfolio.entry_price", lang): f"${p['entry_price']:.2f}",
            t("portfolio.current_price", lang): f"${p['current_price']:.2f}",
            t("portfolio.pnl", lang): f"${p['pnl']:+,.0f}",
            t("portfolio.pnl_pct", lang): f"{p['pnl_pct']:+.1f}%",
            t("portfolio.stop_loss", lang): f"${p['stop_loss']:.2f}",
            t("portfolio.target", lang): f"${p['target_price']:.2f}" if p.get("target_price") else "N/A",
            "Risk": p.get("risk_metrics", {}).get("risk_level", "unknown").upper(),
            "Volatility": f"{p.get('risk_metrics', {}).get('annual_volatility_pct', 0):.1f}%" if p.get("risk_metrics", {}).get("available") else "N/A",
        })
    import pandas as pd
    st.dataframe(pd.DataFrame(pos_rows), hide_index=True, width="stretch", height=400)

    with st.expander(t("portfolio.journal", lang)):
        journal = st.session_state.get("portfolio_journal", [])
        if journal:
            import pandas as _pd
            st.dataframe(_pd.DataFrame(journal), hide_index=True, width="stretch", height=300)
        else:
            st.caption(t("portfolio.journal_empty", lang))

    if st.button(t("portfolio.reset", lang), type="secondary"):
        st.session_state.portfolio = {}
        st.session_state.portfolio_state = {}
        st.session_state.portfolio_journal = []
        st.rerun()


def build_minimal_sidebar() -> Dict[str, Any]:
    lang = st.session_state.get("lang", "zh_tw")
    st.sidebar.markdown(f'<div class="sidebar-section">{t("sidebar.control", lang)}</div>', unsafe_allow_html=True)
    language_options = {"zh_cn": "简体中文", "zh_tw": "繁體中文", "en": "English"}
    selected_lang = st.sidebar.selectbox(
        "Language",
        options=list(language_options),
        format_func=lambda key: language_options[key],
        index=list(language_options).index(lang),
        key="minimal_lang_selector",
    )
    if selected_lang != lang:
        st.session_state.lang = selected_lang
        st.rerun()
    render_account_panel(get_account_repository(), selected_lang, st.session_state)
    force_refresh = st.sidebar.checkbox(t("sidebar.refresh", selected_lang), value=False, key="picks_force_refresh")
    return {"lang": selected_lang, "force_refresh": force_refresh}


def render_primary_navigation(lang: str) -> None:
    home, picks, scan = st.columns(3)
    actions = [
        (home, "home", t("nav.home", lang)),
        (picks, "picks", t("nav.picks", lang)),
        (scan, "scan", t("nav.scan", lang)),
    ]
    for column, route, label in actions:
        with column:
            if st.button(
                label,
                type="primary" if st.session_state.app_route == route else "secondary",
                width="stretch",
                key=f"route_{route}",
            ):
                st.session_state.app_route = route
                if route == "picks":
                    st.session_state.deep_workspace = "research"
                st.rerun()


def render_scan_page(params: Dict[str, Any], lang: str) -> None:
    params = dict(params)
    st.session_state.scan_filter_tickers = list(params.get("selected_tickers") or [])
    params["selected_tickers"] = [stock["ticker"] for stock in STOCK_UNIVERSE]
    with st.container(key="primary_analysis_action"):
        run_clicked = st.button(
            t("sidebar.start", lang),
            type="primary",
            width="stretch",
            key="primary_run_analysis",
            disabled=st.session_state.analysis_running,
        )
    if run_clicked:
        run_analysis(params)
    if not st.session_state.analysis_done:
        st.caption(t("scan.ready", lang, n=len(STOCK_UNIVERSE)))
        return
    views = {
        "overview": t("tab.recommend", lang), "ranking": t("tab.ranking", lang),
        "charts": t("tab.charts", lang), "compare": t("tab.compare", lang),
        "valuation": t("tab.valuation", lang),
        "news": t("tab.news", lang), "industry": t("industry_news.title", lang),
        "pool": t("tab.pool", lang), "system": t("tab.ai", lang),
    }
    selected_view = st.selectbox(
        t("scan.view", lang), options=list(views), format_func=views.get,
        key="scan_view", label_visibility="collapsed",
    )
    if selected_view == "overview":
        render_recommendations_tab()
    elif selected_view == "ranking":
        render_rankings_tab()
    elif selected_view == "charts":
        render_charts_tab()
    elif selected_view == "pool":
        render_stock_pool_tab()
    elif selected_view == "compare":
        render_compare_tab()
    elif selected_view == "valuation":
        render_valuation_tab()
    elif selected_view == "news":
        render_news_tab()
    elif selected_view == "industry":
        render_industry_news_tab()
    elif selected_view == "system":
        render_ai_status_tab()


def main() -> None:
    st.set_page_config(page_title="Stock Analyzer", page_icon="", layout="wide")
    init_state()
    account_repository = get_account_repository()
    if account_repository is not None:
        hydrate_account_state(st.session_state, account_repository)
    _inject_apple_css()

    st.markdown("""<script>
try {
  const l=document.createElement('link'); l.rel='manifest'; l.href='/manifest.json';
  document.head.appendChild(l);
  const m=document.createElement('meta'); m.name='apple-mobile-web-app-capable'; m.content='yes';
  document.head.appendChild(m);
  if('serviceWorker'in navigator) navigator.serviceWorker.register('/sw.js',{scope:'/'});
} catch(e){}
</script>""", unsafe_allow_html=True)

    lang = st.session_state.get("lang", "zh_tw")

    legacy_workspace = {"picks_news": "news", "portfolio": "portfolio"}
    if st.session_state.app_route in legacy_workspace:
        st.session_state.deep_workspace = legacy_workspace[st.session_state.app_route]
        st.session_state.app_route = "picks"

    st.markdown(
        f"""
        <div class="terminal-header">
            <div>
                <div class="brand-kicker">Quant Research Terminal</div>
                <p class="app-title">ALPHA<span>//</span>DESK</p>
                <p class="app-subtitle">{html.escape(t("app.subtitle", lang))}</p>
            </div>
            <div class="terminal-live"><span class="live-dot"></span>System online · {len(STOCK_UNIVERSE)} assets</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    render_primary_navigation(lang)
    route = st.session_state.app_route
    if route == "scan":
        params = build_sidebar()
        show_source_health()
        st.sidebar.markdown("<hr style='margin:1.2rem 0;'>", unsafe_allow_html=True)
        export_csv()
        render_scan_page(params, lang)
    elif route == "picks":
        params = build_minimal_sidebar()
        render_deep_workspace(lang, force_refresh=params["force_refresh"])
    else:
        build_minimal_sidebar()
        render_home_tab(lang)


if __name__ == "__main__":
    main()
