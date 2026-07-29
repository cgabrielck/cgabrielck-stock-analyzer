from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st
import yfinance as yf

from accounts.repository import AccountRepository, AccountStorageError
from accounts.session import current_context
from i18n import t
from saved_plans import (
    ALERT_EVENT_TYPES, STOCK_ALERT_EVENT_TYPES, OPTION_ALERT_EVENT_TYPES,
    alert_rule_data, build_saved_plan, is_saveable_plan, plan_changes,
)
from saved_plan_outcomes import OUTCOME_HORIZONS, evaluate_saved_plan_outcome


def render_saved_plan_controls(
    ticker: str,
    result: Dict[str, Any],
    lang: str,
    repository: Optional[AccountRepository],
) -> None:
    context = current_context(st.session_state)
    if not context.is_authenticated:
        st.caption(t("saved_plan.login_required", lang))
        return
    if repository is None:
        st.warning(t("account.not_configured", lang))
        return
    analysis_timestamp = st.session_state.get("picks_analyzed_at") or datetime.now(timezone.utc).isoformat()
    active = repository.get_active_plan(context.user.id, ticker)
    saveable = is_saveable_plan(result)
    plan_data = build_saved_plan(ticker, result, analysis_timestamp, lang) if saveable else None

    st.markdown("**" + t("saved_plan.title", lang) + "**")
    if active is None:
        if not saveable:
            st.warning(t("saved_plan.not_saveable", lang))
            return
        if st.button(t("saved_plan.save", lang), key=f"saved_plan_save_{ticker}", width="stretch"):
            try:
                repository.save_plan(context.user.id, ticker, plan_data, analysis_timestamp)
            except AccountStorageError as exc:
                st.error(t(f"account.storage_{exc.code}", lang))
                return
            st.session_state[f"saved_plan_notice_{ticker}"] = t("saved_plan.saved", lang, version=1)
            st.rerun()
        return

    changes = plan_changes(active.plan_data, plan_data) if plan_data else []
    st.caption(t("saved_plan.active", lang, version=active.version, time=_display_time(active.analysis_timestamp)))
    if not saveable:
        st.warning(t("saved_plan.not_saveable", lang))
    elif changes:
        rows = [{
            t("saved_plan.field", lang): t(f"saved_plan.field_{change['field']}", lang),
            t("saved_plan.previous", lang): _display_value(change["previous"]),
            t("saved_plan.current", lang): _display_value(change["current"]),
        } for change in changes]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        confirmed = st.checkbox(
            t("saved_plan.confirm_replace", lang), key=f"saved_plan_confirm_replace_{ticker}",
        )
        if st.button(
            t("saved_plan.replace", lang), key=f"saved_plan_replace_{ticker}",
            width="stretch", disabled=not confirmed,
        ):
            try:
                saved = repository.save_plan(context.user.id, ticker, plan_data, analysis_timestamp)
            except AccountStorageError as exc:
                st.error(t(f"account.storage_{exc.code}", lang))
                return
            st.session_state[f"saved_plan_notice_{ticker}"] = t("saved_plan.saved", lang, version=saved.version)
            st.rerun()
    elif saveable:
        st.info(t("saved_plan.no_changes", lang))

    notice = st.session_state.pop(f"saved_plan_notice_{ticker}", None)
    if notice:
        st.success(notice)
    _render_alert_rules(context.user.id, active, lang, repository)
    versions = repository.list_plan_versions(context.user.id, ticker)
    _render_history(versions, lang)
    _render_outcome_journal(context.user.id, ticker, versions, lang, repository)


def _render_alert_rules(user_id: str, active, lang: str, repository: AccountRepository) -> None:
    stance = active.plan_data.get("decision", {}).get("stance")
    allowed = list(STOCK_ALERT_EVENT_TYPES if stance in {"bullish", "bearish"} else STOCK_ALERT_EVENT_TYPES[:2])
    if active.plan_data.get("alert_levels", {}).get("options"):
        allowed.extend(OPTION_ALERT_EVENT_TYPES)
    existing = {
        rule.event_type for rule in repository.list_alert_rules(user_id, active.plan_id)
        if rule.plan_version == active.version
    }
    with st.expander(t("alerts.title", lang), expanded=False):
        st.caption(t("alerts.monitoring_note", lang))
        selected = []
        for event_type in allowed:
            if st.checkbox(
                t(f"alerts.{event_type}", lang), value=event_type in existing,
                key=f"alert_{active.plan_id}_{active.version}_{event_type}",
            ):
                selected.append(event_type)
        confirmed = st.checkbox(
            t("alerts.confirm", lang), key=f"alerts_confirm_{active.plan_id}_{active.version}",
        )
        if st.button(
            t("alerts.save", lang), key=f"alerts_save_{active.plan_id}_{active.version}",
            width="stretch", disabled=not confirmed,
        ):
            rules = [(event_type, alert_rule_data(active.plan_data, event_type)) for event_type in selected]
            try:
                repository.replace_alert_rules(user_id, active.plan_id, active.version, rules)
                st.success(t("alerts.saved_monitoring", lang, count=len(rules)))
            except AccountStorageError as exc:
                st.error(t(f"account.storage_{exc.code}", lang))


