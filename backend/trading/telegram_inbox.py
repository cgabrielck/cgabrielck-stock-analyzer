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
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from backend.utils.constants import DATA_DIR

logger = logging.getLogger(__name__)

OFFSET_PATH = Path(DATA_DIR) / "telegram_updates_offset.json"
_POLL_TIMEOUT = 25
_HELP = (
    "請用下方選單點選功能。\n"
    "\n"
    "狀態／持倉 — 查看自動交易與模擬盤\n"
    "待買 — 已送券商、尚未成交的限價單（例如 GOOGL 28 股）\n"
    "日報 — 今日成交 vs 排隊、skip、紙上 vs SPY（扣成本）；未成交不算封印\n"
    "理由 — Scan 為何挑這些股；也可傳「為何 GOOGL」\n"
    "啟動／停止 — 紙上自動交易（啟動會用 AI Mode 已存策略）\n"
    "跟掃描 — 改為 research_list，只買最新 Scan 前 N 名（仍過 Stage-2 與風控；不會改成 defensive_gld）\n"
    "急停／恢復 — 暫停新買入或解除\n"
    "掃描 — 掃 74 檔\n"
    "\n"
    "防禦 GLD 與週頻節奏請到 Desk「AI 模式」手動選擇，Telegram 不會自動開啟。\n"
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
    "跟掃描": "follow_scan",
    "跟扫描": "follow_scan",
    "跟名單": "follow_scan",
    "跟名单": "follow_scan",
    "research_list": "follow_scan",
    "scanlist": "follow_scan",
    "follow_scan": "follow_scan",
    "持倉": "positions",
    "持仓": "positions",
    "倉位": "positions",
    "positions": "positions",
    "待買": "pending",
    "待买": "pending",
    "排隊": "pending",
    "排队": "pending",
    "pending": "pending",
    "queue": "pending",
    "日報": "digest",
    "日报": "digest",
    "digest": "digest",
    "daily": "digest",
    "理由": "why",
    "為何": "why",
    "为何": "why",
    "why": "why",
    "reason": "why",
}

_ORDER_WORDS = {"買", "賣", "买", "卖", "buy", "sell", "下單", "下单", "order"}

_inbox_stop = threading.Event()
_inbox_thread: Optional[threading.Thread] = None


def parse_command(text: str) -> Tuple[str, str]:
    raw = (text or "").strip()
    if not raw:
        return "empty", ""
    token = raw.splitlines()[0].strip().lstrip("/")
    parts = token.split()
    first = parts[0] if parts else ""
    rest = " ".join(parts[1:]).strip()
    lowered = first.lower()
    if first in _ORDER_WORDS or lowered in _ORDER_WORDS:
        return "reject_order", first
    if first in _ALIASES:
        return _ALIASES[first], rest
    if lowered in _ALIASES:
        return _ALIASES[lowered], rest
    return "unknown", first


