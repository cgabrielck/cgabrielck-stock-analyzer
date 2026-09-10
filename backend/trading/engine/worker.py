"""
TradingWorker 2.0 — full auto-trading signal polling loop.

Upgrade from 1.0:
  - Replaces the `pass` stub _run_reconciliation with real position sync
  - Adds _run_strategy_signals() — the core auto-trading loop
  - Integrates StrategyBase, Kelly sizer, RiskEngine 2.0, and price_history
  - Tracks trailing stops per position
  - Tracks cooldown tickers (24h cool-down after sell)
  - Market-hours gate: only trade during US market hours (9:30–16:00 ET)
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone, timedelta, date
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
from backend.trading.strategies.registry import get_strategy
from backend.trading.safety import audit, kill_switch
from backend.trading.safety.mandate import MandateGate, TradingMandate, load_mandate

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

        # 1. Fetch price history for the universe (last 250 trading days)
        price_history: Dict[str, pd.DataFrame] = {}
        for ticker in self.ticker_universe[:20]:   # cap at 20 per tick to limit API calls
            df = self._fetch_ohlcv(ticker, period="1y")
            if df is not None and len(df) >= 30:
                price_history[ticker] = df

        if not price_history:
            logger.warning("No price data fetched — skipping strategy signals.")
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

        # 5. Scan universe for entry signals
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
            summary_rows.append({
                "action": "REGIME_GATE",
                "regime": (regime or {}).get("regime", "n/a"),
                "exposure_pct": round(exposure_ratio * 100, 1),
                "target_pct": round(target_allocation * 100, 1),
            })

        for ticker, df in price_history.items():
            if not allow_new_entries:
                break
            if ticker in open_positions:
                continue  # already holding

            # Get fundamental score (fast — no LLM)
            fund_score = self._get_fundamental_score(ticker)
            llm_signal = None   # LLM not called in worker to avoid latency

            signal = self.strategy.generate_signal(
                ticker=ticker,
                df=df,
                fundamental_score=fund_score,
                llm_signal=llm_signal,
                current_positions=current_positions_list,
            )

            if signal is None:
                continue

            logger.info("ENTRY signal for %s via %s: %s", ticker, signal.strategy_id, signal.reason)

            # Kill switch gate (blocks BUYs only; exits still work)
            if self.is_halted:
                logger.info("BLOCKED %s: kill switch engaged", ticker)
                audit.log_mandate_decision(ticker, "buy", approved=False,
                                           reason="kill_switch_engaged")
                summary_rows.append({"ticker": ticker, "action": "BLOCKED", "reason": "kill_switch"})
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

            if result.status == OrderStatus.FILLED:
                self._notify_fill(result, float(result.filled_avg_price or signal.entry_price))

            self._position_meta[ticker] = {
                "stop_loss_price":   signal.stop_loss_price,
                "take_profit_price": signal.take_profit_price,
                "strategy_id":       signal.strategy_id,
            }
            self._trailing_highs[ticker] = signal.entry_price
            summary_rows.append({"ticker": ticker, "action": "BUY",
                                  "qty": qty, "price": signal.entry_price,
                                  "reason": signal.reason})

        self.last_signal_summary = summary_rows

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
                    self.manager.sync_order_status(order.id)
                except Exception as exc:
                    logger.debug("sync_order_status failed for %s: %s", order.id, exc)

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
        """Approximate US market hours gate (Mon–Fri, 14:30–21:00 UTC)."""
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
        """Fetch daily OHLCV — Polygon preferred when configured, else Yahoo."""
        try:
            from backend.agents import polygon_equity
            if polygon_equity.is_configured():
                poly = polygon_equity.fetch_chart_data_polygon(ticker, period_hint=period)
                if not poly.get("error") and poly.get("data") is not None and not poly["data"].empty:
                    return poly["data"]
        except Exception as exc:
            logger.debug("Polygon OHLCV fetch failed for %s: %s", ticker, exc)
        try:
            from backend.utils.chart_utils import fetch_chart_data
            result = fetch_chart_data(ticker, "1d", False)
            if result.get("error"):
                return None
            df = result.get("data")
            if df is None or df.empty:
                return None
            return df
        except Exception as exc:
            logger.debug("OHLCV fetch failed for %s: %s", ticker, exc)
            return None

    def _notify_fill(self, order: Order, price: float) -> None:
        """Best-effort Telegram ops notification on fills."""
        try:
            from backend.config import get_telegram_settings
            from backend.trading import ops_notifier
            settings = get_telegram_settings()
            ops_notifier.notify_trade_fill(
                settings,
                symbol=order.symbol,
                side=order.side.value,
                quantity=order.quantity,
                price=price,
                mode=self.execution_mode,
                order_id=order.id,
            )
        except Exception as exc:
            logger.debug("ops fill notify skipped: %s", exc)

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
        """
        Fast fundamental score lookup from cache.
        Falls back to 50.0 (neutral) if not cached.
        """
        try:
            from backend.utils.cache import Cache
            cache = Cache()
            cached = cache.get(f"fund_score_{ticker}")
            if cached is not None:
                return float(cached)
        except Exception:
            pass
        return 50.0

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
    parser.add_argument("--strategy", default="stable", choices=["stable", "aggressive", "hybrid"],
                        help="Strategy to run (default: stable)")
    parser.add_argument("--mode", default="shadow", choices=["paper", "shadow"],
                        help="Execution mode: 'paper' submits to broker, 'shadow' simulates fills locally (default: shadow)")
    parser.add_argument("--interval", type=int, default=60,
                        help="Polling interval in seconds (default: 60)")
    parser.add_argument("--tickers", nargs="*", default=None,
                        help="Override ticker universe (default: STOCK_UNIVERSE)")
    parser.add_argument("--heartbeat-file", default=None,
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
            payload = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "running": worker.running,
                "market_hours": worker.is_market_hours,
                "last_run": worker.last_run_utc.isoformat() if worker.last_run_utc else None,
                "last_signals": worker.last_signal_summary[-10:],
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

