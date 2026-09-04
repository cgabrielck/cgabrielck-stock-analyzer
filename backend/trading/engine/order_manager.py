import logging
from typing import Dict, List, Optional, Protocol
from datetime import datetime, timezone

from backend.trading.models import Order, OrderStatus
from backend.trading.broker import BrokerAdapter

logger = logging.getLogger(__name__)

class OrderStore(Protocol):
    """Protocol for durable storage of orders and fills."""
    def save_order(self, order: Order) -> None: ...
    def get_order_by_idempotency_key(self, key: str) -> Optional[Order]: ...
    def get_order(self, order_id: str) -> Optional[Order]: ...
    def get_recent_orders(self, limit: int = 50) -> List[Order]: ...

class InMemoryOrderStore:
    """A simple in-memory store for development and testing."""
    def __init__(self):
        self._orders_by_id: Dict[str, Order] = {}
        self._orders_by_idem_key: Dict[str, Order] = {}

    def save_order(self, order: Order) -> None:
        self._orders_by_id[order.id] = order
        self._orders_by_idem_key[order.idempotency_key] = order

    def get_order_by_idempotency_key(self, key: str) -> Optional[Order]:
        return self._orders_by_idem_key.get(key)

    def get_order(self, order_id: str) -> Optional[Order]:
        return self._orders_by_id.get(order_id)

    def get_recent_orders(self, limit: int = 50) -> List[Order]:
        """Return the most recent orders (by updated_at, descending)."""
        all_orders = list(self._orders_by_id.values())
        all_orders.sort(key=lambda o: o.updated_at or o.created_at, reverse=True)
        return all_orders[:limit]


class OrderManager:
    """
    Manages the lifecycle of an order, enforcing state transitions and idempotency.
    """
    def __init__(self, broker: BrokerAdapter, store: OrderStore):
        self.broker = broker
        self.store = store

    def submit_new_order(self, draft_order: Order) -> Order:
        """
        Attempts to submit a new order. Includes idempotency checks.
        """
        if draft_order.status != OrderStatus.DRAFT:
            raise ValueError(f"Can only submit DRAFT orders. Current status: {draft_order.status}")

        # 1. Idempotency Check
        existing_order = self.store.get_order_by_idempotency_key(draft_order.idempotency_key)
        if existing_order:
            logger.info(f"Order with idempotency key {draft_order.idempotency_key} already exists. Skipping submission.")
            return existing_order

        # 2. Pre-submission setup
        # In a full implementation, the Risk Worker would have already evaluated this 
        # and changed the state to RISK_APPROVED. For now, we transition directly.
        draft_order.status = OrderStatus.RISK_APPROVED
        self.store.save_order(draft_order)
        
        logger.info(f"Submitting order to broker: {draft_order.symbol} {draft_order.side} {draft_order.quantity}")

        # 3. Broker Submission
        try:
            submitted_order = self.broker.submit_order(draft_order)
        except Exception as e:
            # Handle unexpected broker adapter errors
            logger.error(f"Failed to submit order to broker: {str(e)}")
            draft_order.status = OrderStatus.REJECTED
            draft_order.error_message = f"Broker Adapter Error: {str(e)}"
            draft_order.failed_at = datetime.now(timezone.utc)
            self.store.save_order(draft_order)
            return draft_order

        # 4. Post-submission update
        # submitted_order should now have an updated status (e.g. SUBMITTED or REJECTED)
        # and a broker-assigned ID.
        self.store.save_order(submitted_order)
        
        return submitted_order

    def sync_order_status(self, internal_order_id: str) -> Optional[Order]:
        """
        Fetches the latest status from the broker and updates the internal store.
        """
        local_order = self.store.get_order(internal_order_id)
        if not local_order:
             logger.warning(f"Order {internal_order_id} not found in local store during sync.")
             return None
        
        if local_order.status in [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED]:
             # Terminal states, no need to sync
             return local_order
            
        broker_order = self.broker.get_order(local_order.id)
        
        if broker_order:
            # Update local state
            if broker_order.status != local_order.status:
                logger.info(f"Order {local_order.id} status changed: {local_order.status} -> {broker_order.status}")
                local_order.status = broker_order.status
                local_order.filled_at = broker_order.filled_at
                local_order.cancelled_at = broker_order.cancelled_at
                local_order.failed_at = broker_order.failed_at
                local_order.error_message = broker_order.error_message
                self.store.save_order(local_order)
            return local_order
        else:
             logger.error(f"Order {local_order.id} exists locally but not found on broker during sync.")
             # We don't mark as error immediately, it could be a propagation delay on the broker side.
             # A robust system would retry or escalate.
             return local_order

    def cancel_active_order(self, internal_order_id: str) -> Optional[Order]:
        """
        Attempts to cancel an active order.
        """
        local_order = self.store.get_order(internal_order_id)
        if not local_order:
             logger.warning(f"Cannot cancel: Order {internal_order_id} not found.")
             return None
            
        if local_order.status in [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED]:
            logger.info(f"Order {internal_order_id} is already in terminal state: {local_order.status}")
            return local_order

        try:
            updated_order = self.broker.cancel_order(local_order.id)
            # Sync the cancelled state back to our local object
            local_order.status = updated_order.status
            local_order.cancelled_at = updated_order.cancelled_at
            local_order.error_message = updated_order.error_message
            self.store.save_order(local_order)
            return local_order
        except Exception as e:
            logger.error(f"Failed to cancel order {internal_order_id}: {str(e)}")
            # Fetch latest state in case it was filled while we tried to cancel
            return self.sync_order_status(internal_order_id)
