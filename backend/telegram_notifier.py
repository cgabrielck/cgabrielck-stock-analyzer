import html
from typing import Any, Dict

import requests

from config import TelegramSettings


def send_alert_message(settings: TelegramSettings, event: Dict[str, Any]) -> None:
    if not settings.configured:
        raise RuntimeError("Telegram notification settings are incomplete")

    url = f"https://api.telegram.org/bot{settings.bot_token}/sendMessage"
    try:
        response = requests.post(
            url,
            json={
                "chat_id": settings.chat_id,
                "text": format_alert_message(event),
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
    except requests.RequestException:
        raise RuntimeError("Telegram request failed") from None
    if not response.ok:
        raise RuntimeError(f"Telegram API returned HTTP {response.status_code}")


def format_alert_message(event: Dict[str, Any]) -> str:
    ticker = _escape(event.get("ticker") or "UNKNOWN")
    event_type = str(event.get("event_type") or "alert")
    details = event.get("event_data") or {}
    signal = _escape(_signal_label(event_type, details))
    price = float(event.get("price") or 0)
    quote_time = _escape(event.get("quote_time") or "N/A")
    instrument = _escape(details.get("instrument_type") or "stock")
    contract = _escape(details.get("monitor_symbol") or "N/A")

    lines = [
        f"<b>Stock Analyzer: {signal}</b>",
        "",
        f"<b>Ticker:</b> {ticker}",
        f"<b>Observed price:</b> ${price:,.2f}",
        f"<b>Quote time:</b> {quote_time}",
        f"<b>Rule:</b> {_escape(event_type)}",
        f"<b>Instrument:</b> {instrument}",
        f"<b>Contract:</b> {contract}",
        "",
        "Research information only. Not an order or personalized financial advice.",
    ]
    if event_type == "option_entry":
        lines.extend([
            "",
            "<b>Entry opportunity only.</b> No fill or position was recorded.",
            "Confirm the simulated fill in Saved Plans before stop and target alerts activate.",
        ])
    return "\n".join(lines)


def _signal_label(event_type: str, event_data: Dict[str, Any]) -> str:
    option_type = str(event_data.get("option_type") or "").upper()
    if event_type == "option_entry":
        return "OPTION ENTRY OPPORTUNITY"
    if event_type in {"entry_zone", "confirmation"}:
        return option_type or "BUY"
    if event_type in {"stop", "option_stop"}:
        return "EXIT / STOP"
    if event_type.startswith("target_") or event_type.startswith("option_target_"):
        return "TAKE PROFIT"
    if event_type == "option_expiry":
        return "OPTION EXPIRY"
    return event_type.replace("_", " ").upper()


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=False)
