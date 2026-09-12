"""Inbound Telegram commands for paper ops (poll getUpdates).

Only TELEGRAM_CHAT_ID may issue commands. No buy/sell by text — RiskEngine stays
the only path that can place orders.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import requests

from backend.utils.constants import DATA_DIR

logger = logging.getLogger(__name__)

OFFSET_PATH = Path(DATA_DIR) / "telegram_updates_offset.json"
_POLL_TIMEOUT = 25
_HELP = (
    "請用下方選單點選功能。\n"
    "\n"
    "狀態／持倉 — 查看自動交易與模擬盤\n"
    "啟動／停止 — 紙上自動交易\n"
    "急停／恢復 — 暫停新買入或解除\n"
    "掃描 — 掃 74 檔\n"
    "\n"
    "沒有「買入」按鈕。下單必須通過風控。"
)

_ALIASES = {
    "start": "help",
    "help": "help",
    "幫助": "help",
    "说明": "help",
    "說明": "help",
    "指令": "help",
    "選單": "help",
    "菜单": "help",
    "menu": "help",
    "狀態": "status",
    "状态": "status",
    "status": "status",
    "啟動": "start_auto",
    "启动": "start_auto",
    "開始": "start_auto",
    "开始": "start_auto",
    "start_auto": "start_auto",
    "停止": "stop_auto",
    "stop": "stop_auto",
    "stop_auto": "stop_auto",
    "急停": "halt",
    "halt": "halt",
    "kill": "halt",
    "恢復": "resume",
    "恢复": "resume",
    "resume": "resume",
    "掃描": "scan",
    "扫描": "scan",
    "scan": "scan",
    "持倉": "positions",
    "持仓": "positions",
    "倉位": "positions",
    "positions": "positions",
}

_ORDER_WORDS = {"買", "賣", "买", "卖", "buy", "sell", "下單", "下单", "order"}

_inbox_stop = threading.Event()
_inbox_thread: Optional[threading.Thread] = None


def parse_command(text: str) -> Tuple[str, str]:
    raw = (text or "").strip()
    if not raw:
        return "empty", ""
    token = raw.splitlines()[0].strip().lstrip("/")
    first = token.split()[0] if token else ""
    lowered = first.lower()
    if first in _ORDER_WORDS or lowered in _ORDER_WORDS:
        return "reject_order", first
    if first in _ALIASES:
        return _ALIASES[first], first
    if lowered in _ALIASES:
        return _ALIASES[lowered], first
    return "unknown", first


def reply_keyboard() -> Dict[str, Any]:
    """Persistent Telegram reply keyboard — tap sends the button label as text."""
    return {
        "keyboard": [
            [{"text": "狀態"}, {"text": "持倉"}],
            [{"text": "啟動"}, {"text": "停止"}],
            [{"text": "急停"}, {"text": "恢復"}],
            [{"text": "掃描"}, {"text": "選單"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "one_time_keyboard": False,
    }


def allowed_chat(chat_id: Any, expected: str) -> bool:
    want = str(expected or "").strip()
    got = str(chat_id or "").strip()
    return bool(want) and got == want


def _read_offset() -> int:
    try:
        data = json.loads(OFFSET_PATH.read_text(encoding="utf-8"))
        return int(data.get("offset") or 0)
    except Exception:
        return 0


def _write_offset(offset: int) -> None:
    OFFSET_PATH.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_PATH.write_text(json.dumps({"offset": int(offset)}), encoding="utf-8")


def handle_command(command: str) -> str:
    if command == "help":
        return _HELP
    if command == "empty":
        return _HELP
    if command == "reject_order":
        return "不會從 Telegram 下單。請用工作台或等紙上自動交易通過風控後再買。"
    if command == "unknown":
        return "無法辨識。請點下方選單，或傳「選單」。不會從這裡下單。"
    if command == "status":
        return _cmd_status()
    if command == "start_auto":
        return _cmd_start_auto()
    if command == "stop_auto":
        return _cmd_stop_auto()
    if command == "halt":
        return _cmd_halt()
    if command == "resume":
        return _cmd_resume()
    if command == "scan":
        return _cmd_scan()
    if command == "positions":
        return _cmd_positions()
    return _HELP


def handle_update(update: Dict[str, Any], expected_chat_id: str) -> Optional[str]:
    msg = update.get("message") or update.get("edited_message") or {}
    chat = msg.get("chat") or {}
    if not allowed_chat(chat.get("id"), expected_chat_id):
        logger.info("Ignored Telegram update from chat_id=%s", chat.get("id"))
        return None
    text = msg.get("text") or ""
    command, _token = parse_command(text)
    return handle_command(command)


def _cmd_status() -> str:
    from backend.api import research_jobs, worker_ctl
    from backend.trading.safety import kill_switch

    st = worker_ctl.status()
    scan = research_jobs.latest_scan()
    auto = "開" if st.get("running") and not st.get("stale") else "關"
    halt = "是" if kill_switch.is_halted() else "否"
    picks = ", ".join((scan.get("top5_tickers") or [])[:5]) or "—"
    stale = "過期" if st.get("scan_stale") or scan.get("stale") or not scan.get("available") else "新鮮"
    as_of = str(scan.get("ts") or st.get("scan_as_of") or "—").replace("T", " ")[:19]
    next_scan = str(st.get("next_scan_at") or "—").replace("T", " ")[:16]
    scan_auto = "開" if st.get("scan_auto") else "關"
    from backend.trading.strategies.skip_codes import format_skip_lines

    skips = format_skip_lines(st.get("skip_counts") or {}, lang="zh")
    cap = st.get("universe_cap")
    size = st.get("universe_size")
    fetched = st.get("fetched")
    lines = [
        f"PAPER 自動交易：{auto}",
        f"策略：{st.get('strategy') or '—'}",
        f"急停：{halt}",
        f"掃描：{stale} · as-of {as_of} · {picks}",
        f"自動掃描：{scan_auto} · 下次 {next_scan}",
        f"宇宙：抓 {fetched or '—'}／上限 {cap or '—'}／共 {size or '—'}",
    ]
    if skips:
        lines.append(f"為何沒買：{skips}")
    lines.append(f"最近訊號：{st.get('last_signals') or '無'}")
    return "\n".join(lines)


def _cmd_start_auto() -> str:
    from backend.api import worker_ctl

    try:
        result = worker_ctl.start(strategy="stable", interval=60)
    except Exception as exc:
        return f"啟動失敗：{exc}"
    if result.get("already_running"):
        return "紙上自動交易本來就在跑。"
    return "已啟動紙上自動交易（stable）。頂欄應顯示 AUTO ON。"


def _cmd_stop_auto() -> str:
    from backend.api import worker_ctl

    try:
        worker_ctl.stop()
    except Exception as exc:
        return f"停止失敗：{exc}"
    return "已停止紙上自動交易。"


def _cmd_halt() -> str:
    from backend.trading.safety import kill_switch

    kill_switch.engage("halted via Telegram")
    return "急停已開：暫停新買入，賣出仍可執行。"


def _cmd_resume() -> str:
    from backend.trading.safety import kill_switch

    kill_switch.disengage()
    return "急停已解除。"


def _cmd_scan() -> str:
    from backend.api import research_jobs

    try:
        job = research_jobs.start_scan(lang="zh-TW", llm_weight=0.2, use_llm=True)
    except Exception as exc:
        return f"掃描無法開始：{exc}"
    return f"掃描已開始（{job.get('id', '')[:8]}…）。約 1–3 分鐘，完成後可傳「狀態」。"


def _cmd_positions() -> str:
    try:
        from backend.trading.alpaca_broker import AlpacaBroker

        account = AlpacaBroker().get_account_summary()
    except Exception as exc:
        return f"讀不到模擬盤：{exc}"
    rows = account.positions or []
    lines = [
        f"權益 ${float(account.portfolio_value or 0):,.0f} · 現金 ${float(account.cash or 0):,.0f}",
        f"持倉 {len(rows)} 檔",
    ]
    for pos in rows[:12]:
        lines.append(f"{pos.symbol} ×{pos.quantity:g}")
    if not rows:
        lines.append("空白帳本不算故障：還沒有通過風控的成交。")
    return "\n".join(lines)


def _reply(settings: Any, text: str) -> None:
    from backend.trading.ops_notifier import send_plain

    send_plain(
        settings,
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"),
        reply_markup=reply_keyboard(),
    )


def poll_once(settings: Any, timeout: int = _POLL_TIMEOUT) -> int:
    if not getattr(settings, "ops_configured", False):
        return 0
    offset = _read_offset()
    url = f"https://api.telegram.org/bot{settings.bot_token}/getUpdates"
    try:
        response = requests.get(
            url,
            params={"offset": offset, "timeout": timeout, "allowed_updates": json.dumps(["message"])},
            timeout=timeout + 10,
        )
    except requests.RequestException as exc:
        logger.debug("Telegram getUpdates failed: %s", exc)
        return 0
    payload = response.json() if response.ok else {}
    updates = payload.get("result") or []
    max_id = offset
    for update in updates:
        uid = int(update.get("update_id") or 0)
        max_id = max(max_id, uid + 1)
        reply = handle_update(update, settings.chat_id)
        if reply:
            _reply(settings, reply)
    if max_id != offset:
        _write_offset(max_id)
    return len(updates)


def _loop() -> None:
    from backend.config import get_telegram_settings

    logger.info("Telegram inbox polling started")
    while not _inbox_stop.is_set():
        try:
            get_telegram_settings.cache_clear()
            settings = get_telegram_settings()
            if not settings.ops_configured:
                _inbox_stop.wait(20)
                continue
            poll_once(settings)
        except Exception:
            logger.exception("Telegram inbox loop error")
            _inbox_stop.wait(5)
    logger.info("Telegram inbox polling stopped")


def start_background() -> Callable[[], None]:
    """Start the poller; returns a stop callback."""
    global _inbox_thread
    _inbox_stop.clear()
    if _inbox_thread and _inbox_thread.is_alive():
        return stop_background
    _inbox_thread = threading.Thread(target=_loop, name="telegram-inbox", daemon=True)
    _inbox_thread.start()
    return stop_background


def stop_background() -> None:
    _inbox_stop.set()
    thread = _inbox_thread
    if thread and thread.is_alive():
        thread.join(timeout=2.0)
