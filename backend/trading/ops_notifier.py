"""
Operational Telegram notifications for the trading engine.

Reuses the existing TelegramSettings (backend/config.py) and the same
sendMessage HTTP pattern as backend/telegram_notifier.py, but provides
formatters for trading-engine ops events: trade fills, circuit-breaker
(kill switch) trips, and daily P&L summaries.

All sends are best-effort: failures are logged, never raised, so that a
notification outage cannot halt or crash the trading worker.
"""
from __future__ import annotations

import html
import logging
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

_TELEGRAM_TIMEOUT = 20


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=False)


def _send(settings: Any, text: str) -> bool:
    """
    Best-effort send. Returns True on success, False on any failure.

    Never raises — ops notifications must not disrupt the trading loop.
    """
    if settings is None or not getattr(settings, "configured", False):
        logger.debug("Telegram not configured; skipping ops notification.")
        return False

    url = f"https://api.telegram.org/bot{settings.bot_token}/sendMessage"
    try:
        response = requests.post(
            url,
            json={
                "chat_id": settings.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=_TELEGRAM_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.warning("Telegram ops notification failed (request): %s", exc)
        return False
    if not response.ok:
        logger.warning("Telegram ops notification failed: HTTP %s", response.status_code)
        return False
    return True


def notify_trade_fill(
    settings: Any,
    *,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    mode: str = "paper",
    order_id: Optional[str] = None,
) -> bool:
    """Notify on an order fill (buy or sell)."""
    side_upper = str(side).upper()
    emoji = "🟢" if side_upper == "BUY" else "🔴"
    notional = float(quantity) * float(price)
    lines = [
        f"{emoji} <b>Trade Filled [{_escape(mode.upper())}]</b>",
        "",
        f"<b>Symbol:</b> {_escape(symbol)}",
        f"<b>Side:</b> {_escape(side_upper)}",
        f"<b>Quantity:</b> {float(quantity):,.4f}",
        f"<b>Fill Price:</b> ${float(price):,.2f}",
        f"<b>Notional:</b> ${notional:,.2f}",
    ]
    if order_id:
        lines.append(f"<b>Order ID:</b> {_escape(order_id)}")
    return _send(settings, "\n".join(lines))


def notify_kill_switch(
    settings: Any,
    *,
    reason: str,
    triggered_by: str = "system",
    details: Optional[Dict[str, Any]] = None,
) -> bool:
    """Notify when the circuit breaker / kill switch trips (trading halted)."""
    lines = [
        "🛑 <b>CIRCUIT BREAKER TRIPPED</b>",
        "",
        f"<b>Reason:</b> {_escape(reason)}",
        f"<b>Triggered by:</b> {_escape(triggered_by)}",
    ]
    if details:
        for key, value in details.items():
            lines.append(f"<b>{_escape(key)}:</b> {_escape(value)}")
    lines.extend([
        "",
        "Trading is halted. Manual review and resume required.",
    ])
    return _send(settings, "\n".join(lines))


def notify_daily_pnl(
    settings: Any,
    *,
    date: str,
    total_return_pct: float,
    daily_pnl: float,
    equity: float,
    num_trades: int = 0,
    win_rate_pct: Optional[float] = None,
    mode: str = "paper",
) -> bool:
    """Notify with an end-of-day P&L summary."""
    trend = "📈" if daily_pnl >= 0 else "📉"
    lines = [
        f"{trend} <b>Daily P&amp;L Summary [{_escape(mode.upper())}]</b>",
        "",
        f"<b>Date:</b> {_escape(date)}",
        f"<b>Day P&amp;L:</b> ${daily_pnl:,.2f}",
        f"<b>Total Return:</b> {total_return_pct:+.2f}%",
        f"<b>Equity:</b> ${equity:,.2f}",
        f"<b>Trades:</b> {int(num_trades)}",
    ]
    if win_rate_pct is not None:
        lines.append(f"<b>Win Rate:</b> {win_rate_pct:.1f}%")
    return _send(settings, "\n".join(lines))
