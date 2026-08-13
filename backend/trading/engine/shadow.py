import logging
from typing import Optional
from datetime import datetime, timezone

from backend.trading.models import Order, OrderStatus
from backend.trading.engine.order_manager import OrderManager

logger = logging.getLogger(__name__)

class ShadowTradingEngine:
    """
    Simulates the execution of orders without submitting them to a real broker.
    It transitions state and models expected fills to compare against real market data later.
    """
    def __init__(self, order_manager: OrderManager):
        self.order_manager = order_manager

    def simulate_submission(self, order: Order) -> Order:
        """
        Takes a Risk-Approved order and simulates broker submission and eventual filling.
        """
        if order.status != OrderStatus.RISK_APPROVED:
            logger.warning(f"Shadow engine expects RISK_APPROVED orders. Got: {order.status}")
            return order

        # Transition to submitted
        order.status = OrderStatus.SUBMITTED
        order.submitted_at = datetime.now(timezone.utc)
        self.order_manager.store.save_order(order)
        
        logger.info(f"[SHADOW] Simulated submission for {order.symbol} {order.side}")

        # Simulate immediate fill at the limit price for shadow testing purposes
        # In a more advanced shadow engine, we'd wait for actual market data to cross the limit price.
        if order.limit_price:
            order.status = OrderStatus.FILLED
            order.filled_at = datetime.now(timezone.utc)
            self.order_manager.store.save_order(order)
            logger.info(f"[SHADOW] Simulated FILL for {order.symbol} at {order.limit_price}")

        return order
