import logging
import uuid
from typing import Dict, Any, List
from datetime import datetime

from backend.trading.models import Order, OrderSide, OrderType, OrderStatus
from backend.trading.engine.order_manager import OrderManager
from backend.trading.risk.gates import RiskEngine
from backend.trading.broker import BrokerAdapter

logger = logging.getLogger(__name__)

class SignalProcessor:
    """
    Connects the upstream analytical research/signals to the execution pipeline.
    It takes an abstract signal, passes it through the RiskEngine, and if approved,
    submits an OrderIntent (via OrderManager).
    """
    def __init__(self, broker: BrokerAdapter, risk_engine: RiskEngine, order_manager: OrderManager):
        self.broker = broker
        self.risk_engine = risk_engine
        self.order_manager = order_manager

    def process_signal(self, signal: Dict[str, Any]) -> Order:
        """
        Args:
            signal: Dict containing 'symbol', 'side' (buy/sell), 'quantity', 'limit_price', 'strategy_id'
        Returns:
            The processed Order (which may be Rejected by risk, or Submitted).
        """
        symbol = signal['symbol'].upper()
        side = OrderSide.BUY if signal['side'].lower() == 'buy' else OrderSide.SELL
        
        # 1. Construct Draft Order
        draft_order = Order(
            id=str(uuid.uuid4()), # Temporary ID, broker will assign real one
            symbol=symbol,
            side=side,
            order_type=OrderType.LIMIT if signal.get('limit_price') else OrderType.MARKET,
            quantity=signal.get('quantity', 0),
            limit_price=signal.get('limit_price'),
            idempotency_key=signal.get('idempotency_key', str(uuid.uuid4()))
        )

        logger.info(f"Processing Signal: {side.value} {draft_order.quantity} {symbol}")

        # 2. Risk Evaluation
        account_summary = self.broker.get_account_summary()
        risk_decision = self.risk_engine.evaluate_order(draft_order, account_summary)

        if not risk_decision.approved:
            logger.warning(f"Signal rejected by Risk Engine: {risk_decision.reason}")
            draft_order.status = OrderStatus.REJECTED
            draft_order.error_message = f"Risk Gate Rejected: {risk_decision.reason}"
            # Even rejected intents are saved for audit
            self.order_manager.store.save_order(draft_order)
            return draft_order

        # 3. Submit to Execution Pipeline
        logger.info("Signal Risk-Approved. Forwarding to Order Manager.")
        draft_order.status = OrderStatus.DRAFT # Resetting explicitly just in case
        
        # OrderManager handles the transition from DRAFT -> RISK_APPROVED -> SUBMITTED
        return self.order_manager.submit_new_order(draft_order)
