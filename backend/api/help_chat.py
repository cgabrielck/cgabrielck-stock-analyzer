"""Desk Ask Cgab help chat — teaching / guide only.

Independent of AI Mode, IntentStore, OrderManager, and the paper worker.
Conversations are not journaled and never mutate trading state.
Knowledge whitelist: docs/CGAB_USER_GUIDE.md + in-app Guide / tour copy.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Request
from pydantic import BaseModel, Field, field_validator

from backend.api.desk_auth import is_operator

ROOT = Path(__file__).resolve().parents[2]
USER_GUIDE_PATH = ROOT / "docs" / "CGAB_USER_GUIDE.md"

HONESTY_ZH = "教學說明 · 不是 verified／live · 不會下單或改規則 · 自動交易請去 AI Mode"
HONESTY_EN = (
    "Teaching only · not verified/live · will not place orders or change rules"
    " · paper auto is in AI Mode"
)

HARD_REFUSE_ZH = (
    "【拒答 · 不下單／不改規則】\n"
    "Help 聊天不能下單、不能改 live／L2／風控規則，也不能繞過 risk／mandate／kill。\n"
    "請到正確 UI：\n"
    "· 下單／紙上交易 → Navigation · Desk（工作台票）\n"
    "· 風控／規則 → Settings（非聊天）\n"
    "· 自動交易 → Apps · AI Mode（手動 Start／Stop）\n"
    "我只做教學說明，不會假裝已成交或已改設定。"
)
HARD_REFUSE_EN = (
    "[Refused · no orders / no rule changes]\n"
    "Help chat cannot place orders, change live/L2/risk rules, or bypass"
    " risk/mandate/kill.\n"
    "Use the correct UI:\n"
    "· Orders / paper ticket → Navigation · Desk\n"
    "· Risk / rules → Settings (not chat)\n"
    "· Paper auto → Apps · AI Mode (manual Start/Stop)\n"
    "This is teaching only. It will not pretend a fill happened or that"
    " settings changed."
)

GUEST_REFUSE_ZH = (
    "【Guest 拒答】目前是 Guest（LAN 研究）角色。\n"
    "我只能回答 Scan／Deep／Chart／研究面概念。\n"
    "持倉、P&L、交易日誌細節，以及啟動 AI Mode／自動交易，請改用 Owner"
    "（本機／operator）或自行到對應頁面操作。\n"
    "本聊天不會執行任何動作。"
)
GUEST_REFUSE_EN = (
    "[Guest refused] You are a research guest (LAN).\n"
    "I can only explain Scan / Deep / Chart / research surfaces.\n"
    "Holdings, P&L, journal detail, and starting AI Mode / paper auto stay"
    " on the owner PC (localhost / operator token).\n"
    "This chat never executes those actions."
)

# Imperative money / control — never execute; always the fixed refuse copy.
_ORDER_RE = re.compile(
    r"下單|下单|買入|买入|購入|购入|買進|买进|購進|购进|"
    r"賣出|卖出|掛單|挂单|"
    r"幫我買|帮我买|幫我賣|帮我卖|幫我購入|帮我购入|"
    r"請幫我[買賣購购]|请帮我[买卖购]|"
    r"幫我成交|帮我成交|幫我執行|帮我执行|"
    r"執行交易|执行交易|"
    r"市價買|市价买|限價|限价|"
    r"\b(buy|sell|short)\b|"
    r"place\s+(an\s+|a\s+)?(market\s+|limit\s+)?order|"
    r"submit\s+(an\s+|a\s+)?order|"
    r"market\s+order|"
    r"execute\s+(a\s+|an\s+|the\s+)?(trade|order)",
    re.I,
)
_RULE_RE = re.compile(
    r"改規則|改规则|改\s*l2|改\s*live|改風控|改风控|"
    r"修改.{0,12}(l2|live|規則|规则|風控|风控)|"
    r"change[- ]rules?|"
    r"(?:change|modify|edit|update|adjust|tweak)\s+"
    r"(the\s+)?(.{0,24}\b)?(live|l2|risk)\b.{0,24}\brules?|"
    r"edit\s+(the\s+)?(live|l2)\s+rules?|"
    r"update\s+(the\s+)?(live|l2)\s+rules?",
    re.I,
)
_AUTO_CMD_RE = re.compile(
    r"啟動\s*(?:紙上)?自動|启动\s*(?:纸上)?自动|"
    r"開始\s*(?:紙上)?自動|开始\s*(?:纸上)?自动|"
    r"開啟\s*(?:紙上)?自動|开启\s*(?:纸上)?自动|"
    r"打開\s*(?:紙上)?自動|打开\s*(?:纸上)?自动|"
    r"停止\s*(?:紙上|纸上)?自動|停止\s*(?:纸上)?自动|"
    r"啟動\s*AI|启动\s*AI|開啟\s*AI\s*模式|开启\s*AI\s*模式|"
    r"幫我啟動|帮我启动|打開自動交易|打开自动交易|"
    r"(?:please\s+)?(?:start|stop|enable|disable)\s+"
    r"(?:the\s+)?(?:paper\s+)?(?:auto|worker|bot)\b|"
    r"start\s+paper(\s+auto)?|"
    r"stop\s+paper(\s+auto)?|"
    r"turn\s+(?:on|off)\s+(?:the\s+)?(?:paper\s+)?auto|"
    r"open\s+paper\s+auto|"
    r"kill\s+the\s+worker",
    re.I,
)
_BYPASS_RE = re.compile(
    r"繞過|绕过|"
    r"關掉\s*(?:kill|風控|风控)|关掉\s*(?:kill|風控|风控)|"
    r"關閉風控|关闭风控|關閉\s*kill|关闭\s*kill|"
    r"bypass\s+(the\s+)?(risk|mandate|kill)|"
    r"disable\s+(the\s+)?(risk|mandate|kill)|"
    r"ignore\s+(the\s+)?(risk|mandate|kill)|"
    r"turn\s+off\s+(the\s+)?(risk|mandate|kill)|"
    r"switch\s+off\s+(the\s+)?(risk|mandate|kill)",
    re.I,
)
_GUEST_SENSITIVE_RE = re.compile(
    r"持倉|仓位|持仓|倉位|"
    r"持股|獲利|获利|權益|权益|"
    r"損益|损益|日誌|日志|"
    r"\b(pnl|p&l|holdings?|positions?|journal|balance|equity|profit)\b|"
    r"啟動\s*(?:紙上)?自動|启动\s*(?:纸上)?自动|"
    r"開始\s*(?:紙上)?自動|开始\s*(?:纸上)?自动|"
    r"開啟\s*(?:紙上)?自動|开启\s*(?:纸上)?自动|"
    r"啟動\s*AI|启动\s*AI|開啟\s*AI|开启\s*AI|"
    r"start\s+(?:paper\s+)?auto|start\s+ai\s*mode|"
    r"ai\s*mode\s+start|open\s+paper\s+auto",
    re.I,
)
# Completed-execution claims. Negated teaching copy is stripped first.
_DISHONEST_RE = re.compile(
    r"verified\s+fill|(?<!/)live\s+fills?\b|already\s+filled|"
    r"filled\s+at|"
    r"execution\s+is\s+verified|"
    r"(?:i\s+)?submitted\s+your\s+order|"
    r"order\s+(?:was\s+)?(?:placed|submitted)|"
    r"i\s+placed\s+(?:the\s+)?order|"
    r"auto\s+is\s+now\s+on|"
    r"paper\s+auto\s+is\s+(?:now\s+)?running|"
    r"worker\s+is\s+now\s+running|"
    r"已成交|成交了|已下單|已下单|已送出|"
    r"已啟動自動|已启动自动|"
    r"規則已改好|规则已改好|"
    r"我已經幫你買|我已经帮你买|"
    r"這是\s*verified|这是\s*verified|"
    r"這是\s*live\s*成交|这是\s*live\s*成交",
    re.I,
)
_NEGATED_CLAIM_RE = re.compile(
    r"(?:不會|不会|不要|不能|沒有|没有|不是|未|絕不|绝不|"
    r"never|not|won'?t|will not|cannot|can'?t|"
    r"do not|don'?t|did not|didn'?t|without)"
    r".{0,36}?"
    r"(?:已成交|成交了|已下單|已下单|已送出|規則已改|规则已改|"
    r"verified|live fill|filled|submitted|execution|fill happened)",
    re.I | re.S,
)

GUIDE_STEPS: List[Dict[str, Any]] = [
    {
        "id": "welcome",
        "en": {
            "title": "Welcome to Cgab",
            "body": "This is a US-equity research desk with optional AI paper trading. Nothing here spends real money while the PAPER badge is on.",
        },
        "zh": {
            "title": "歡迎使用 Cgab",
            "body": "這是美股研究工作台，可選用 AI 模擬自動交易。只要右上角顯示 PAPER，就不會動用真實資金。",
        },
    },
    {
        "id": "nav",
        "en": {
            "title": "Three sidebar groups",
            "body": "Navigation is the daily workspace (Desk, Orders, Research, Market, Chart). Apps are tools (AI Mode, Strategies, Alerts, Calendar). More holds Journal, Options, Settings, and this Guide.",
        },
        "zh": {
            "title": "側欄三個分組",
            "body": "導覽是日常工作區（工作台、訂單、研究、行情、圖表）。應用是工具（AI 模式、策略、警示、行事曆）。更多包含日誌、選擇權、設定與本教學。",
        },
    },
    {
        "id": "desk",
        "en": {
            "title": "Desk is the paper book",
            "body": "Equity, cash, and holdings come from your Alpaca paper account. An empty table means no fills yet — not that the desk is broken. Two books, two directories. Desk broker journal/ledger is backend/data/. Streamlit Kelly/lab portfolio is data/. They are not the same book.",
        },
        "zh": {
            "title": "工作台是模擬持倉",
            "body": "權益、現金與持倉來自 Alpaca 模擬帳戶。表格空白代表還沒成交，不是畫面壞掉。兩本帳、兩個目錄。Desk 券商日誌／帳本在 backend/data/。Streamlit Kelly／實驗室組合在 data/。兩者不是同一本帳。",
        },
    },
    {
        "id": "books",
        "en": {
            "title": "Two books, two directories",
            "body": "Two books, two directories. Desk broker journal/ledger is backend/data/. Streamlit Kelly/lab portfolio is data/. They are not the same book.",
        },
        "zh": {
            "title": "兩本帳、兩個目錄",
            "body": "兩本帳、兩個目錄。Desk 券商日誌／帳本在 backend/data/。Streamlit Kelly／實驗室組合在 data/。兩者不是同一本帳。",
        },
    },
    {
        "id": "auto",
        "en": {
            "title": "How paper auto trading works",
            "body": "Open AI Mode → pick a strategy → Start paper auto. The top bar shows AUTO ON only when the worker process is running (live pid, heartbeat not stale) — not when the account is loaded, not from an unverified heartbeat, and not from a stale pid. The worker scans during US regular hours unless IGNORE_MARKET_HOURS=true.",
        },
        "zh": {
            "title": "如何開啟紙上自動交易",
            "body": "打開 AI 模式 → 選擇策略 → 按「啟動紙上自動交易」。只有工作行程進程在跑（有活 PID 且心跳未過期）時，頂欄才會顯示 AUTO ON。不是帳戶已載入，不是未驗證心跳，也不是過期 PID。預設只在美股正規交易時段掃描，除非 .env 設 IGNORE_MARKET_HOURS=true。",
        },
    },
    {
        "id": "empty",
        "en": {
            "title": "Why you may see no trades",
            "body": "The bot does not buy every minute. A signal must beat the entry threshold, Kelly size, RiskEngine, and mandate. Yahoo data gaps can skip a cycle. Manual Buy on Desk still works anytime in paper.",
        },
        "zh": {
            "title": "為什麼看不到成交",
            "body": "機器人不會每分鐘都買。訊號必須通過進場門檻、凱利倉位、風控與授權。Yahoo 行情中斷時該輪會跳過。工作台上的手動買入隨時可在模擬盤下單。",
        },
    },
    {
        "id": "journal",
        "en": {
            "title": "Trade journal is on the Desk",
            "body": "More → Journal shows recent orders, risk rejects, and notes with audit/provenance links. Empty means nothing was recorded — not a live fill. Streamlit’s journal is lab/legacy only.",
        },
        "zh": {
            "title": "交易日誌在 Desk",
            "body": "更多 → 交易日誌顯示近期訂單、風控拒絕與筆記，並可跟著 audit／provenance 連結。空白代表尚未入帳，不是即時成交。Streamlit 日誌只是實驗室／舊介面。",
        },
    },
    {
        "id": "help",
        "en": {
            "title": "Ask Cgab is teaching only",
            "body": "The Ask Cgab button and the top-bar ? open a help chat that explains the app. It is not AI Mode, not verified, not live, and it will not place orders, change rules, or Start/Stop paper auto. Replay the first-visit tour from Guide.",
        },
        "zh": {
            "title": "Ask Cgab 只做教學說明",
            "body": "右下角 Ask Cgab 與頂欄 ? 打開同一個教學聊天。不是 AI Mode、不是 verified、不是 live，也不會下單、改規則或 Start／Stop 紙上自動。第一次導覽可從教學頁重看。",
        },
    },
    {
        "id": "lang",
        "en": {
            "title": "English and 繁體中文",
            "body": "Use the EN / 繁 switch in the top bar. Replay the first-visit tour from Guide. The ? button and Ask Cgab open the teaching chat (not AI Mode).",
        },
        "zh": {
            "title": "英文與繁體中文",
            "body": "用頂欄的 EN / 繁 切換語言。第一次導覽可從「教學」重看。問號按鈕與 Ask Cgab 打開教學聊天（不是 AI Mode）。",
        },
    },
]

IN_APP_PAGE_COPY = """
In-app page copy (shipped Desk UI, not Draft design docs):
- Scan is a Navigation page: rank the universe (fundamentals, technicals, sentiment, risk, regime). Top names become today's picks. Scan scores are not verified, not live, and not orders.
- Deep is an in-page action (Scan / Research), not a third sidebar shell item. Deep reports are research, not fills.
- Chart is a TradingView-style daily snapshot. Not TradingView, not live, not an order.
- Auto is the top-bar worker chip: AUTO ON | OFF | STALE. ON means the worker process is running (live pid, heartbeat not stale). It is not account-loaded and not an unverified heartbeat.
- Desk is the Alpaca paper book. Empty holdings = no fill yet, not a broken screen, not a live broker book.
- AI Mode (Apps) is the only place to pick a strategy and Start / Stop paper auto. Saving sliders does not start the bot.
- Ask Cgab / Help chat ≠ AI Mode. Help only explains and points; it never executes.
- Do not teach unpublished Draft ≠ Accepted design docs as shipped features.
""".strip()


class HelpChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=400)
    lang: Optional[str] = None

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("message is required")
        return text


def guide_payload() -> Dict[str, Any]:
    return {
        "languages": ["en", "zh-TW"],
        "default": "en",
        "steps": GUIDE_STEPS,
    }


def normalize_lang(lang: Optional[str]) -> str:
    token = (lang or "").strip().lower().replace("_", "-")
    if token.startswith("zh"):
        return "zh"
    return "en"


def honesty_bar(lang: str) -> str:
    return HONESTY_ZH if lang == "zh" else HONESTY_EN


def hard_refuse_copy(lang: str) -> str:
    return HARD_REFUSE_ZH if lang == "zh" else HARD_REFUSE_EN


def guest_refuse_copy(lang: str) -> str:
    return GUEST_REFUSE_ZH if lang == "zh" else GUEST_REFUSE_EN


def is_hard_refuse(text: str) -> bool:
    return bool(
        _ORDER_RE.search(text)
        or _RULE_RE.search(text)
        or _AUTO_CMD_RE.search(text)
        or _BYPASS_RE.search(text)
    )


def is_guest_sensitive(text: str) -> bool:
    return bool(_GUEST_SENSITIVE_RE.search(text))


def reply_is_dishonest(text: str) -> bool:
    """True when the reply claims an execution. Negations are not hits.

    「不會假裝已成交」 and "will not pretend a fill happened" stay honest.
    """
    cleaned = _NEGATED_CLAIM_RE.sub(" ", text or "")
    return bool(_DISHONEST_RE.search(cleaned))


def load_knowledge() -> str:
    guide = ""
    try:
        guide = USER_GUIDE_PATH.read_text(encoding="utf-8")
    except OSError:
        guide = "(user guide unavailable)"
    steps = []
    for step in GUIDE_STEPS:
        en = step.get("en") or {}
        zh = step.get("zh") or {}
        steps.append(
            f"- {step.get('id')}: {en.get('title')} — {en.get('body')}\n"
            f"  {zh.get('title')} — {zh.get('body')}"
        )
    return (
        "USER GUIDE (shipped):\n"
        f"{guide}\n\n"
        "IN-APP GUIDE / TOUR (shipped):\n"
        + "\n".join(steps)
        + "\n\n"
        + IN_APP_PAGE_COPY
    )


def _llm_reply(system: str, user: str) -> Optional[str]:
    """Call the shared OpenAI-compatible LLM_* client. None if unconfigured."""
    try:
        from backend.pathsetup import ensure_backend_on_path

        ensure_backend_on_path()
        from agents.llm_agent import complete_chat

        return complete_chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            task="help",
            temperature=0.2,
            max_tokens=700,
        )
    except Exception:
        return None


def _fallback_teach(message: str, lang: str, role: str) -> str:
    q = message
    zh = lang == "zh"
    guest = role == "guest"
    # Guests never receive Start/Stop auto teaching, even if a classifier miss
    # lets a holdings / AI Mode prompt through.
    if guest and (is_guest_sensitive(q) or is_hard_refuse(q)):
        return guest_refuse_copy("zh" if zh else "en")
    if guest and re.search(r"ai\s*mode|aimode|自動|自动|paper auto", q, re.I):
        return guest_refuse_copy("zh" if zh else "en")
    if re.search(r"scan|掃描|扫描|deep|深度", q, re.I):
        if zh:
            return (
                "Scan＝側欄 Navigation · 掃描：對關注清單／宇宙做掃描總覽。\n"
                "Deep＝頁內動作：在 Scan 或 Research 對單一代碼做更深報告，不是第三個平級殼項。\n"
                "Auto＝頂欄／AI Mode 的紙上 worker 狀態，與 Help 聊天無關。\n"
                "掃描分數／報告不是 verified、不是 live、不是下單指令。"
            )
        return (
            "Scan is a Navigation page: universe ranking (fundamentals, technicals,"
            " sentiment, risk, regime).\n"
            "Deep is an in-page action on Scan or Research — not a third sidebar"
            " shell item.\n"
            "Auto is the top-bar paper-worker chip, unrelated to this help chat.\n"
            "Scan scores / Deep reports are not verified, not live, and not orders."
        )
    if re.search(r"持倉|仓位|沒持倉|没持仓|empty|為何沒|为何没|no positions", q, re.I):
        if zh:
            return (
                "空帳／尚無持倉＝還沒有過關的紙上成交，或尚未接上完整資料——"
                "通常不是壞畫面，也不是回測數字，更不是 live 券商帳本。\n"
                "請到 Navigation · 工作台查看 Alpaca 模擬持倉。本聊天不載入你的 P&L。\n"
                "若要知道紙上自動是否在跑，看頂欄 AUTO ON｜OFF｜STALE（唯讀）；"
                "開關請去 AI Mode，不要叫聊天幫你啟動。"
            )
        return (
            "Empty holdings means no paper fill has passed the gates yet — not a"
            " broken screen, not backtest figures, and not a live broker book.\n"
            "Look at Navigation · Desk for the Alpaca paper book. This chat does"
            " not load your P&L.\n"
            "AUTO ON|OFF|STALE on the top bar is read-only; Start/Stop stays in"
            " AI Mode."
        )
    if re.search(r"ai\s*mode|aimode|自動|自动|auto\s*on|paper auto", q, re.I):
        if zh:
            return (
                "AI Mode ≠ Help 聊天。\n"
                "AI Mode（Apps → AI Mode）＝選策略＋手動 Start／Stop paper auto。\n"
                "Ask Cgab 只解釋概念與指路，不會代你按 Start／Stop，也不會改 AUTO 狀態。\n"
                "頂欄 PAPER · AUTO ON｜OFF｜STALE 是唯讀示意。儲存滑桿不會啟動機器人。"
            )
        return (
            "AI Mode ≠ this help chat.\n"
            "AI Mode (Apps → AI Mode) is where you pick a strategy and manually"
            " Start / Stop paper auto.\n"
            "Ask Cgab only explains and points. It will not press Start/Stop or"
            " change the AUTO chip.\n"
            "PAPER and AUTO ON|OFF|STALE are read-only. Saving sliders does not"
            " start the bot."
        )
    if re.search(r"desk|工作台|導覽|导览|guide|教學|教学|help|ask cgab", q, re.I):
        if zh:
            return (
                "Cgab Desk 是產品殼（web/static + FastAPI）。Streamlit 是實驗室／舊介面。\n"
                "Ask Cgab＝Help／導覽聊天：解釋頁面、Scan／Deep／Auto、空狀態與紙上 vs 研究。\n"
                "不是下單代理，也不是 AI Mode。Draft ≠ Accepted 的設計稿不當已上線功能教。"
            )
        return (
            "Cgab Desk is the product UI (web/static + FastAPI). Streamlit is"
            " lab / legacy.\n"
            "Ask Cgab is teaching/guide chat: pages, Scan/Deep/Auto, empty"
            " states, paper vs research.\n"
            "It is not an order agent and not AI Mode. Draft ≠ Accepted design"
            " docs are not taught as shipped features."
        )
    if zh:
        return (
            "我是 Ask Cgab，只做 Desk 教學說明（不是 AI Mode）。\n"
            "可以問：Scan／Deep／Chart、空持倉、PAPER 徽章、如何到 AI Mode 開紙上自動。\n"
            "不能：下單、改 live／L2／風控、Start／Stop auto、宣稱 verified／live 成交。\n"
            f"角色：{role}。知識＝User Guide 與畫面導覽白名單。"
        )
    return (
        "I am Ask Cgab — Desk teaching only (not AI Mode).\n"
        "I can explain Scan / Deep / Chart, empty holdings, the PAPER badge,"
        " and how to reach AI Mode for paper auto.\n"
        "I cannot place orders, change live/L2/risk rules, Start/Stop auto, or"
        " claim a verified or live execution.\n"
        f"Role: {role}. Knowledge = User Guide + in-app Guide/tour whitelist."
    )


def _system_prompt(role: str, lang: str, knowledge: str) -> str:
    lang_name = "Traditional Chinese" if lang == "zh" else "English"
    return (
        "You are Ask Cgab, the in-app teaching assistant for the Cgab Desk UI.\n"
        "Teaching / guide only. You are NOT an order agent and NOT AI Mode.\n"
        f"Answer in {lang_name}. Be concise.\n"
        f"Caller role: {role}. "
        "Owner (localhost / operator token) may receive full teaching answers. "
        "Guest (LAN research) may only be taught Scan / Deep / Chart / research"
        " surfaces; never holdings, P&L, journal detail, or how to start auto"
        " as if they could do it here.\n"
        "Hard rules:\n"
        "- Never place orders, change L2/live/risk/mandate/kill, or Start/Stop"
        " the paper worker.\n"
        "- Never claim verified or live fills. PAPER is paper. Empty book ≠ live.\n"
        "- Never teach unpublished Draft ≠ Accepted design docs as shipped"
        " features.\n"
        "- If the user asks you to execute something, refuse and point to Desk"
        " (ticket), Settings (rules), or AI Mode (Start/Stop).\n"
        "- Do not invent account numbers, P&L, or fills. Point at the UI.\n"
        "- Knowledge whitelist only (user guide + in-app guide/tour/page copy):\n"
        f"{knowledge}\n"
    )


def _envelope(
    *,
    role: str,
    lang: str,
    reply: str,
    refused: bool,
    refuse_kind: Optional[str],
    llm_used: bool,
) -> Dict[str, Any]:
    return {
        "role": role,
        "lang": "zh-TW" if lang == "zh" else "en",
        "reply": reply,
        "refused": refused,
        "refuse_kind": refuse_kind,
        "verified": False,
        "live": False,
        "places_order": False,
        "changes_rules": False,
        "starts_worker": False,
        "writes_journal": False,
        "writes_intent": False,
        "llm_used": llm_used,
        "honesty": honesty_bar(lang),
    }


def sanitize_reply(text: str, lang: str) -> str:
    raw = (text or "").strip()
    if reply_is_dishonest(raw):
        extra = (
            "Ask Cgab 不會標記 verified 或 live 成交。"
            if lang == "zh"
            else "Ask Cgab will not stamp verified or live executions."
        )
        return hard_refuse_copy(lang) + "\n" + extra
    return raw


def answer_help_chat(
    request: Request,
    message: str,
    lang: Optional[str] = None,
) -> Dict[str, Any]:
    """Teaching reply. Never calls OrderManager / IntentStore / worker start."""
    role = "operator" if is_operator(request) else "guest"
    ui_lang = normalize_lang(lang)
    text = (message or "").strip()

    if role == "guest" and is_guest_sensitive(text):
        return _envelope(
            role=role,
            lang=ui_lang,
            reply=guest_refuse_copy(ui_lang),
            refused=True,
            refuse_kind="guest",
            llm_used=False,
        )
    if is_hard_refuse(text):
        return _envelope(
            role=role,
            lang=ui_lang,
            reply=hard_refuse_copy(ui_lang),
            refused=True,
            refuse_kind="hard",
            llm_used=False,
        )

    knowledge = load_knowledge()
    llm_text = _llm_reply(_system_prompt(role, ui_lang, knowledge), text)
    source = (llm_text or _fallback_teach(text, ui_lang, role) or "").strip()
    dishonest = reply_is_dishonest(source)
    reply = sanitize_reply(source, ui_lang) if dishonest else source
    return _envelope(
        role=role,
        lang=ui_lang,
        reply=reply,
        refused=dishonest,
        refuse_kind="hard" if dishonest else None,
        llm_used=bool(llm_text) and not dishonest,
    )
