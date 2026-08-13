from typing import Callable, Dict, List

import streamlit as st

from backend.i18n import t
from backend.utils.constants import STOCK_UNIVERSE


def _tour_steps(lang: str) -> List[Dict[str, str]]:
    return [
        {
            "icon": "👋",
            "title": t("onboarding.step1.title", lang),
            "body": t("onboarding.step1.body", lang),
        },
        {
            "icon": "🔬",
            "title": t("onboarding.step2.title", lang),
            "body": t("onboarding.step2.body", lang),
        },
        {
            "icon": "📡",
            "title": t("onboarding.step3.title", lang),
            "body": t("onboarding.step3.body", lang, n=len(STOCK_UNIVERSE)),
        },
        {
            "icon": "💼",
            "title": t("onboarding.step4.title", lang),
            "body": t("onboarding.step4.body", lang),
        },
        {
            "icon": "🤖",
            "title": t("onboarding.step5.title", lang),
            "body": t("onboarding.step5.body", lang),
        },
        {
            "icon": "📱",
            "title": t("onboarding.step6.title", lang),
            "body": t("onboarding.step6.body", lang),
        },
    ]


def _finish_tour() -> None:
    st.session_state["onboarding_seen"] = True
    st.session_state["onboarding_open"] = False


def _tour_dialog_body(lang: str) -> None:
    steps = _tour_steps(lang)
    total = len(steps)
    current = min(st.session_state.get("onboarding_step", 0), total - 1)
    step = steps[current]

    dots = "".join(
        f"<span style='display:inline-block;width:{'18px' if i == current else '6px'};height:6px;"
        f"border-radius:3px;margin:0 3px;background:{'var(--cyan)' if i == current else 'var(--line-hot)'};"
        "transition:all .2s;'></span>"
        for i in range(total)
    )
    st.markdown(
        f"<div style='text-align:center;margin-bottom:.4rem;'>{dots}</div>"
        f"<div style='text-align:center;font-size:2.4rem;line-height:1;margin-bottom:.5rem;'>{step['icon']}</div>"
        f"<div style='text-align:center;font-size:1.15rem;font-weight:750;color:var(--text);margin-bottom:.5rem;'>{step['title']}</div>"
        f"<div style='text-align:center;font-size:.85rem;color:var(--muted);line-height:1.6;max-width:420px;margin:0 auto 1.1rem;'>{step['body']}</div>",
        unsafe_allow_html=True,
    )
    st.caption(t("onboarding.progress", lang, current=current + 1, total=total))

    col_skip, col_prev, col_next = st.columns([1, 1, 1])
    with col_skip:
        if st.button(t("onboarding.skip", lang), key="onboarding_skip", width="stretch"):
            _finish_tour()
            st.rerun()
    with col_prev:
        if current > 0:
            if st.button(t("onboarding.prev", lang), key="onboarding_prev", width="stretch"):
                st.session_state.onboarding_step = current - 1
                st.rerun()
    with col_next:
        is_last = current == total - 1
        label = t("onboarding.done", lang) if is_last else t("onboarding.next", lang)
        if st.button(label, key="onboarding_next", type="primary", width="stretch"):
            if is_last:
                _finish_tour()
            else:
                st.session_state.onboarding_step = current + 1
            st.rerun()


def maybe_render_onboarding_tour(lang: str) -> None:
    """Shows the first-time onboarding tour, or a re-opened tour on demand."""
    if "onboarding_seen" not in st.session_state:
        st.session_state.onboarding_seen = False
    if "onboarding_open" not in st.session_state:
        st.session_state.onboarding_open = not st.session_state.onboarding_seen

    if st.session_state.onboarding_open:
        st.session_state.setdefault("onboarding_step", 0)

        @st.dialog(t("onboarding.dialog_title", lang), width="medium", dismissible=True, on_dismiss=_finish_tour)
        def _tour_dialog() -> None:
            _tour_dialog_body(lang)

        _tour_dialog()


def render_onboarding_reopen_button(lang: str) -> None:
    """Sidebar entry point so returning users can replay the tour anytime."""
    if st.sidebar.button(t("onboarding.reopen", lang), key="onboarding_reopen_btn", width="stretch"):
        st.session_state.onboarding_step = 0
        st.session_state.onboarding_open = True
        st.rerun()
