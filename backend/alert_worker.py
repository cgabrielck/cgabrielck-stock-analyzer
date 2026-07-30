import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict

import yfinance as yf

from accounts.repository import SupabaseAccountRepository
from alert_monitor import evaluate_alert_transition, event_idempotency_key, quote_is_fresh
from config import get_account_settings
from config import get_telegram_settings
from agents.options_quote_adapter import fetch_option_quote
from agents.options_risk_agent import evaluate_option_risk
from telegram_notifier import send_alert_message
from utils.price_utils import get_latest_quote


LOGGER = logging.getLogger(__name__)


def check_alerts(repository: SupabaseAccountRepository, owner_user_id: str) -> Dict[str, int]:
    rules = repository.list_enabled_alerts(owner_user_id)
    quotes: Dict[Any, Dict[str, Any]] = {}
    result = {"rules": len(rules), "evaluated": 0, "triggered": 0, "stale": 0, "rejected": 0}
    for rule in rules:
        ticker = rule.get("ticker")
        if not ticker:
            continue
        monitor_symbol = rule.get("rule_data", {}).get("monitor_symbol") or ticker
        is_option = rule.get("rule_data", {}).get("instrument_type") == "option"
        quote_key = (monitor_symbol, rule.get("event_type")) if is_option else monitor_symbol
        if quote_key not in quotes:
            quotes[quote_key] = (
                fetch_option_quote(ticker, rule.get("rule_data", {}), rule.get("event_type", ""))
                if is_option else get_latest_quote(yf.Ticker(monitor_symbol))
            )
        quote = quotes[quote_key]
        if not quote_is_fresh(quote):
            result["stale"] += 1
            continue
        risk = evaluate_option_risk(quote, rule.get("rule_data", {})) if is_option else None
        if risk and not risk["hard_gate_passed"]:
            result["rejected"] += 1
            continue
        price = float(quote["price"])
        transition = evaluate_alert_transition(
            rule, float(rule["last_price"]) if rule.get("last_price") is not None else None,
            price, armed=bool(rule.get("armed", True)),
        )
        quote_time = _iso_quote_time(quote["quote_time"])
        repository.record_alert_evaluation(
            rule["id"], price, quote_time, transition["armed"], transition["triggered"],
            event_idempotency_key(rule["id"], quote_time, price),
            {
                "source": quote.get("source"), "session": quote.get("session"),
                "instrument_type": rule.get("rule_data", {}).get("instrument_type", "stock"),
                "monitor_symbol": monitor_symbol,
                "option_type": rule.get("rule_data", {}).get("option_type"),
                "bid": quote.get("bid"), "ask": quote.get("ask"),
                "spread_pct": quote.get("spread_pct"),
                "agent_trace": quote.get("agent_trace", []),
                "risk_judge": risk,
            },
        )
        result["evaluated"] += 1
        result["triggered"] += int(transition["triggered"])
    result["delivered"] = deliver_pending_alerts(repository, owner_user_id)
    return result


def deliver_pending_alerts(repository: SupabaseAccountRepository, owner_user_id: Optional[str] = None) -> int:
    settings = get_telegram_settings()
    if not settings.configured:
        return 0
    sent = 0
    delivery_user_id = owner_user_id or settings.owner_user_id
    for event in repository.claim_pending_alert_deliveries(delivery_user_id):
        try:
            send_alert_message(settings, event)
            repository.record_alert_delivery(event["id"], True)
            sent += 1
        except Exception as exc:
            LOGGER.exception("Telegram delivery failed for event %s", event.get("id"))
            repository.record_alert_delivery(event["id"], False, str(exc))
    return sent


def run_forever(interval_seconds: int = 60) -> None:
    settings = get_account_settings()
    if not settings.configured:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
    repository = SupabaseAccountRepository(settings.supabase_url, settings.supabase_service_role_key)
    telegram = get_telegram_settings()
    if not telegram.configured:
        raise RuntimeError("Telegram settings and ALERT_OWNER_USER_ID are required")
    while True:
        started = time.monotonic()
        try:
            LOGGER.info("Alert check completed: %s", check_alerts(repository, telegram.owner_user_id))
        except Exception:
            LOGGER.exception("Alert check failed")
        time.sleep(max(1, interval_seconds - int(time.monotonic() - started)))


def _iso_quote_time(value: Any) -> str:
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, (int, float)):
        timestamp = datetime.fromtimestamp(value, tz=timezone.utc)
    else:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat()


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    run_forever(int(os.getenv("ALERT_INTERVAL_SECONDS", "60")))
