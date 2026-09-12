"""
Operational Telegram notifications for the trading engine.

Reuses TelegramSettings (backend/config.py). Failures are logged, never raised.
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


def _ready(settings: Any) -> bool:
    if settings is None:
        return False
    if hasattr(settings, "ops_configured"):
        return bool(settings.ops_configured)
    return bool(getattr(settings, "configured", False))


def send_plain(settings: Any, text: str, reply_markup: Optional[Dict[str, Any]] = None) -> bool:
    """Send a UTF-8 HTML message. Used by ops alerts and the inbound command bot."""
    return _send(settings, text, reply_markup=reply_markup)


def _send(settings: Any, text: str, reply_markup: Optional[Dict[str, Any]] = None) -> bool:
    if not _ready(settings):
        logger.debug("Telegram not configured; skipping ops notification.")
        return False

    url = f"https://api.telegram.org/bot{settings.bot_token}/sendMessage"
    payload: Dict[str, Any] = {
        "chat_id": settings.chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        response = requests.post(
            url,
            json=payload,
            timeout=_TELEGRAM_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.warning("Telegram ops notification failed (request): %s", exc)
        return False
    if not response.ok:
        logger.warning("Telegram ops notification failed: HTTP %s", response.status_code)
        return False
    return True


def _num(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _levels_lines(
    *,
    entry: Any,
    stop: Any,
    target: Any,
    quantity: Any,
    side: str = "BUY",
) -> list[str]:
    entry_px = _num(entry)
    stop_px = _num(stop)
    target_px = _num(target)
    qty = _num(quantity)
    lines: list[str] = []
    is_buy = str(side).upper() != "SELL"

    if stop_px is not None:
        lines.append(f"<b>Stop loss:</b> ${stop_px:,.2f}")
        if entry_px and entry_px > 0:
            risk_pct = ((entry_px - stop_px) / entry_px * 100) if is_buy else ((stop_px - entry_px) / entry_px * 100)
            risk_usd = ((entry_px - stop_px) * qty) if (qty and is_buy) else None
            if risk_usd is not None:
                lines.append(f"<b>Risk:</b> {abs(risk_pct):.1f}% · ${abs(risk_usd):,.2f}")
            else:
                lines.append(f"<b>Risk:</b> {abs(risk_pct):.1f}%")

    if target_px is not None:
        lines.append(f"<b>Target (T1):</b> ${target_px:,.2f}")
        if entry_px and entry_px > 0 and is_buy:
            gain_pct = (target_px - entry_px) / entry_px * 100
            gain_usd = (target_px - entry_px) * qty if qty else None
            if gain_usd is not None:
                lines.append(f"<b>Predicted gain:</b> {gain_pct:.1f}% · ${gain_usd:,.2f}")
            else:
                lines.append(f"<b>Predicted gain:</b> {gain_pct:.1f}%")

    if entry_px and stop_px and target_px:
        risk = abs(entry_px - stop_px)
        reward = abs(target_px - entry_px)
        if risk > 0:
            lines.append(f"<b>R/R:</b> {reward / risk:.2f}")
    return lines


def format_trade_notice(
    *,
    kind: str,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    mode: str = "paper",
    stop_loss: Any = None,
    take_profit: Any = None,
    strategy: Optional[str] = None,
    reason: Optional[str] = None,
    order_id: Optional[str] = None,
    status: Optional[str] = None,
) -> str:
    side_upper = str(side).upper()
    kind_key = str(kind or "fill").lower()
    if kind_key == "decision":
        title = f"Paper auto decided to {side_upper}"
        emoji = "📌"
    else:
        title = "Trade filled"
        emoji = "🟢" if side_upper == "BUY" else "🔴"
    notional = float(quantity) * float(price)
    lines = [
        f"{emoji} <b>{_escape(title)} [{_escape(mode.upper())}]</b>",
        "",
        f"<b>Symbol:</b> {_escape(symbol)}",
        f"<b>Side:</b> {_escape(side_upper)}",
        f"<b>Quantity:</b> {float(quantity):,.4f}",
        f"<b>Price:</b> ${float(price):,.2f}",
        f"<b>Notional:</b> ${notional:,.2f}",
    ]
    if strategy:
        lines.append(f"<b>Strategy:</b> {_escape(strategy)}")
    if status:
        lines.append(f"<b>Status:</b> {_escape(status)}")
    lines.extend(
        _levels_lines(
            entry=price,
            stop=stop_loss,
            target=take_profit,
            quantity=quantity,
            side=side_upper,
        )
    )
    if reason:
        lines.append(f"<b>Why:</b> {_escape(str(reason)[:240])}")
    if order_id:
        lines.append(f"<b>Order ID:</b> {_escape(order_id)}")
    if kind_key == "decision":
        lines.extend(["", "Submitted to Alpaca paper. A fill message follows if/when it executes."])
    return "\n".join(lines)


def notify_trade_decision(
    settings: Any,
    *,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    mode: str = "paper",
    stop_loss: Any = None,
    take_profit: Any = None,
    strategy: Optional[str] = None,
    reason: Optional[str] = None,
    order_id: Optional[str] = None,
    status: Optional[str] = None,
) -> bool:
    return _send(
        settings,
        format_trade_notice(
            kind="decision",
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            mode=mode,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strategy=strategy,
            reason=reason,
            order_id=order_id,
            status=status,
        ),
    )


def notify_trade_fill(
    settings: Any,
    *,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    mode: str = "paper",
    order_id: Optional[str] = None,
    stop_loss: Any = None,
    take_profit: Any = None,
    strategy: Optional[str] = None,
    reason: Optional[str] = None,
    status: Optional[str] = None,
) -> bool:
    """Notify on an order fill (buy or sell)."""
    return _send(
        settings,
        format_trade_notice(
            kind="fill",
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            mode=mode,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strategy=strategy,
            reason=reason,
            order_id=order_id,
            status=status or "filled",
        ),
    )


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
    """Notify with an end-of-day P&amp;L summary."""
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
