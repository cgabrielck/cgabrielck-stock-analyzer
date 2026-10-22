import logging
from typing import Dict, List, Optional, Protocol
from datetime import datetime, timezone

from backend.trading.models import Order, OrderStatus
from backend.trading.broker import BrokerAdapter
from backend.trading.engine.sticky_idempotency import (
    find_sticky_order,
    is_acceptance_unknown_exception,
    mark_acceptance_unknown,
    order_side_value,
)

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

        Does **not** stamp RISK_APPROVED. Callers that actually ran RiskEngine
        (Desk place-order, SignalProcessor) must set that status themselves
        before submit. A DRAFT stays DRAFT until the broker responds.
        """
        if draft_order.status not in (OrderStatus.DRAFT, OrderStatus.RISK_APPROVED):
            raise ValueError(
                f"Can only submit DRAFT or RISK_APPROVED orders. Current status: {draft_order.status}"
            )

        # 1. Blind new key while a 502-class place is still open: do not submit.
        sticky = find_sticky_order(
            self.store, symbol=draft_order.symbol, side=order_side_value(draft_order)
        )
        if sticky is not None and sticky.idempotency_key != draft_order.idempotency_key:
            logger.warning(
                "Blind retry blocked for %s %s. Reuse idempotency_key=%s (refused %s).",
                draft_order.symbol,
                order_side_value(draft_order),
                sticky.idempotency_key,
                draft_order.idempotency_key,
            )
            return sticky

        # 2. Same-key idempotency. Unknown-accept may safe-retry with that key.
        existing_order = self.store.get_order_by_idempotency_key(draft_order.idempotency_key)
        safe_resubmit = (
            existing_order is not None
            and existing_order.idempotency_sticky
            and existing_order.status == OrderStatus.ACCEPTANCE_UNKNOWN
        )
        if existing_order is not None and not safe_resubmit:
            logger.info(
                "Order with idempotency key %s already exists. Skipping submission.",
                draft_order.idempotency_key,
            )
            return existing_order

        if safe_resubmit and existing_order is not None:
            draft_order.id = existing_order.id
            draft_order.idempotency_key = existing_order.idempotency_key
            draft_order.idempotency_sticky = True
        else:
            # Persist caller status as-is (no invented RISK_APPROVED).
            self.store.save_order(draft_order)

        logger.info(
            "Submitting order to broker: %s %s %s key=%s",
            draft_order.symbol,
            draft_order.side,
            draft_order.quantity,
            draft_order.idempotency_key,
        )

        # 3. Broker Submission
        try:
            submitted_order = self.broker.submit_order(draft_order)
        except Exception as e:
            if is_acceptance_unknown_exception(e):
                logger.error(
                    "Broker acceptance unknown for %s key=%s: %s",
                    draft_order.symbol,
                    draft_order.idempotency_key,
                    e,
                )
                mark_acceptance_unknown(draft_order, e)
                self.store.save_order(draft_order)
                return draft_order
            # Definite adapter failure (request not accepted). A new key may retry.
            logger.error(f"Failed to submit order to broker: {str(e)}")
            draft_order.status = OrderStatus.REJECTED
            draft_order.idempotency_sticky = False
            draft_order.error_message = f"Broker Adapter Error: {str(e)}"
            draft_order.failed_at = datetime.now(timezone.utc)
            self.store.save_order(draft_order)
            return draft_order

        # 4. Post-submission update. 502 mapped by the adapter is not a reject
        # and not a fill.
        if submitted_order.status == OrderStatus.ACCEPTANCE_UNKNOWN:
            if not submitted_order.error_message:
                mark_acceptance_unknown(submitted_order)
            else:
                submitted_order.idempotency_sticky = True
                submitted_order.filled_at = None
                submitted_order.filled_avg_price = None
            self.store.save_order(submitted_order)
            return submitted_order
        if submitted_order.status == OrderStatus.REJECTED:
            submitted_order.idempotency_sticky = False
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
