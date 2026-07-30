import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from i18n import _TR


ACCOUNT_KEYS = {
    "account.title", "account.login", "account.register", "account.logout",
    "account.username", "account.pin", "account.username_not_found",
    "account.incorrect_pin", "account.username_taken", "account.username_required",
    "account.signed_in_as", "account.not_configured", "account.save_preferences",
    "account.preferences_saved", "favorites.title", "favorites.ticker",
    "account.storage_migration_required", "account.storage_migration_update_required",
    "account.storage_permission_denied", "account.storage_database_error",
    "favorites.add", "favorites.empty",
    "saved_plan.title", "saved_plan.login_required", "saved_plan.not_saveable",
    "saved_plan.save", "saved_plan.replace", "saved_plan.confirm_replace", "saved_plan.saved",
    "saved_plan.active", "saved_plan.no_changes", "saved_plan.history", "saved_plan.field",
    "saved_plan.previous", "saved_plan.current", "saved_plan.field_stance", "saved_plan.field_action",
    "saved_plan.field_entry_low", "saved_plan.field_entry_high", "saved_plan.field_confirmation",
    "saved_plan.field_stop", "saved_plan.field_target_1", "saved_plan.field_target_2",
    "saved_plan.entry", "saved_plan.stop", "saved_plan.targets", "alerts.title",
    "alerts.pending_worker", "alerts.monitoring_note", "alerts.entry_zone", "alerts.confirmation", "alerts.stop",
    "alerts.target_1", "alerts.target_2", "alerts.save", "alerts.saved_pending",
    "alerts.saved_monitoring", "alerts.inbox", "alerts.inbox_empty", "alerts.inbox_event", "alerts.mark_read",
    "chart.pattern_status", "chart.download_pine",
    "chart.horizon", "chart.horizon_year", "chart.horizon_short", "chart.horizon_entry", "chart.horizon_today",
    "chart.detail_mode", "chart.simple_explainer", "chart.detail_explainer", "chart.gesture_hint", "chart.indicator_guide",
    "deep.sec_evidence", "deep.sec_filing_meta", "deep.sec_view_filing", "deep.sec_source_caption",
    "deep.sec_excerpt_note", "deep.sec_unavailable", "deep.sec_timeout", "deep.sec_not_found",
    "portfolio.portfolio_volatility", "portfolio.daily_var_95", "portfolio.history_coverage",
    "portfolio.cash_weight", "portfolio.coverage_warning", "portfolio.stress_tests",
    "portfolio.scenario", "portfolio.equity_shock", "portfolio.stress_loss",
    "portfolio.portfolio_change", "portfolio.stressed_value",
    "portfolio.scenario.market_correction_10", "portfolio.scenario.bear_market_20",
    "portfolio.target_weight", "portfolio.actual_weight", "portfolio.market_value",
    "saved_plan.outcomes.title", "saved_plan.outcomes.evaluate", "saved_plan.outcomes.none",
    "saved_plan.outcomes.methodology", "saved_plan.outcomes.saved_count", "saved_plan.outcomes.horizon",
    "saved_plan.outcomes.period", "saved_plan.outcomes.raw_return", "saved_plan.outcomes.benchmark_return",
    "saved_plan.outcomes.alpha", "saved_plan.outcomes.directional_return", "saved_plan.outcomes.mfe",
    "saved_plan.outcomes.mae", "saved_plan.outcomes.level_events",
}


def test_account_translations_exist_in_all_languages() -> None:
    for language in ("zh_cn", "zh_tw", "en"):
        assert ACCOUNT_KEYS <= _TR[language].keys()
