"""
SignalProcessor — research/strategy signals → risk → execution.

Supports paper/live submission via OrderManager and shadow fills via
ShadowTradingEngine so the worker has a single execution entry point.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from backend.trading.broker import BrokerAdapter
from backend.trading.engine.order_manager import OrderManager
from backend.trading.engine.shadow import ShadowTradingEngine
from backend.trading.models import Order, OrderSide, OrderStatus, OrderType
from backend.trading.risk.gates import RiskEngine

logger = logging.getLogger(__name__)


class SignalProcessor:
    """
    Connects upstream strategy signals to the execution pipeline.

    Flow: draft Order → RiskEngine → (shadow simulate | OrderManager submit).
    """

    def __init__(
        self,
        broker: BrokerAdapter,
        risk_engine: RiskEngine,
        order_manager: OrderManager,
        *,
        execution_mode: str = "paper",
        shadow_engine: Optional[ShadowTradingEngine] = None,
    ):
        self.broker = broker
        self.risk_engine = risk_engine
        self.order_manager = order_manager
        self.execution_mode = execution_mode
        self.shadow_engine = shadow_engine

    def process_signal(
        self,
        signal: Dict[str, Any],
        *,
        account_summary: Optional[Any] = None,
        risk_extras: Optional[Dict[str, Any]] = None,
        next_bars: Optional[Any] = None,
    ) -> Order:
        """
        Args:
            signal: Dict with symbol, side, quantity, optional limit_price,
                    stop_price, take_profit_price, stop_loss_price,
                    idempotency_key, strategy_id.
            account_summary: Optional pre-fetched AccountSummary.
            risk_extras: Extra kwargs for RiskEngine.evaluate_order
                         (vix_level, price_history, cooldown_tickers, ...).
            next_bars: Shadow fill context for next-session open simulation.
        """
        symbol = signal["symbol"].upper()
        side = OrderSide.BUY if str(signal["side"]).lower() == "buy" else OrderSide.SELL

        order_type = OrderType.LIMIT if signal.get("limit_price") else OrderType.MARKET
        if signal.get("order_type"):
            order_type = OrderType(str(signal["order_type"]).lower())

        draft_order = Order(
            id=str(signal.get("id") or uuid.uuid4()),
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=float(signal.get("quantity", 0)),
            limit_price=signal.get("limit_price"),
            stop_price=signal.get("stop_price") or signal.get("stop_loss_price"),
            take_profit_price=signal.get("take_profit_price"),
            stop_loss_price=signal.get("stop_loss_price") or signal.get("stop_price"),
            order_class=str(signal.get("order_class") or "simple"),
            idempotency_key=signal.get("idempotency_key", str(uuid.uuid4())),
        )

        logger.info("Processing Signal: %s %s %s", side.value, draft_order.quantity, symbol)

        summary = account_summary if account_summary is not None else self.broker.get_account_summary()
        extras = risk_extras or {}
        risk_decision = self.risk_engine.evaluate_order(draft_order, summary, **extras)

        if not risk_decision.approved:
            logger.warning("Signal rejected by Risk Engine: %s", risk_decision.reason)
            draft_order.status = OrderStatus.REJECTED
            draft_order.error_message = f"Risk Gate Rejected: {risk_decision.reason}"
            self.order_manager.store.save_order(draft_order)
            return draft_order

        draft_order.status = OrderStatus.RISK_APPROVED

        if self.execution_mode == "shadow":
            if self.shadow_engine is None:
                draft_order.status = OrderStatus.REJECTED
                draft_order.error_message = "Shadow mode requested but ShadowTradingEngine is not configured"
                self.order_manager.store.save_order(draft_order)
                return draft_order
            logger.info("Signal Risk-Approved. Forwarding to ShadowTradingEngine.")
            bars = next_bars if next_bars is not None else None
            return self.shadow_engine.simulate_submission(draft_order, next_bars=bars)

        logger.info("Signal Risk-Approved. Forwarding to Order Manager.")
        # OrderManager.submit_new_order currently requires DRAFT and re-marks RISK_APPROVED.
        draft_order.status = OrderStatus.DRAFT
        return self.order_manager.submit_new_order(draft_order)