def reply_keyboard() -> Dict[str, Any]:
    """Persistent Telegram reply keyboard — tap sends the button label as text."""
    return {
        "keyboard": [
            [{"text": "狀態"}, {"text": "持倉"}],
            [{"text": "待買"}, {"text": "理由"}],
            [{"text": "啟動"}, {"text": "停止"}],
            [{"text": "跟掃描"}, {"text": "掃描"}],
            [{"text": "急停"}, {"text": "恢復"}],
            [{"text": "選單"}, {"text": "日報"}],
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


def handle_command(command: str, argument: str = "") -> str:
    if command == "help":
        return _HELP
    if command == "empty":
        return _HELP
    if command == "reject_order":
        return "不會從 Telegram 下單。請用工作台或等紙上自動交易通過風控後再買。"
    if command == "unknown":
        maybe = str(argument or "").upper().strip()
        if _is_universe_ticker(maybe):
            return _cmd_why(maybe)
        return "無法辨識。請點下方選單，或傳「選單」。不會從這裡下單。"
    if command == "status":
        return _cmd_status()
    if command == "pending":
        return _cmd_pending()
    if command == "digest":
        return _cmd_digest()
    if command == "why":
        return _cmd_why(argument)
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
    if command == "follow_scan":
        return _cmd_follow_scan()
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
    command, argument = parse_command(text)
    return handle_command(command, argument)


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
    lines.append(_open_orders_brief())
    try:
        from backend.api.paper_performance import digest_status_line

        lines.append(digest_status_line())
    except Exception:
        lines.append("今日成交／排隊：點「日報」。未成交不算封印。")
    lines.append(f"最近訊號：{st.get('last_signals') or '無'}")
    if str(st.get("strategy") or "") != "research_list":
        lines.append("若要跟 Scan 前五名買，點「跟掃描」（仍過風控，不會從這裡直接下單）。")
    return "\n".join(lines)


def _is_universe_ticker(symbol: str) -> bool:
    from backend.utils.constants import STOCK_UNIVERSE

    want = str(symbol or "").upper().strip()
    return bool(want) and any(str(row.get("ticker") or "").upper() == want for row in STOCK_UNIVERSE)


def working_buy_orders() -> List[Dict[str, Any]]:
    """Open BUY orders from Alpaca paper. Empty list if none; raises on broker failure."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    from backend.trading.alpaca_broker import AlpacaBroker

    rows = AlpacaBroker().api.get_orders(
        filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=20)
    )
    out: List[Dict[str, Any]] = []
    for order in rows or []:
        if str(getattr(order.side, "value", order.side)).lower() != "buy":
            continue
        px = getattr(order, "limit_price", None)
        out.append(
            {
                "id": str(getattr(order, "id", "") or ""),
                "symbol": str(order.symbol or "").upper(),
                "qty": float(order.qty or 0),
                "limit_price": float(px) if px else None,
                "status": str(getattr(order.status, "value", order.status) or ""),
                "submitted_at": str(getattr(order, "submitted_at", "") or ""),
            }
        )
    return out


def _format_buy_order(order: Dict[str, Any]) -> str:
    px = order.get("limit_price")
    px_s = f" @ ${float(px):.2f}" if px else ""
    status = order.get("status") or "open"
    return f"{order.get('symbol')} 限價買 {float(order.get('qty') or 0):g}{px_s}（{status}）"


def _heartbeat_pending() -> List[str]:
    try:
        from backend.api import worker_ctl

        return [str(s).upper() for s in (worker_ctl.status().get("pending_buys") or []) if s]
    except Exception:
        return []


def _open_orders_brief() -> str:
    """Name working orders so status is not mistaken for the AAPL ticket default."""
    try:
        buys = working_buy_orders()
    except Exception as exc:
        pending = _heartbeat_pending()
        if pending:
            return f"未完成訂單：{', '.join(pending)}（Alpaca 連線暫時失敗，用本地紀錄）"
        return f"未完成訂單：讀不到（{exc}）"
    if not buys:
        return "未完成訂單：無。成交（filled）= 券商已買到股票入帳。"
    parts = [_format_buy_order(order) for order in buys[:5]]
    return (
        "未完成訂單："
        + "；".join(parts)
        + "。這不是等 AAPL。成交（filled）要等限價被碰到，券商才入帳。"
    )


def _cmd_pending() -> str:
    try:
        buys = working_buy_orders()
        broker_ok = True
    except Exception as exc:
        buys = []
        broker_ok = False
        broker_err = str(exc)
    lines = [
        "待買清單（已送 Alpaca paper，還沒成交）",
        "狀態 new／accepted = 排隊中。filled 才入帳。",
    ]
    if buys:
        for order in buys[:8]:
            submitted = str(order.get("submitted_at") or "").replace("T", " ")[:19]
            extra = f" · 送出 {submitted}" if submitted else ""
            lines.append("- " + _format_buy_order(order) + extra)
    else:
        pending = _heartbeat_pending()
        if pending:
            lines.append("本地心跳待買：" + ", ".join(pending))
            if not broker_ok:
                lines.append(f"Alpaca 連線失敗：{broker_err}")
        elif not broker_ok:
            lines.append(f"讀不到未完成訂單：{broker_err}")
        else:
            lines.append("目前沒有排隊中的買單。")
    lines.append("點「理由」看為何挑這些股。Telegram 不會從這裡下單。")
    return "\n".join(lines)


def _cmd_digest() -> str:
    try:
        from backend.api.paper_performance import build_daily_digest, format_digest_html

        digest = build_daily_digest()
        return format_digest_html(digest)
    except Exception as exc:
        return f"日報讀取失敗：{exc}\n未成交限價單不算封印。Telegram 不會從這裡下單。"


def _scan_pick(symbol: str) -> Optional[Dict[str, Any]]:
    try:
        from backend.api.research_jobs import latest_scan

        scan = latest_scan()
    except Exception:
        return None
    want = str(symbol or "").upper()
    top = [str(t).upper() for t in (scan.get("top5_tickers") or []) if t]
    recs = scan.get("recommendations") or []
    ranks = scan.get("rankings") or []
    rec = next((r for r in recs if str((r or {}).get("ticker") or "").upper() == want), None)
    rank_row = next((r for r in ranks if str((r or {}).get("ticker") or "").upper() == want), None)
    if rec is None and rank_row is None and want not in top:
        return None
    row = dict(rec or rank_row or {})
    rank = top.index(want) + 1 if want in top else None
    return {
        "ticker": want,
        "name": row.get("name_zh") or row.get("name") or want,
        "rank": rank,
        "in_top5": want in top,
        "top5": top,
        "risk_adjusted_score": row.get("risk_adjusted_score"),
        "growth_score": row.get("growth_score") or row.get("model_score"),
        "llm_key_signal": row.get("llm_key_signal"),
        "reasoning": str(row.get("reasoning") or "")[:220],
        "stale": bool(scan.get("stale") or not scan.get("available")),
        "as_of": str(scan.get("ts") or "")[:19],
    }


def _worker_skip_for(symbol: str) -> Optional[str]:
    try:
        from backend.api import worker_ctl

        by_ticker = worker_ctl.status().get("skip_by_ticker") or {}
    except Exception:
        return None
    if not isinstance(by_ticker, dict):
        return None
    code = by_ticker.get(str(symbol).upper())
    return str(code) if code else None


def _why_one(symbol: str) -> str:
    from backend.trading.strategies.skip_codes import label

    sym = str(symbol or "").upper().strip()
    if not sym:
        return _cmd_why("")
    pick = _scan_pick(sym)
    skip = _worker_skip_for(sym)
    order = None
    try:
        order = next((o for o in working_buy_orders() if o.get("symbol") == sym), None)
    except Exception:
        order = None
        if sym in _heartbeat_pending():
            order = {"symbol": sym, "qty": None, "limit_price": None, "status": "pending"}

    lines = [f"{sym} 選股／下單追蹤"]
    if pick:
        rank_s = f"Scan 第 {pick['rank']} 名" if pick.get("rank") else "不在 Scan 前五"
        score = pick.get("risk_adjusted_score")
        score_s = f"風險調整分 {score}" if score is not None else "尚無掃描分"
        lines.append(f"{pick.get('name') or sym} · {rank_s} · {score_s}")
        if pick.get("llm_key_signal"):
            lines.append(f"掃描 LLM 標籤：{pick['llm_key_signal']}（只當標籤，不下單）")
        if pick.get("reasoning"):
            lines.append(pick["reasoning"])
        if pick.get("stale"):
            lines.append("掃描／訊號 as-of 已過期，worker 不會再開新倉。")
    else:
        lines.append("這一檔不在最新 Scan 前五，自動買不會碰它。")

    if order:
        if order.get("qty") is not None:
            lines.append("已送券商：" + _format_buy_order(order))
        else:
            lines.append(f"本地心跳顯示 {sym} 有未完成買單。")
        lines.append("還在等限價成交，不是模型還在想。美股開盤碰到限價才入帳。")
    elif skip:
        lines.append(f"本輪沒買：{label(skip, 'zh')}（{skip}）")
        if skip == "not_stage2":
            lines.append("Scan 分數高不夠：價格還要在 Minervini Stage-2（站上 SMA50>150>200 且 SMA200 向上）。")
        elif skip == "not_in_scan_list":
            lines.append("跟掃描模式只買最新 Scan 前 N 名。")
    else:
        lines.append("本輪沒有這檔的未完成買單。點「待買」看排隊中的限價單。")
    lines.append("Stage-2／Kelly／$10k／RiskEngine 是毫秒規則閘，不是 LLM 長考。")
    return "\n".join(lines)


def _cmd_why(argument: str = "") -> str:
    from backend.trading.strategies.skip_codes import label

    ticker = str(argument or "").strip().upper()
    if ticker:
        return _why_one(ticker.split()[0])
    top: List[str] = []
    try:
        from backend.api.research_jobs import latest_scan

        scan = latest_scan()
        top = [str(t).upper() for t in (scan.get("top5_tickers") or []) if t]
    except Exception:
        top = []
    lines = ["為何挑這些股（Scan 前五 → 規則閘 → 限價單）"]
    if not top:
        lines.append("還沒有新鮮 Scan 名單。請點「掃描」。")
        return "\n".join(lines)
    for idx, sym in enumerate(top[:5], start=1):
        row = _scan_pick(sym) or {"ticker": sym}
        score = row.get("risk_adjusted_score")
        score_s = f"分 {score}" if score is not None else "—"
        skip = _worker_skip_for(sym)
        if skip == "order_open":
            gate = "已送限價單，等成交"
        elif skip:
            gate = label(skip, "zh")
        else:
            gate = "本輪未記錄閘碼"
        lines.append(f"{idx}. {sym} {score_s} — {gate}")
    try:
        buys = working_buy_orders()
    except Exception:
        buys = []
    if buys:
        lines.append("排隊中：" + "；".join(_format_buy_order(o) for o in buys[:5]))
    lines.append("傳「為何 GOOGL」可看單檔理由。閘門是規則計算，不是 AI 慢慢想。")
    return "\n".join(lines)


def _ai_mode_path() -> Path:
    return Path(DATA_DIR) / "ai_mode.json"


def _load_ai_mode() -> Dict[str, Any]:
    default = {
        "strategy": "breakout",
        "llm_influence": 20.0,
        "entry_threshold": 70.0,
        "max_positions": 10,
        "risk_tolerance": "medium",
        "worker_enabled": False,
        "ignore_market_hours": False,
        "cadence": "intraday",
    }
    path = _ai_mode_path()
    if not path.exists():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(default)
    if not isinstance(data, dict):
        return dict(default)
    return {**default, **data}


def _save_ai_mode(payload: Dict[str, Any]) -> None:
    path = _ai_mode_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _apply_research_list_threshold(threshold: float) -> None:
    try:
        from backend.trading.strategies.registry import get_strategy

        get_strategy("research_list").MIN_FUND_SCORE = float(threshold or 65)
    except Exception:
        pass


def _set_saved_strategy(strategy: str, worker_enabled: Optional[bool] = None) -> Dict[str, Any]:
    cfg = _load_ai_mode()
    cfg["strategy"] = strategy
    if worker_enabled is not None:
        cfg["worker_enabled"] = bool(worker_enabled)
    if strategy == "research_list":
        _apply_research_list_threshold(float(cfg.get("entry_threshold") or 65))
    _save_ai_mode(cfg)
    return cfg


def _scan_picks_line() -> str:
    try:
        from backend.api.research_jobs import latest_scan

        scan = latest_scan()
    except Exception:
        return "掃描名單：—"
    picks = ", ".join((scan.get("top5_tickers") or [])[:5]) or "—"
    stale = bool(scan.get("stale") or not scan.get("available"))
    as_of = str(scan.get("ts") or "—").replace("T", " ")[:19]
    flag = "過期（不開新倉）" if stale else "新鮮"
    return f"掃描 {flag} · as-of {as_of} · 前五 {picks}"


def _cmd_start_auto() -> str:
    from backend.api import worker_ctl

    cfg = _load_ai_mode()
    strategy = str(cfg.get("strategy") or "breakout")
    try:
        result = worker_ctl.restart(strategy=strategy, interval=60)
    except Exception as exc:
        return f"啟動失敗：{exc}"
    _set_saved_strategy(strategy, worker_enabled=True)
    if result.get("already_running"):
        live = result.get("strategy") or strategy
        extra = ""
        if str(live) != "research_list":
            extra = " 若要改成跟 Scan 買，請點「跟掃描」。"
        return f"紙上自動交易本來就在跑（策略 {live}）。{extra}".strip()
    return f"已啟動紙上自動交易（{strategy}）。頂欄應顯示 AUTO ON。\n{_scan_picks_line()}"


def _cmd_follow_scan() -> str:
    """Operator-chosen Scan top-N buys — never a silent rewrite, never defensive_gld."""
    from backend.api import worker_ctl

    _set_saved_strategy("research_list", worker_enabled=True)
    try:
        current = worker_ctl.status()
        if current.get("running"):
            result = worker_ctl.restart(strategy="research_list", interval=60)
            switched = "已把紙上策略改成 research_list 並重啟。"
        else:
            result = worker_ctl.start(strategy="research_list", interval=60)
            switched = "已啟動紙上自動交易（research_list）。"
    except Exception as exc:
        return f"跟掃描失敗：{exc}\n已把 AI Mode 存成 research_list，可稍後再點「啟動」。"
    live = result.get("strategy") or "research_list"
    lines = [
        switched,
        f"策略：{live}",
        "只買最新 Scan 前 N 名；仍要過 Stage-2、凱利與風控。Telegram 不會直接下單，也不會改成 defensive_gld。",
        _scan_picks_line(),
    ]
    return "\n".join(lines)


def _cmd_stop_auto() -> str:
    from backend.api import worker_ctl

    try:
        worker_ctl.stop()
    except Exception as exc:
        return f"停止失敗：{exc}"
    _set_saved_strategy(str(_load_ai_mode().get("strategy") or "breakout"), worker_enabled=False)
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
