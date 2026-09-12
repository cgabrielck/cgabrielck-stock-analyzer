"""Cgab API — paper-first trading desk for the React UI."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

load_dotenv()

from backend.pathsetup import ensure_backend_on_path

ensure_backend_on_path()

from backend.api import research_jobs, worker_ctl
from backend.trading import telegram_inbox
from backend.trading.alpaca_broker import AlpacaBroker
from backend.trading.models import Order, OrderSide, OrderType
from backend.trading.safety import kill_switch
from backend.trading.safety.mandate import load_mandate
from backend.trading.storage import create_order_store
from backend.utils.constants import DATA_DIR, STOCK_UNIVERSE

APP_NAME = "Cgab"
AI_MODE_PATH = Path(DATA_DIR) / "ai_mode.json"
WATCHLIST_PATH = Path(DATA_DIR) / "watchlist.json"
JOURNAL_PATH = Path(DATA_DIR) / "trade_journal.json"

DEFAULT_AI_MODE = {
    "strategy": "stable",
    "llm_influence": 20,
    "entry_threshold": 70,
    "max_positions": 10,
    "risk_tolerance": "medium",
    "worker_enabled": False,
    "ignore_market_hours": os.getenv("IGNORE_MARKET_HOURS", "false").lower() in ("1", "true", "yes"),
}

@asynccontextmanager
async def lifespan(_app: FastAPI):
    stop_inbox = telegram_inbox.start_background()
    from backend.api import scan_scheduler

    stop_scan = scan_scheduler.start_background()
    try:
        yield
    finally:
        stop_scan()
        stop_inbox()


app = FastAPI(title=APP_NAME, version="1.0.0", lifespan=lifespan)
STATIC_DIR = Path(__file__).resolve().parents[2] / "web" / "static"
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_broker = AlpacaBroker()
_store = create_order_store()


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _require_paper() -> None:
    if os.getenv("APCA_PAPER", "true").lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=403, detail="Live trading is blocked. Keep APCA_PAPER=true.")


class OrderIn(BaseModel):
    symbol: str
    side: str = "buy"
    quantity: float = Field(gt=0)
    order_type: str = "market"
    limit_price: Optional[float] = None


class AiModeIn(BaseModel):
    strategy: str = "stable"
    llm_influence: float = 20
    entry_threshold: float = 70
    max_positions: int = 10
    risk_tolerance: str = "medium"
    worker_enabled: bool = False
    ignore_market_hours: bool = False


class WatchIn(BaseModel):
    symbol: str


class JournalIn(BaseModel):
    symbol: str
    note: str
    side: Optional[str] = None


class WorkerStartIn(BaseModel):
    strategy: str = "stable"
    interval: int = 60


class ScanIn(BaseModel):
    lang: str = "en"
    llm_weight: float = 0.2
    use_llm: bool = True
    force_refresh: bool = False


class DeepIn(BaseModel):
    tickers: List[str] = Field(default_factory=list)
    lang: str = "en"
    force_refresh: bool = False


@app.get("/api/meta")
def meta() -> Dict[str, Any]:
    scan = research_jobs.latest_scan()
    from backend.api import scan_scheduler

    sched = scan_scheduler.status()
    return {
        "name": APP_NAME,
        "paper": os.getenv("APCA_PAPER", "true").lower() in ("1", "true", "yes"),
        "ignore_market_hours": os.getenv("IGNORE_MARKET_HOURS", "").lower() in ("1", "true", "yes"),
        "scan_available": bool(scan.get("available")),
        "scan_stale": bool(scan.get("stale", True)),
        "scan_ts": scan.get("ts"),
        "scan_as_of": scan.get("ts"),
        "scan_auto": sched.get("scan_auto"),
        "next_scan_at": sched.get("next_scan_at"),
        "universe_size": len(STOCK_UNIVERSE),
        "llm": _llm_public(),
    }


def _llm_public() -> Dict[str, Any]:
    try:
        ensure_backend_on_path()
        from agents.llm_agent import get_public_config

        return get_public_config()
    except Exception as exc:
        return {"configured": False, "error": str(exc)}


@app.get("/api/account")
def account() -> Dict[str, Any]:
    _require_paper()
    try:
        summary = _broker.get_account_summary()
        return summary.model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/positions")
def positions() -> Dict[str, Any]:
    data = account()
    rows = data.get("positions") or []
    equity = float(data.get("equity") or data.get("portfolio_value") or 0)
    for row in rows:
        mv = float(row.get("market_value") or 0)
        row["weight_pct"] = round(100.0 * mv / equity, 2) if equity else 0.0
    return {"positions": rows, "count": len(rows)}


@app.get("/api/orders")
def orders(status: str = "all", limit: int = 50) -> Dict[str, Any]:
    _require_paper()
    out: List[Dict[str, Any]] = []
    try:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        qstatus = QueryOrderStatus.OPEN if status == "open" else QueryOrderStatus.ALL
        raw = _broker.api.get_orders(filter=GetOrdersRequest(status=qstatus, limit=limit))
        for o in raw:
            out.append(
                {
                    "id": str(o.id),
                    "symbol": o.symbol,
                    "side": getattr(o.side, "value", o.side),
                    "type": getattr(o.type, "value", o.type),
                    "qty": float(o.qty or 0),
                    "status": getattr(o.status, "value", o.status),
                    "limit_price": float(o.limit_price) if o.limit_price else None,
                    "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
                    "submitted_at": str(o.submitted_at) if o.submitted_at else None,
                }
            )
    except Exception:
        recent = _store.get_recent_orders(limit=limit)
        out = [o.model_dump(mode="json") for o in recent]
    return {"orders": out}


@app.post("/api/orders")
def submit_order(body: OrderIn) -> Dict[str, Any]:
    _require_paper()
    if kill_switch.is_halted() and body.side.lower() == "buy":
        raise HTTPException(status_code=409, detail="Kill switch is on — new buys blocked.")
    try:
        side = OrderSide(body.side.lower())
        otype = OrderType(body.order_type.lower())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    order = Order(
        id=str(uuid.uuid4()),
        symbol=body.symbol.upper().strip(),
        side=side,
        order_type=otype,
        quantity=float(body.quantity),
        limit_price=body.limit_price,
        idempotency_key=f"cgab-{uuid.uuid4()}",
    )
    filled = _broker.submit_order(order)
    try:
        _store.save_order(filled)
    except Exception:
        pass
    if filled.status.value == "rejected":
        raise HTTPException(status_code=400, detail=filled.error_message or "Order rejected")
    return filled.model_dump(mode="json")


@app.post("/api/orders/{order_id}/cancel")
def cancel_order(order_id: str) -> Dict[str, Any]:
    _require_paper()
    try:
        updated = _broker.cancel_order(order_id)
        return updated.model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/ops")
def ops() -> Dict[str, Any]:
    mandate = load_mandate()
    return {
        "kill_switch": kill_switch.is_halted(),
        "kill_reason": kill_switch.get_reason(),
        "mandate": mandate.model_dump() if hasattr(mandate, "model_dump") else str(mandate),
        "paper": True,
    }


@app.post("/api/ops/kill")
def ops_kill(reason: str = "Manual halt from Cgab") -> Dict[str, Any]:
    kill_switch.engage(reason)
    return ops()


@app.post("/api/ops/resume")
def ops_resume() -> Dict[str, Any]:
    kill_switch.disengage()
    return ops()


@app.get("/api/ai-mode")
def get_ai_mode() -> Dict[str, Any]:
    data = {**DEFAULT_AI_MODE, **_read_json(AI_MODE_PATH, {})}
    data["kill_switch"] = kill_switch.is_halted()
    return data


@app.put("/api/ai-mode")
def put_ai_mode(body: AiModeIn) -> Dict[str, Any]:
    if body.strategy not in ("stable", "aggressive", "hybrid", "research_list"):
        raise HTTPException(status_code=400, detail="Unknown strategy")
    payload = body.model_dump()
    payload["llm_influence"] = max(0, min(40, float(body.llm_influence)))
    _write_json(AI_MODE_PATH, payload)
    return get_ai_mode()


def _position_count() -> int:
    try:
        return len((_broker.get_account_summary().positions) or [])
    except Exception:
        return 0


@app.get("/api/worker/status")
def worker_status() -> Dict[str, Any]:
    _require_paper()
    status = worker_ctl.status(position_count=_position_count(), halted=kill_switch.is_halted())
    scan = research_jobs.latest_scan()
    as_of = scan.get("ts") or "—"
    next_scan = status.get("next_scan_at") or "—"
    auto = "on" if status.get("scan_auto") else "off"
    status["research"] = {
        "scan_available": bool(scan.get("available")),
        "scan_stale": bool(scan.get("stale", True)),
        "scan_ts": scan.get("ts"),
        "scan_as_of": scan.get("ts"),
        "scan_auto": status.get("scan_auto"),
        "next_scan_at": status.get("next_scan_at"),
        "top5_tickers": scan.get("top5_tickers") or [],
        "message_en": (
            f"Scan as-of {as_of} is past freshness — research_list blocks new buys; "
            f"Stable keeps last real scores. Auto-scan={auto}; next={next_scan}."
            if scan.get("stale", True)
            else f"Fresh scan as-of {as_of} feeding paper worker. Auto-scan={auto}; next={next_scan}."
        ),
        "message_zh": (
            f"掃描 as-of {as_of} 已過新鮮度視窗 — research_list 不開新倉；Stable 保留上次真實分數。"
            f"自動掃描={auto}；下次={next_scan}。"
            if scan.get("stale", True)
            else f"掃描 as-of {as_of} 新鮮，正提供紙上自動交易分數。自動掃描={auto}；下次={next_scan}。"
        ),
    }
    return status


@app.post("/api/worker/start")
def worker_start(body: Optional[WorkerStartIn] = None) -> Dict[str, Any]:
    _require_paper()
    body = body or WorkerStartIn()
    cfg = {**DEFAULT_AI_MODE, **_read_json(AI_MODE_PATH, {})}
    strategy = body.strategy or cfg.get("strategy") or "stable"
    try:
        result = worker_ctl.start(strategy=strategy, interval=body.interval)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    cfg["worker_enabled"] = True
    cfg["strategy"] = strategy
    _write_json(AI_MODE_PATH, cfg)
    result["ai_mode"] = get_ai_mode()
    return result


@app.post("/api/worker/stop")
def worker_stop() -> Dict[str, Any]:
    _require_paper()
    result = worker_ctl.stop()
    cfg = {**DEFAULT_AI_MODE, **_read_json(AI_MODE_PATH, {})}
    cfg["worker_enabled"] = False
    _write_json(AI_MODE_PATH, cfg)
    result["ai_mode"] = get_ai_mode()
    return result


@app.get("/api/guide")
def guide() -> Dict[str, Any]:
    return {
        "languages": ["en", "zh-TW"],
        "default": "en",
        "steps": [
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
                    "title": "Desk is the live paper book",
                    "body": "Equity, cash, and holdings come from your Alpaca paper account. An empty table means no fills yet — not that the desk is broken.",
                },
                "zh": {
                    "title": "工作台是模擬持倉",
                    "body": "權益、現金與持倉來自 Alpaca 模擬帳戶。表格空白代表還沒成交，不是畫面壞掉。",
                },
            },
            {
                "id": "auto",
                "en": {
                    "title": "How paper auto trading works",
                    "body": "Open AI Mode → pick a strategy → Start paper auto. The top bar shows AUTO ON only when the worker heartbeat is fresh. The worker scans during US regular hours unless IGNORE_MARKET_HOURS=true.",
                },
                "zh": {
                    "title": "如何開啟紙上自動交易",
                    "body": "打開 AI 模式 → 選擇策略 → 按「啟動紙上自動交易」。只有工作行程心跳正常時，頂欄才會顯示 AUTO ON。預設只在美股正規交易時段掃描，除非 .env 設 IGNORE_MARKET_HOURS=true。",
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
                "id": "lang",
                "en": {
                    "title": "English and 繁體中文",
                    "body": "Use the EN / 繁 switch in the top bar. Replay this tour anytime from Guide or the ? button.",
                },
                "zh": {
                    "title": "英文與繁體中文",
                    "body": "用頂欄的 EN / 繁 切換語言。之後可從「教學」或問號按鈕再看一次導覽。",
                },
            },
        ],
    }


@app.get("/api/watchlist")
def get_watchlist() -> Dict[str, Any]:
    symbols = _read_json(WATCHLIST_PATH, ["AAPL", "MSFT", "NVDA", "AMZN", "META"])
    return {"symbols": symbols}


@app.post("/api/watchlist")
def add_watch(body: WatchIn) -> Dict[str, Any]:
    symbols = _read_json(WATCHLIST_PATH, [])
    sym = body.symbol.upper().strip()
    if sym and sym not in symbols:
        symbols.append(sym)
        _write_json(WATCHLIST_PATH, symbols)
    return {"symbols": symbols}


@app.delete("/api/watchlist/{symbol}")
def del_watch(symbol: str) -> Dict[str, Any]:
    symbols = [s for s in _read_json(WATCHLIST_PATH, []) if s.upper() != symbol.upper()]
    _write_json(WATCHLIST_PATH, symbols)
    return {"symbols": symbols}


@app.get("/api/universe")
def universe() -> Dict[str, Any]:
    return {"universe": STOCK_UNIVERSE}


@app.get("/api/quotes")
def quotes(limit: int = 24) -> Dict[str, Any]:
    import yfinance as yf

    tickers = [s["ticker"] for s in STOCK_UNIVERSE[:limit]]
    rows = []
    try:
        data = yf.download(tickers, period="5d", interval="1d", group_by="ticker", progress=False, threads=True)
        for t in tickers:
            try:
                frame = data[t] if len(tickers) > 1 else data
                close = float(frame["Close"].dropna().iloc[-1])
                prev = float(frame["Close"].dropna().iloc[-2]) if len(frame["Close"].dropna()) > 1 else close
                chg = (close - prev) / prev * 100 if prev else 0
                meta = next((s for s in STOCK_UNIVERSE if s["ticker"] == t), {})
                rows.append({"symbol": t, "name": meta.get("name_en", t), "sector": meta.get("sector"), "last": close, "chg_pct": round(chg, 2)})
            except Exception:
                continue
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Quote fetch failed: {exc}") from exc
    return {"quotes": rows}


@app.get("/api/chart/{symbol}")
def chart(symbol: str, period: str = "6mo") -> Dict[str, Any]:
    import yfinance as yf

    hist = yf.Ticker(symbol.upper()).history(period=period, interval="1d")
    if hist is None or hist.empty:
        raise HTTPException(status_code=404, detail="No chart data")
    bars = [
        {
            "t": idx.strftime("%Y-%m-%d"),
            "o": round(float(row["Open"]), 4),
            "h": round(float(row["High"]), 4),
            "l": round(float(row["Low"]), 4),
            "c": round(float(row["Close"]), 4),
            "v": int(row["Volume"]),
        }
        for idx, row in hist.iterrows()
    ]
    return {"symbol": symbol.upper(), "bars": bars}


@app.post("/api/scan")
def scan_start(body: Optional[ScanIn] = None) -> Dict[str, Any]:
    body = body or ScanIn()
    llm_weight = max(0.0, min(0.4, float(body.llm_weight)))
    job = research_jobs.start_scan(
        lang=body.lang,
        llm_weight=llm_weight,
        use_llm=body.use_llm,
        force_refresh=body.force_refresh,
    )
    return {"accepted": True, "job": job}


@app.get("/api/scan/status")
def scan_status(job_id: Optional[str] = None) -> Dict[str, Any]:
    if job_id:
        return {"job": research_jobs.resolve_job("scan", job_id)}
    return {"job": research_jobs.latest_job("scan"), "latest": research_jobs.latest_scan()}


@app.get("/api/scan/latest")
def scan_latest() -> Dict[str, Any]:
    return research_jobs.latest_scan()


@app.post("/api/deep")
def deep_start(body: DeepIn) -> Dict[str, Any]:
    try:
        job = research_jobs.start_deep(
            tickers=body.tickers,
            lang=body.lang,
            force_refresh=body.force_refresh,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"accepted": True, "job": job}


@app.get("/api/deep/status")
def deep_status(job_id: Optional[str] = None) -> Dict[str, Any]:
    if job_id:
        return {"job": research_jobs.resolve_job("deep", job_id)}
    return {"job": research_jobs.latest_job("deep"), "latest": research_jobs.latest_deep()}


@app.get("/api/deep/latest")
def deep_latest() -> Dict[str, Any]:
    return research_jobs.latest_deep()


@app.get("/api/research/{symbol}")
def research(symbol: str) -> Dict[str, Any]:
    return research_jobs.research_bundle(symbol)


@app.get("/api/options/{symbol}")
def options(symbol: str) -> Dict[str, Any]:
    import yfinance as yf

    t = yf.Ticker(symbol.upper())
    try:
        expiries = list(t.options or [])
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not expiries:
        return {"symbol": symbol.upper(), "expiry": None, "calls": [], "puts": []}
    expiry = expiries[0]
    chain = t.option_chain(expiry)
    def _trim(df):
        if df is None or df.empty:
            return []
        cols = [c for c in ("contractSymbol", "strike", "lastPrice", "bid", "ask", "impliedVolatility", "volume", "openInterest") if c in df.columns]
        return df[cols].head(20).fillna(0).to_dict(orient="records")
    return {"symbol": symbol.upper(), "expiry": expiry, "expiries": expiries[:8], "calls": _trim(chain.calls), "puts": _trim(chain.puts)}


@app.get("/api/calendar")
def calendar() -> Dict[str, Any]:
    import yfinance as yf

    events: List[Dict[str, Any]] = [
        {"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "event": "US session", "impact": "Med", "symbol": "SPY", "kind": "macro"},
    ]
    for ticker in ["AAPL", "MSFT", "NVDA", "AMZN", "META", "JPM"]:
        try:
            cal = yf.Ticker(ticker).calendar
            if cal is None:
                continue
            earn = None
            if hasattr(cal, "get"):
                earn = cal.get("Earnings Date")
            if earn is not None:
                events.append({"date": str(earn)[:10], "event": f"{ticker} earnings", "impact": "High", "symbol": ticker, "kind": "earnings"})
        except Exception:
            continue
    return {"events": events[:30]}


@app.get("/api/journal")
def journal() -> Dict[str, Any]:
    raw = _read_json(JOURNAL_PATH, [])
    if isinstance(raw, dict):
        raw = raw.get("entries") or raw.get("trades") or []
    return {"entries": raw}


@app.post("/api/journal")
def add_journal(body: JournalIn) -> Dict[str, Any]:
    raw = _read_json(JOURNAL_PATH, [])
    if isinstance(raw, dict):
        entries = raw.get("entries") or []
        wrapper = raw
    else:
        entries = raw
        wrapper = None
    entry = {
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": body.symbol.upper(),
        "note": body.note,
        "side": body.side,
    }
    entries = list(entries) + [entry]
    if wrapper is not None:
        wrapper["entries"] = entries
        _write_json(JOURNAL_PATH, wrapper)
    else:
        _write_json(JOURNAL_PATH, entries)
    return {"entries": entries[-100:]}


@app.get("/api/performance")
def performance(refresh: bool = False) -> Dict[str, Any]:
    """Paper book vs SPY (preferred) plus optional stable backtest summary."""
    from backend.api import paper_performance

    paper = paper_performance.build_paper_report(persist=True) if refresh else None
    if paper is None:
        cached = paper_performance.load_cached_paper_report()
        if cached:
            metrics = cached.get("metrics") or {}
            trading_days = (cached.get("period") or {}).get("trading_days") or 0
            num_trades = metrics.get("num_trades") or 0
            empty_book = int(trading_days) == 0 and int(num_trades) == 0
            paper = {
                "available": True,
                "empty_book": empty_book,
                "source": "performance_paper.json",
                "generated_at": cached.get("generated_at"),
                "period": cached.get("period") or {},
                "pnl": cached.get("pnl") or {},
                "risk": {
                    "sharpe_ratio": metrics.get("sharpe_ratio"),
                    "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                },
                "trading": {
                    "num_trades": metrics.get("num_trades"),
                    "turnover_pct": (cached.get("costs") or {}).get("turnover_pct"),
                },
                "benchmark": {
                    "spy_return_pct": (cached.get("benchmark") or {}).get("spy_return_pct"),
                    "alpha_gross_pct": (cached.get("costs") or {}).get("alpha_gross_pct"),
                    "alpha_net_of_costs_pct": (cached.get("costs") or {}).get("alpha_net_of_costs_pct"),
                    "assumed_cost_bps": (cached.get("costs") or {}).get("assumed_round_trip_bps"),
                    "beats_spy_net": None if empty_book else cached.get("beats_spy_net"),
                },
                "seal_preview": {
                    "passed": cached.get("seal_passed"),
                    "reasons": cached.get("seal_reasons") or [],
                },
                "disclaimer": cached.get("disclaimer"),
            }
        else:
            paper = paper_performance.build_paper_report(persist=True)

    summary_path = Path(DATA_DIR) / "strategy_backtest_stable_summary.json"
    summary = _read_json(summary_path, {})
    empty = bool(paper.get("empty_book")) if paper else True
    return {
        "paper": paper,
        "source": paper.get("source") if paper else "none",
        "win_rate_pct": (paper.get("trading") or {}).get("win_rate_pct")
        if paper and not empty
        else summary.get("win_rate_pct"),
        "profit_factor": (paper.get("trading") or {}).get("profit_factor")
        if paper and not empty
        else summary.get("profit_factor"),
        "sharpe_ratio": (paper.get("risk") or {}).get("sharpe_ratio")
        if paper and not empty
        else summary.get("sharpe_ratio"),
        "max_drawdown_pct": (paper.get("risk") or {}).get("max_drawdown_pct")
        if paper and not empty
        else summary.get("max_drawdown_pct"),
        "total_return_pct": (paper.get("pnl") or {}).get("total_return_pct")
        if paper and not empty
        else summary.get("total_return_pct"),
        "num_trades": (paper.get("trading") or {}).get("num_trades")
        if paper
        else summary.get("num_trades"),
        "spy_return_pct": None if empty else (paper.get("benchmark") or {}).get("spy_return_pct"),
        "alpha_net_of_costs_pct": None if empty else (paper.get("benchmark") or {}).get("alpha_net_of_costs_pct"),
        "beats_spy_net": None if empty else (paper.get("benchmark") or {}).get("beats_spy_net"),
        "backtest": {
            "source": "stable_strategy_backtest" if summary else "none",
            "win_rate_pct": summary.get("win_rate_pct"),
            "profit_factor": summary.get("profit_factor"),
            "sharpe_ratio": summary.get("sharpe_ratio"),
            "max_drawdown_pct": summary.get("max_drawdown_pct"),
            "total_return_pct": summary.get("total_return_pct"),
            "num_trades": summary.get("num_trades"),
        },
    }


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "name": APP_NAME}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})
