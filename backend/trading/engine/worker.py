"""
TradingWorker 2.0 — full auto-trading signal polling loop.

Upgrade from 1.0:
  - Replaces the `pass` stub _run_reconciliation with real position sync
  - Adds _run_strategy_signals() — the core auto-trading loop
  - Integrates StrategyBase, Kelly sizer, RiskEngine 2.0, and price_history
  - Tracks trailing stops per position
  - Tracks cooldown tickers (24h cool-down after sell)
  - Market-hours gate: only trade during US market hours (9:30–16:00 ET)
    unless IGNORE_MARKET_HOURS=true (allows strategy cycles anytime).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from backend.trading.broker import BrokerAdapter
from backend.trading.engine.order_manager import OrderManager, OrderStore
from backend.trading.engine.shadow import ShadowTradingEngine
from backend.trading.engine.signal_processor import SignalProcessor
from backend.trading.models import Order, OrderSide, OrderStatus, OrderType
from backend.trading.reconciliation.service import ReconciliationService
from backend.trading.risk.gates import RiskEngine, RiskLimits
from backend.trading.risk.position_sizer import shares_to_buy
from backend.trading.strategies.base import StrategyBase
from backend.trading.strategies.registry import KNOWN_STRATEGIES, get_strategy
from backend.trading.engine.fetch_policy import (
    env_universe_cap,
    parse_fetch_batch,
    parse_fetch_pause_sec,
    symbols_to_fetch,
)
from backend.trading.strategies import skip_codes
from backend.pathsetup import ensure_backend_on_path
from backend.trading.safety import audit, kill_switch
from backend.trading.safety.mandate import MandateGate, TradingMandate, load_mandate

ensure_backend_on_path()

logger = logging.getLogger(__name__)

# US Eastern timezone offset (ET = UTC-5 winter, UTC-4 summer)
# We approximate market hours as 14:30–21:00 UTC (conservative)
MARKET_OPEN_UTC_HOUR  = 14  # 09:30 ET = 14:30 UTC (winter)
MARKET_CLOSE_UTC_HOUR = 21  # 16:00 ET = 21:00 UTC

COOLDOWN_HOURS = 24


class TradingWorker:
    """
    Background daemon that:
      1. Every tick: syncs open order statuses with the broker.
      2. Every tick: checks exits (stop-loss, take-profit) for open positions.
      3. Every tick: generates strategy signals for the stock universe and submits orders.
      4. Once per day: runs account reconciliation and resets daily-loss tracker.

    All operations are guarded by market-hours gate (US equities only).
    """

    def __init__(
        self,
        broker: BrokerAdapter,
        store: OrderStore,
        manager: OrderManager,
        reconciler: ReconciliationService,
        strategy_id: str = "stable",
        risk_limits: Optional[RiskLimits] = None,
        ticker_universe: Optional[List[str]] = None,
        mandate: Optional[TradingMandate] = None,
        execution_mode: str = "paper",
        shadow_engine: Optional["ShadowTradingEngine"] = None,
    ):
        self.broker      = broker
        self.store       = store
        self.manager     = manager
        self.reconciler  = reconciler
        self.strategy: StrategyBase = get_strategy(strategy_id)
        self.risk_engine = RiskEngine(risk_limits or RiskLimits())
        # Mandate gate: declarative authorization envelope loaded from
        # config/mandate.json (permissive default preserves legacy behaviour).
        self.mandate_gate = MandateGate(mandate or load_mandate())
        # Execution mode: "paper" submits to broker, "shadow" simulates fills locally
        self.execution_mode = execution_mode
        self.shadow_engine = shadow_engine
        # Single entry point: strategy signals → risk → paper/shadow execution.
        self.signal_processor = SignalProcessor(
            broker,
            self.risk_engine,
            manager,
            execution_mode=execution_mode,
            shadow_engine=shadow_engine,
        )

        # Default universe — can be overridden
        if ticker_universe is None:
            try:
                from backend.utils.constants import STOCK_UNIVERSE
                self.ticker_universe = [s["ticker"] for s in STOCK_UNIVERSE]
            except Exception:
                self.ticker_universe = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]
        else:
            self.ticker_universe = ticker_universe

        self._running   = False
        self._thread: Optional[threading.Thread] = None
        self._last_reconcile_date: Optional[date] = None

        # Per-position trailing stop tracking: {symbol: highest_close_since_entry}
        self._trailing_highs: Dict[str, float] = {}
        # Per-position entry metadata: {symbol: {entry_price, stop, target, strategy_id}}
        self._position_meta: Dict[str, Dict[str, Any]] = {}
        # Cool-down set: tickers we sold recently
        self._cooldown: Dict[str, datetime] = {}

        # Latest stats (readable from UI without locking)
        self.last_run_utc: Optional[datetime] = None
        self.last_signal_summary: List[Dict[str, Any]] = []
        self.is_market_hours: bool = False
        self.is_halted: bool = False  # Kill switch state (checked every tick)
        # Latest market regime (SPY+VIX) — gates total exposure by target allocation
        self.last_regime: Optional[Dict[str, Any]] = None
        self._telegram_sent: set[str] = set()
        self.last_skip_counts: Dict[str, int] = {}
        self.last_universe_cap: int = len(self.ticker_universe)
        self.last_fetched: int = 0
        self.last_scan_stale: bool = False

    # ------------------------------------------------------------------
    # Public control API
    # ------------------------------------------------------------------

    def set_strategy(self, strategy_id: str) -> None:
        """Hot-swap strategy without restarting the worker."""
        self.strategy = get_strategy(strategy_id)
        logger.info("Strategy switched to: %s", strategy_id)

    def start(self, interval_seconds: int = 60) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(interval_seconds,),
            daemon=True,
            name="TradingWorker",
        )
        self._thread.start()
        logger.info("TradingWorker started — strategy=%s, interval=%ds",
                    self.strategy.strategy_id, interval_seconds)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=10.0)
        logger.info("TradingWorker stopped.")

    @property
    def running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _run_loop(self, interval_seconds: int) -> None:
        while self._running:
            try:
                now_utc = datetime.now(timezone.utc)
                self.is_market_hours = self._is_market_hours(now_utc)
                self.last_run_utc = now_utc

                # Always sync orders regardless of market hours
                self._sync_orders()

                if self.is_market_hours:
                    account = self._safe_get_account()
                    if account:
                        # Once per day: record start-of-day portfolio value
                        self.risk_engine.record_day_start(account.portfolio_value)
                        # Run strategy
                        self._run_strategy_signals(account)
                        # Run daily reconciliation
                        self._run_reconciliation(account)

            except Exception as exc:
                logger.error("TradingWorker loop error: %s", exc, exc_info=True)

            # Responsive sleep
            for _ in range(int(interval_seconds)):
                if not self._running:
                    break
                time.sleep(1)

    # ------------------------------------------------------------------
    # Strategy signal loop
    # ------------------------------------------------------------------

    def _run_strategy_signals(self, account) -> None:
        """Core auto-trading: fetch data, check exits, generate entries."""
        summary_rows: List[Dict[str, Any]] = []
        skip_counts: Dict[str, int] = {}

        # 0. Kill switch — highest-priority manual override.
        # When engaged, we still run EXITS (must be able to close positions)
        # but block ALL new entries. This is the emergency brake.
        was_halted = self.is_halted
        self.is_halted = kill_switch.is_halted()
        halt_reason = kill_switch.get_reason() if self.is_halted else None
        audit.log_kill_switch_check(self.is_halted, halt_reason)
        if self.is_halted:
            logger.warning("KILL SWITCH ENGAGED (%s) — new entries blocked, exits still active.",
                           halt_reason)
            if not was_halted:
                self._notify_kill_switch(halt_reason or "kill_switch_engaged")
            skip_codes.bump(skip_counts, "kill_switch")

        held_symbols = [p.symbol for p in getattr(account, "positions", []) or []]
        cap = env_universe_cap(len(self.ticker_universe))
        to_fetch = symbols_to_fetch(self.ticker_universe, held_symbols, cap)
        self.last_universe_cap = cap
        batch = parse_fetch_batch()
        pause = parse_fetch_pause_sec()

        price_history: Dict[str, pd.DataFrame] = {}
        for i, ticker in enumerate(to_fetch):
            if i and batch and i % batch == 0 and pause > 0:
                time.sleep(pause)
            df = self._fetch_ohlcv(ticker, period="1y")
            if df is not None and len(df) >= 30:
                price_history[ticker] = df
        self.last_fetched = len(price_history)

        fetched_u = {str(t).upper() for t in price_history}
        fetch_u = {str(t).upper() for t in to_fetch}
        univ_u = [str(t).upper() for t in self.ticker_universe]
        for symbol in univ_u:
            if symbol in fetched_u:
                continue
            if symbol in fetch_u:
                skip_codes.bump(skip_counts, "no_price")
            else:
                skip_codes.bump(skip_counts, "universe_capped")

        if not price_history:
            logger.warning(
                "No price data fetched — skipping strategy signals. "
                "Tried %s names (cap=%s of %s). No orders this cycle.",
                len(to_fetch), cap, len(self.ticker_universe),
            )
            skip_counts.setdefault("no_price", len(to_fetch) or 1)
            self.last_skip_counts = skip_counts
            self.last_signal_summary = [{"action": "NO_PRICE", "tried": len(to_fetch), "cap": cap}]
            return

        # 2. Fetch VIX for dampening
        vix_level = self._fetch_vix()

        # 2b. Detect market regime (SPY + VIX) — gates total exposure.
        regime = self._detect_regime()
        self.last_regime = regime
        target_allocation = float(regime.get("target_allocation", 0.70)) if regime else 0.70

        # 3. Clean expired cooldowns
        now = datetime.now(timezone.utc)
        self._cooldown = {
            t: ts for t, ts in self._cooldown.items()
            if (now - ts).total_seconds() < COOLDOWN_HOURS * 3600
        }
        cooldown_tickers = list(self._cooldown.keys())

        # 4. Check exits for open positions
        open_positions = {p.symbol: p for p in account.positions}
        for symbol, pos in open_positions.items():
            if symbol not in price_history:
                continue
            df = price_history[symbol]
            current_price = float(df["Close"].iloc[-1])

            # Update trailing high
            prev_high = self._trailing_highs.get(symbol, pos.average_entry_price)
            self._trailing_highs[symbol] = max(prev_high or 0.0, current_price)

            meta = self._position_meta.get(symbol, {})
            strategy_id = meta.get("strategy_id", self.strategy.strategy_id)
            strat = get_strategy(strategy_id)

            # Dynamic trailing stop for aggressive strategy
            trailing_stop = meta.get("stop_loss_price", pos.average_entry_price * 0.95)
            if strategy_id == "aggressive":
                from backend.trading.strategies.aggressive import AggressiveStrategy
                trailing_stop = self._trailing_highs[symbol] * (1 - AggressiveStrategy.TRAILING_STOP_PCT)
                # Update in meta
                if symbol in self._position_meta:
                    self._position_meta[symbol]["stop_loss_price"] = trailing_stop

            exit_sig = strat.check_exit(
                ticker=symbol,
                entry_price=pos.average_entry_price,
                current_price=current_price,
                df=df,
                stop_loss_price=trailing_stop,
                take_profit_price=meta.get("take_profit_price", current_price * 1.1),
            )

            if exit_sig:
                logger.info("EXIT signal for %s: %s @ %.4f", symbol, exit_sig.reason, exit_sig.exit_price)
                self._submit_sell(symbol, pos.quantity, exit_sig.exit_price, exit_sig.reason)
                self._cooldown[symbol] = now
                self._trailing_highs.pop(symbol, None)
                self._position_meta.pop(symbol, None)
                summary_rows.append({"ticker": symbol, "action": f"EXIT:{exit_sig.reason}",
                                      "price": exit_sig.exit_price})

        # 5. Scan universe for entry signals — prefer today's top-5 when available
        current_positions_list = [
            {"symbol": p.symbol, "quantity": p.quantity, "avg_entry": p.average_entry_price}
            for p in account.positions
        ]

        # Regime exposure gate: don't open new positions beyond the regime's
        # target allocation (e.g. bear/high-vol caps total invested at 40%).
        portfolio_value = float(getattr(account, "portfolio_value", 0.0) or 0.0)
        invested_value = 0.0
        for p in account.positions:
            px = price_history.get(p.symbol)
            mark = float(px["Close"].iloc[-1]) if px is not None and len(px) else float(p.average_entry_price)
            invested_value += mark * float(p.quantity)
        exposure_ratio = invested_value / portfolio_value if portfolio_value > 0 else 0.0
        allow_new_entries = exposure_ratio < target_allocation
        if not allow_new_entries:
            logger.info(
                "Regime=%s exposure gate: invested %.0f%% >= target %.0f%% — no new entries.",
                (regime or {}).get("regime", "n/a"), exposure_ratio * 100, target_allocation * 100,
            )
            skip_codes.bump(skip_counts, "regime_gate")
            summary_rows.append({
                "action": "REGIME_GATE",
                "regime": (regime or {}).get("regime", "n/a"),
                "exposure_pct": round(exposure_ratio * 100, 1),
                "target_pct": round(target_allocation * 100, 1),
            })

        top5 = set(self._get_top5_tickers())
        ordered_tickers = sorted(
            price_history.keys(),
            key=lambda t: (0 if t in top5 else 1, t),
        )
        research_stale_logged = False
        requires_fresh = bool(getattr(self.strategy, "requires_fresh_scan", False))
        self.last_scan_stale = False

        for ticker in ordered_tickers:
            if not allow_new_entries:
                break
            df = price_history[ticker]
            if ticker in open_positions:
                continue  # already holding — not a skip for new-entry diagnostics

            fund_info = self._get_fundamental_score_info(ticker)
            fund_score = float(fund_info.get("score", 50.0))
            if fund_info.get("stale"):
                self.last_scan_stale = True
                if not research_stale_logged:
                    logger.warning(
                        "research_stale: last_scan missing or older than session window — "
                        "research_list blocks new buys; Stable keeps last real fund score"
                    )
                    research_stale_logged = True
                    summary_rows.append({"action": "RESEARCH_STALE", "scan_ts": fund_info.get("scan_ts")})
                if requires_fresh:
                    skip_codes.bump(skip_counts, "research_stale")
                    continue
            llm_signal = None   # LLM not called in worker to avoid latency

            signal = self.strategy.generate_signal(
                ticker=ticker,
                df=df,
                fundamental_score=fund_score,
                llm_signal=llm_signal,
                current_positions=current_positions_list,
            )

            if signal is None:
                code = self.strategy.diagnose_entry(
                    ticker, df, fund_score, llm_signal, current_positions_list,
                ) or "no_setup"
                skip_codes.bump(skip_counts, code)
                continue

            # Overlay Deep Research stop / target as suggestions (RiskEngine still gates)
            plan = self._get_deep_trade_plan(ticker)
            if plan:
                stop = plan.get("stop_loss")
                targets = plan.get("targets") or []
                if stop is not None:
                    try:
                        signal.stop_loss_price = float(stop)
                    except (TypeError, ValueError):
                        pass
                if targets:
                    try:
                        signal.take_profit_price = float(targets[0])
                    except (TypeError, ValueError):
                        pass
                signal.meta = dict(signal.meta or {})
                signal.meta["deep_overlay"] = {
                    "stance": plan.get("stance"),
                    "action": plan.get("action"),
                    "entry_zone": plan.get("entry_zone"),
                }

            logger.info(
                "ENTRY signal for %s via %s: %s (fund=%.1f source=%s top5=%s)",
                ticker,
                signal.strategy_id,
                signal.reason,
                fund_score,
                fund_info.get("source"),
                ticker in top5,
            )

            # Kill switch gate (blocks BUYs only; exits still work)
            if self.is_halted:
                logger.info("BLOCKED %s: kill switch engaged", ticker)
                audit.log_mandate_decision(ticker, "buy", approved=False,
                                           reason="kill_switch_engaged")
                summary_rows.append({"ticker": ticker, "action": "BLOCKED", "reason": "kill_switch"})
                skip_codes.bump(skip_counts, "kill_switch")
                continue

            # Mandate gate (authorization check before committing capital)
            # Draft order for notional calc
            draft_for_mandate = Order(
                id=f"draft-{ticker}",
                symbol=ticker,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=1,  # placeholder — real qty computed next via Kelly
                limit_price=signal.entry_price,
                idempotency_key=f"mandate-check-{ticker}",
            )
            mandate_decision = self.mandate_gate.evaluate(draft_for_mandate, signal.entry_price)
            audit.log_mandate_decision(
                ticker, "buy", mandate_decision.approved, mandate_decision.reason,
                notional=signal.entry_price,
            )
            if not mandate_decision.approved:
                logger.info("BLOCKED %s: %s", ticker, mandate_decision.reason)
                summary_rows.append({"ticker": ticker, "action": "BLOCKED",
                                    "reason": mandate_decision.reason})
                skip_codes.bump(skip_counts, "mandate")
                continue

            # Kelly position sizing
            vix_mult = self.risk_engine.vix_size_multiplier(vix_level)
            qty = shares_to_buy(
                portfolio_value=account.portfolio_value * vix_mult,
                current_price=signal.entry_price,
                win_rate=self.strategy.expected_win_rate,
                avg_win_pct=signal.avg_win_pct,
                avg_loss_pct=signal.avg_loss_pct,
                kelly_fraction_scale=self.risk_engine.limits.kelly_scale,
                max_fraction=self.risk_engine.limits.max_position_concentration,
                min_shares=1,
            )

            if qty <= 0:
                skip_codes.bump(skip_counts, "kelly_zero")
                continue

            # Inject extra args for RiskEngine 2.0 via SignalProcessor
            account_refresh = self._safe_get_account()
            if account_refresh is None:
                continue

            use_bracket = (
                self.execution_mode != "shadow"
                and signal.stop_loss_price is not None
                and signal.take_profit_price is not None
            )
            signal_dict = {
                "id": f"{ticker}-{now.strftime('%Y%m%d%H%M')}",
                "symbol": ticker,
                "side": "buy",
                "quantity": qty,
                "limit_price": signal.entry_price,
                "order_type": "limit",
                "stop_loss_price": signal.stop_loss_price,
                "take_profit_price": signal.take_profit_price,
                "order_class": "bracket" if use_bracket else "simple",
                "idempotency_key": f"{ticker}-{now.strftime('%Y%m%d%H%M')}",
                "strategy_id": signal.strategy_id,
            }

            next_bars = (
                self._fetch_next_bar(ticker, signal.entry_price)
                if self.execution_mode == "shadow"
                else None
            )
            result = self.signal_processor.process_signal(
                signal_dict,
                account_summary=account_refresh,
                risk_extras={
                    "vix_level": vix_level,
                    "price_history": price_history,
                    "cooldown_tickers": cooldown_tickers,
                },
                next_bars=next_bars,
            )

            # Audit the risk / submission outcome
            if (result.error_message or "").startswith("Risk Gate Rejected"):
                audit.log_risk_decision(ticker, "buy", qty, False, result.error_message or "")
                logger.info("BLOCKED %s: %s", ticker, result.error_message)
                summary_rows.append({
                    "ticker": ticker, "action": "BLOCKED",
                    "reason": result.error_message,
                })
                skip_codes.bump(skip_counts, "risk_rejected")
                continue

            audit.log_risk_decision(ticker, "buy", qty, True, "approved_via_signal_processor")
            self.mandate_gate.record_order()
            audit.log_order_submission(
                ticker, "buy", qty, signal.entry_price,
                result.id, result.status.value,
            )

            if result.status in (OrderStatus.REJECTED,):
                logger.warning("Order rejected for %s: %s", ticker, result.error_message)
                continue

            self._notify_trade(
                "decision",
                symbol=ticker,
                side="buy",
                quantity=qty,
                price=float(signal.entry_price),
                stop_loss=signal.stop_loss_price,
                take_profit=signal.take_profit_price,
                strategy=signal.strategy_id,
                reason=signal.reason,
                order_id=result.id,
                status=result.status.value,
            )
            if result.status == OrderStatus.FILLED:
                self._notify_trade(
                    "fill",
                    symbol=ticker,
                    side="buy",
                    quantity=qty,
                    price=float(result.filled_avg_price or signal.entry_price),
                    stop_loss=signal.stop_loss_price or result.stop_loss_price,
                    take_profit=signal.take_profit_price or result.take_profit_price,
                    strategy=signal.strategy_id,
                    reason=signal.reason,
                    order_id=result.id,
                    status=result.status.value,
                )

            self._position_meta[ticker] = {
                "stop_loss_price":   signal.stop_loss_price,
                "take_profit_price": signal.take_profit_price,
                "strategy_id":       signal.strategy_id,
            }
            self._trailing_highs[ticker] = signal.entry_price
            summary_rows.append({"ticker": ticker, "action": "BUY",
                                  "qty": qty, "price": signal.entry_price,
                                  "reason": signal.reason})

        self.last_skip_counts = skip_counts
        self.last_signal_summary = summary_rows
        if skip_counts:
            logger.info("why_no_trade this cycle: %s", skip_counts)

    # ------------------------------------------------------------------
    # Order sync  (from 1.0)
    # ------------------------------------------------------------------

    def _sync_orders(self) -> None:
        active_states = {OrderStatus.SUBMITTED, OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}
        try:
            orders = self.store.get_recent_orders(limit=100)
        except Exception:
            return
        for order in orders:
            if order.status in active_states:
                try:
                    updated = self.manager.sync_order_status(order.id)
                except Exception as exc:
                    logger.debug("sync_order_status failed for %s: %s", order.id, exc)
                    continue
                if updated and updated.status == OrderStatus.FILLED:
                    meta = self._position_meta.get(updated.symbol, {})
                    self._notify_trade(
                        "fill",
                        symbol=updated.symbol,
                        side=updated.side.value,
                        quantity=updated.quantity,
                        price=float(updated.filled_avg_price or updated.limit_price or 0),
                        stop_loss=updated.stop_loss_price or meta.get("stop_loss_price"),
                        take_profit=updated.take_profit_price or meta.get("take_profit_price"),
                        strategy=meta.get("strategy_id"),
                        order_id=updated.id,
                        status=updated.status.value,
                    )

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    def _run_reconciliation(self, account) -> None:
        today = date.today()
        if self._last_reconcile_date == today:
            return
        try:
            report = self.reconciler.reconcile_positions(account.positions)
            if not report.is_match:
                logger.warning("Reconciliation discrepancies: %s", report.discrepancies)
            self._last_reconcile_date = today
        except Exception as exc:
            logger.error("Reconciliation failed: %s", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_market_hours(self, now_utc: datetime) -> bool:
        """Approximate US market hours gate (Mon–Fri, 14:30–21:00 UTC).

        Set IGNORE_MARKET_HOURS=true to run strategy cycles around the clock.
        Broker may still queue/reject orders outside regular session.
        """
        if os.getenv("IGNORE_MARKET_HOURS", "").strip().lower() in ("1", "true", "yes"):
            return True
        if now_utc.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        hour = now_utc.hour + now_utc.minute / 60
        return MARKET_OPEN_UTC_HOUR + 0.5 <= hour <= MARKET_CLOSE_UTC_HOUR

    def _safe_get_account(self):
        try:
            return self.broker.get_account_summary()
        except Exception as exc:
            logger.error("Failed to get account summary: %s", exc)
            return None

    def _fetch_ohlcv(self, ticker: str, period: str = "1y") -> Optional[pd.DataFrame]:
        """Fetch daily OHLCV — Polygon preferred when keyed, else Yahoo."""
        ensure_backend_on_path()
        try:
            from backend.agents import polygon_equity
            if polygon_equity.is_configured():
                poly = polygon_equity.fetch_chart_data_polygon(ticker, period_hint=period)
                if not poly.get("error") and poly.get("data") is not None and not poly["data"].empty:
                    return poly["data"]
        except Exception as exc:
            logger.debug("Polygon OHLCV fetch failed for %s: %s", ticker, exc)
        try:
            import yfinance as yf

            hist = yf.Ticker(ticker).history(period=period or "1y", interval="1d", auto_adjust=False)
            if hist is not None and not hist.empty and len(hist) >= 30:
                return hist
        except Exception as exc:
            logger.debug("Yahoo OHLCV fetch failed for %s: %s", ticker, exc)
        try:
            from backend.utils.chart_utils import fetch_chart_data

            result = fetch_chart_data(ticker, "1d", False)
            if result.get("error"):
                logger.debug("chart_utils error for %s: %s", ticker, result.get("error"))
                return None
            df = result.get("data")
            if df is None or df.empty:
                return None
            return df
        except Exception as exc:
            logger.warning("OHLCV fetch failed for %s: %s", ticker, exc)
            return None

    def _notify_trade(
        self,
        kind: str,
        *,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        stop_loss: Any = None,
        take_profit: Any = None,
        strategy: Optional[str] = None,
        reason: Optional[str] = None,
        order_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> None:
        key = f"{order_id or symbol}:{kind}:{side}"
        if key in self._telegram_sent:
            return
        try:
            from backend.config import get_telegram_settings
            from backend.trading import ops_notifier

            settings = get_telegram_settings()
            if kind == "decision":
                ok = ops_notifier.notify_trade_decision(
                    settings,
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    price=price,
                    mode=self.execution_mode,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    strategy=strategy,
                    reason=reason,
                    order_id=order_id,
                    status=status,
                )
            else:
                ok = ops_notifier.notify_trade_fill(
                    settings,
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    price=price,
                    mode=self.execution_mode,
                    order_id=order_id,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    strategy=strategy,
                    reason=reason,
                    status=status,
                )
            if ok:
                self._telegram_sent.add(key)
                if len(self._telegram_sent) > 400:
                    self._telegram_sent = set(list(self._telegram_sent)[-200:])
        except Exception as exc:
            logger.debug("ops %s notify skipped: %s", kind, exc)

    def _notify_fill(self, order: Order, price: float) -> None:
        meta = self._position_meta.get(order.symbol, {})
        self._notify_trade(
            "fill",
            symbol=order.symbol,
            side=order.side.value,
            quantity=order.quantity,
            price=price,
            stop_loss=order.stop_loss_price or meta.get("stop_loss_price"),
            take_profit=order.take_profit_price or meta.get("take_profit_price"),
            strategy=meta.get("strategy_id"),
            order_id=order.id,
            status=order.status.value,
        )

    def _notify_kill_switch(self, reason: str) -> None:
        try:
            from backend.config import get_telegram_settings
            from backend.trading import ops_notifier
            ops_notifier.notify_kill_switch(
                get_telegram_settings(),
                reason=reason,
                triggered_by="worker",
            )
        except Exception as exc:
            logger.debug("ops kill-switch notify skipped: %s", exc)

    def _fetch_vix(self) -> Optional[float]:
        """Fetch latest VIX close."""
        try:
            import yfinance as yf
            vix = yf.Ticker("^VIX")
            hist = vix.history(period="2d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:
            pass
        return None

    def _detect_regime(self) -> Optional[Dict[str, Any]]:
        """Detect the global market regime (SPY + VIX).

        Returns the regime dict from market_regime.detect_global_market_regime(),
        which includes a `target_allocation` used to gate total exposure. On any
        failure we return None and the caller falls back to a neutral 0.70 cap.
        """
        try:
            from backend.agents.market_regime import detect_global_market_regime
            return detect_global_market_regime()
        except Exception as exc:
            logger.debug("Regime detection failed: %s", exc)
            return None

    def _get_fundamental_score(self, ticker: str) -> float:
        return float(self._get_fundamental_score_info(ticker).get("score", 50.0))

    def _get_fundamental_score_info(self, ticker: str) -> Dict[str, Any]:
        """Prefer last Scan cache; fall back to Cache then neutral 50."""
        try:
            from backend.api.research_jobs import get_fund_score

            info = get_fund_score(ticker)
            # Keep last real Scan score even when the book is stale (as-of flag).
            if info.get("source") in ("last_scan", "last_scan_stale"):
                return info
            raw = info.get("raw_score")
        except Exception as exc:
            logger.debug("last_scan fund lookup failed for %s: %s", ticker, exc)
            info = {"score": 50.0, "source": "default", "stale": True}
            raw = None
        try:
            from backend.utils.cache import Cache

            cache = Cache()
            cached = cache.get(f"fund_score_{ticker}", "fundamentals", ttl=86400)
            if cached is not None:
                return {
                    "score": float(cached),
                    "raw_score": float(cached),
                    "source": "cache",
                    "stale": bool(info.get("stale", True)),
                    "scan_ts": info.get("scan_ts"),
                    "in_top5": bool(info.get("in_top5")),
                }
        except Exception:
            pass
        if raw is not None:
            try:
                return {**info, "score": float(raw)}
            except (TypeError, ValueError):
                pass
        return {
            "score": 50.0,
            "raw_score": raw,
            "source": info.get("source") or "default",
            "stale": True,
            "scan_ts": info.get("scan_ts"),
            "in_top5": False,
        }

    def _get_top5_tickers(self) -> List[str]:
        try:
            from backend.api.research_jobs import latest_scan

            scan = latest_scan()
            if scan.get("stale"):
                return []
            return [str(t).upper() for t in (scan.get("top5_tickers") or []) if t]
        except Exception:
            return []

    def _get_deep_trade_plan(self, ticker: str) -> Optional[Dict[str, Any]]:
        try:
            from backend.api.research_jobs import get_deep_trade_plan

            return get_deep_trade_plan(ticker)
        except Exception as exc:
            logger.debug("deep plan lookup failed for %s: %s", ticker, exc)
            return None

    def _submit_sell(self, symbol: str, quantity: float, price: float, reason: str) -> None:
        """Submit a market sell order for an open position."""
        import uuid
        idem = f"sell-{symbol}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        order = Order(
            id=str(uuid.uuid4()),
            symbol=symbol,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=quantity,
            limit_price=round(price * 0.995, 4),  # slight discount to ensure fill
            idempotency_key=idem,
        )
        try:
            if self.execution_mode == "shadow" and self.shadow_engine is not None:
                # Shadow mode: simulate fill with next-day bar
                order.status = OrderStatus.RISK_APPROVED
                next_bars = self._fetch_next_bar(symbol, price)
                self.shadow_engine.simulate_submission(order, next_bars=next_bars)
            else:
                # Paper/live mode: submit to broker
                self.manager.submit_new_order(order)
            logger.info("SELL submitted: %s ×%.0f @ %.4f (%s)", symbol, quantity, price, reason)
        except Exception as exc:
            logger.error("SELL submission failed for %s: %s", symbol, exc)

    def _fetch_next_bar(self, symbol: str, ref_price: float) -> Optional[pd.DataFrame]:
        """
        Fetch a single next-day bar for shadow fill simulation.
        
        In real shadow mode we'd wait for tomorrow's actual bar. For now we 
        approximate with today's last bar as a conservative fill estimate.
        """
        df = self._fetch_ohlcv(symbol, period="5d")
        if df is None or len(df) < 2:
            # Fallback: synthetic bar at ref_price
            return pd.DataFrame({
                "Open": [ref_price],
                "High": [ref_price * 1.002],
                "Low": [ref_price * 0.998],
                "Close": [ref_price],
            })
        return df.tail(1)


# ---------------------------------------------------------------------------
# CLI entry point — run as:  python -m backend.trading.engine.worker
# ---------------------------------------------------------------------------

def _build_worker(strategy_id: str, ticker_universe: Optional[List[str]], 
                  execution_mode: str = "paper") -> TradingWorker:
    """Build a fully wired TradingWorker from environment configuration."""
    from dotenv import load_dotenv
    from pathlib import Path

    load_dotenv()

    from backend.trading.alpaca_broker import AlpacaBroker
    from backend.trading.storage import create_order_store
    from backend.trading.reconciliation.service import ReconciliationService
    from backend.trading.engine.order_manager import OrderManager
    from backend.utils.constants import DATA_DIR

    # Shadow mode uses a separate order ledger
    if execution_mode == "shadow":
        store = create_order_store(backend="sqlite", filepath=str(Path(DATA_DIR) / "shadow_orders.sqlite3"))
    else:
        store = create_order_store()
    
    broker = AlpacaBroker()
    manager = OrderManager(broker=broker, store=store)
    reconciler = ReconciliationService(broker)
    
    # Shadow engine only needed in shadow mode
    shadow_engine = None
    if execution_mode == "shadow":
        shadow_engine = ShadowTradingEngine(order_manager=manager)

    return TradingWorker(
        broker=broker,
        store=store,
        manager=manager,
        reconciler=reconciler,
        strategy_id=strategy_id,
        ticker_universe=ticker_universe,
        execution_mode=execution_mode,
        shadow_engine=shadow_engine,
    )


def main(argv: Optional[List[str]] = None) -> int:
    """Standalone TradingWorker daemon entry point.

    Design notes:
      - Worker runs in a daemon thread (same as the Streamlit path) while the
        main thread blocks on a shutdown Event so SIGTERM/SIGINT exit cleanly.
      - Heartbeat is written to a JSON file (--heartbeat-file) so systemd or a
        monitoring loop can detect a hung worker.
      - Refuses to start with live credentials unless --allow-live is passed,
        guarding against accidental real-money trading during validation.
    """
    import argparse
    import json
    import signal

    parser = argparse.ArgumentParser(description="ALPHA//DESK standalone trading worker")
    parser.add_argument("--strategy", default="stable",
                        choices=list(KNOWN_STRATEGIES),
                        help="Strategy to run (default: stable)")
    parser.add_argument("--mode", default="shadow", choices=["paper", "shadow"],
                        help="Execution mode: 'paper' submits to broker, 'shadow' simulates fills locally (default: shadow)")
    parser.add_argument("--interval", type=int, default=60,
                        help="Polling interval in seconds (default: 60)")
    parser.add_argument("--tickers", nargs="*", default=None,
                        help="Override ticker universe (default: STOCK_UNIVERSE)")
    from backend.utils.constants import DATA_DIR as _DATA_DIR
    _default_hb = str(Path(_DATA_DIR) / "worker_heartbeat.json")
    parser.add_argument("--heartbeat-file", default=_default_hb,
                        help="Write a JSON heartbeat file each loop for external monitoring")
    parser.add_argument("--allow-live", action="store_true",
                        help="Allow running against a live (non-paper) Alpaca account")
    parser.add_argument("--once", action="store_true",
                        help="Run a single strategy cycle and exit (for smoke tests)")
    parser.add_argument("--halt", metavar="REASON", nargs="?", const="Manual halt via CLI",
                        help="Engage the kill switch (blocks new BUYs) and exit")
    parser.add_argument("--resume", action="store_true",
                        help="Disengage the kill switch and exit")
    parser.add_argument("--status", action="store_true",
                        help="Print kill switch status and exit")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Kill switch control commands — no worker/credentials needed.
    from backend.trading.safety import kill_switch as _ks
    if args.halt is not None:
        _ks.engage(args.halt)
        logger.warning("KILL SWITCH ENGAGED: %s", args.halt)
        return 0
    if args.resume:
        _ks.disengage()
        logger.info("Kill switch disengaged — normal trading resumes.")
        return 0
    if args.status:
        if _ks.is_halted():
            logger.warning("Kill switch is ENGAGED: %s", _ks.get_reason())
        else:
            logger.info("Kill switch is DISENGAGED (normal trading).")
        return 0

    from dotenv import load_dotenv
    load_dotenv()

    import os
    is_paper = os.getenv("APCA_PAPER", "true").lower() == "true"
    
    # Shadow mode bypasses broker entirely, so live-guard is not applicable
    if args.mode == "paper" and not is_paper and not args.allow_live:
        logger.error("APCA_PAPER is not 'true'. Refusing to start standalone worker "
                     "without --allow-live. This protects against accidental live trading.")
        return 2

    worker = _build_worker(args.strategy, args.tickers, execution_mode=args.mode)
    logger.info("Worker initialized: mode=%s strategy=%s universe=%d interval=%ds paper=%s",
                args.mode, args.strategy, len(worker.ticker_universe), args.interval, is_paper)

    def _write_heartbeat() -> None:
        if not args.heartbeat_file:
            return
        try:
            counts = getattr(worker, "last_skip_counts", {})
            if not isinstance(counts, dict):
                counts = {}
            payload = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "running": worker.running,
                "market_hours": worker.is_market_hours,
                "last_run": worker.last_run_utc.isoformat() if worker.last_run_utc else None,
                "last_signals": worker.last_signal_summary[-10:] if isinstance(worker.last_signal_summary, list) else [],
                "skip_counts": counts,
                "universe_cap": getattr(worker, "last_universe_cap", None) if isinstance(getattr(worker, "last_universe_cap", None), (int, float)) else None,
                "universe_size": len(list(getattr(worker, "ticker_universe", []) or [])),
                "fetched": getattr(worker, "last_fetched", None) if isinstance(getattr(worker, "last_fetched", None), (int, float)) else None,
                "scan_stale": bool(getattr(worker, "last_scan_stale", False) is True),
                "mode": args.mode,
                "strategy": args.strategy,
                "paper": is_paper,
                "pid": os.getpid(),
                "interval_seconds": args.interval,
                "halted": worker.is_halted,
                "ignore_market_hours": os.getenv("IGNORE_MARKET_HOURS", "").strip().lower()
                in ("1", "true", "yes"),
            }
            with open(args.heartbeat_file, "w") as fh:
                json.dump(payload, fh, default=str)
        except Exception as exc:  # pragma: no cover - best-effort monitoring
            logger.error("Heartbeat write failed: %s", exc)

    if args.once:
        worker.is_market_hours = True
        worker._sync_orders()
        account = worker._safe_get_account()
        if account:
            worker._run_strategy_signals(account)
        _write_heartbeat()
        logger.info("Single-cycle run complete.")
        return 0

    shutdown = threading.Event()

    def _signal_handler(_signum, _frame):  # pragma: no cover - exercised by OS signals
        logger.info("Shutdown signal received, stopping worker...")
        shutdown.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    worker.start(interval_seconds=args.interval)

    # Report heartbeat periodically even during off-hours so monitors know we are alive.
    try:
        while not shutdown.is_set():
            _write_heartbeat()
            shutdown.wait(timeout=min(30, args.interval))
    finally:
        worker.stop()
        _write_heartbeat()
        logger.info("Worker exited cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