def _render_history(versions: List[Any], lang: str) -> None:
    with st.expander(t("saved_plan.history", lang), expanded=False):
        for version in versions:
            decision = version.plan_data.get("decision", {})
            levels = version.plan_data.get("levels", {})
            st.markdown(
                f"**v{version.version}** · {_display_time(version.analysis_timestamp)} · "
                f"{str(decision.get('stance', 'N/A')).upper()} / {str(decision.get('action', 'N/A')).upper()}"
            )
            st.caption(
                f"{t('saved_plan.entry', lang)} {_range(levels.get('entry_zone', {}))} · "
                f"{t('saved_plan.stop', lang)} {_display_value(levels.get('stop_loss'))} · "
                f"{t('saved_plan.targets', lang)} {' / '.join(_display_value(value) for value in levels.get('targets', []))}"
            )


def _render_outcome_journal(
    user_id: str, ticker: str, versions: List[Any], lang: str, repository: AccountRepository,
) -> None:
    if not versions:
        return
    with st.expander(t("saved_plan.outcomes.title", lang), expanded=False):
        st.caption(t("saved_plan.outcomes.methodology", lang))
        if st.button(t("saved_plan.outcomes.evaluate", lang), key=f"outcomes_evaluate_{ticker}", width="stretch"):
            start = min(pd.Timestamp(version.analysis_timestamp) for version in versions) - pd.Timedelta(days=7)
            end = pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=1)
            stock = yf.Ticker(ticker).history(
                start=start.date(), end=end.date(), interval="1d", auto_adjust=False, actions=True,
            )
            benchmark = yf.Ticker("SPY").history(
                start=start.date(), end=end.date(), interval="1d", auto_adjust=False, actions=True,
            )
            existing = {
                (outcome.plan_version, outcome.horizon_days, outcome.calculation_version)
                for outcome in repository.list_plan_outcomes(user_id, versions[0].plan_id)
            }
            saved_count = 0
            statuses = []
            for version in versions:
                for horizon in OUTCOME_HORIZONS:
                    if (version.version, horizon, 1) in existing:
                        continue
                    evaluation = evaluate_saved_plan_outcome(
                        version, stock, benchmark, horizon, evaluated_at=datetime.now(timezone.utc),
                    )
                    if evaluation["status"] == "complete":
                        repository.record_plan_outcome(user_id, version.plan_id, version.version, evaluation)
                        saved_count += 1
                    else:
                        statuses.append(f"v{version.version} {horizon}d: {evaluation['status']}")
            st.success(t("saved_plan.outcomes.saved_count", lang, count=saved_count))
            if statuses:
                st.caption(" · ".join(statuses))
        outcomes = repository.list_plan_outcomes(user_id, versions[0].plan_id)
        if not outcomes:
            st.info(t("saved_plan.outcomes.none", lang))
            return
        rows = []
        for outcome in outcomes:
            data = outcome.outcome_data
            events = [name for name, event in data.get("level_events", {}).items() if isinstance(event, dict) and event.get("hit")]
            rows.append({
                "Version": f"v{outcome.plan_version}",
                t("saved_plan.outcomes.horizon", lang): f"{outcome.horizon_days}d",
                t("saved_plan.outcomes.period", lang): f"{data.get('anchor_date')} - {data.get('endpoint_date')}",
                t("saved_plan.outcomes.raw_return", lang): _percent_value(data.get("raw_return_pct")),
                t("saved_plan.outcomes.benchmark_return", lang): _percent_value(data.get("benchmark_return_pct")),
                t("saved_plan.outcomes.alpha", lang): _percent_value(data.get("raw_alpha_pct")),
                t("saved_plan.outcomes.directional_return", lang): _percent_value(data.get("directional_return_pct")),
                t("saved_plan.outcomes.mfe", lang): _percent_value(data.get("mfe_pct")),
                t("saved_plan.outcomes.mae", lang): _percent_value(data.get("mae_pct")),
                t("saved_plan.outcomes.level_events", lang): ", ".join(events) or "-",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _display_time(value: Any) -> str:
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(value or "N/A")


def _display_value(value: Any) -> str:
    return f"${value:,.2f}" if isinstance(value, (int, float)) else str(value or "N/A")


def _range(entry: Dict[str, Any]) -> str:
    return f"{_display_value(entry.get('low'))} - {_display_value(entry.get('high'))}"


def _percent_value(value: Any) -> str:
    return f"{value:+.2f}%" if isinstance(value, (int, float)) else "N/A"
