import unittest
from unittest.mock import MagicMock
from datetime import datetime

from backend.trading.models import Order, OrderSide, OrderType, OrderStatus
from backend.trading.engine.order_manager import OrderManager, InMemoryOrderStore

class TestOrderManager(unittest.TestCase):
    def setUp(self):
        self.mock_broker = MagicMock()
        self.store = InMemoryOrderStore()
        self.manager = OrderManager(broker=self.mock_broker, store=self.store)

    def _create_draft_order(self, idem_key="idem_123"):
        return Order(
            id="internal_id_1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10,
            idempotency_key=idem_key
        )

    def test_submit_new_order_success(self):
        draft_order = self._create_draft_order()
        
        # Mock broker response
        broker_order = draft_order.model_copy()
        broker_order.status = OrderStatus.SUBMITTED
        broker_order.id = "broker_id_1"
        self.mock_broker.submit_order.return_value = broker_order

        result = self.manager.submit_new_order(draft_order)

        # Assertions
        self.assertEqual(result.status, OrderStatus.SUBMITTED)
        self.assertEqual(result.id, "broker_id_1")
        
        # Verify it was saved to the store
        saved_order = self.store.get_order_by_idempotency_key("idem_123")
        self.assertIsNotNone(saved_order)
        self.assertEqual(saved_order.status, OrderStatus.SUBMITTED)

    def test_submit_new_order_idempotency(self):
        # First submission
        draft_order1 = self._create_draft_order("idem_same")
        broker_order1 = draft_order1.model_copy()
        broker_order1.status = OrderStatus.SUBMITTED
        self.mock_broker.submit_order.return_value = broker_order1
        
        first_result = self.manager.submit_new_order(draft_order1)
        self.assertEqual(first_result.status, OrderStatus.SUBMITTED)
        self.assertEqual(self.mock_broker.submit_order.call_count, 1)

        # Second submission with same idempotency key
        draft_order2 = self._create_draft_order("idem_same")
        second_result = self.manager.submit_new_order(draft_order2)

        # Assertions
        # Should return the already submitted order without calling broker again
        self.assertEqual(second_result.status, OrderStatus.SUBMITTED)
        self.assertEqual(self.mock_broker.submit_order.call_count, 1)

    def test_submit_new_order_broker_exception(self):
        draft_order = self._create_draft_order()
        
        self.mock_broker.submit_order.side_effect = Exception("Connection Refused")

        result = self.manager.submit_new_order(draft_order)

        self.assertEqual(result.status, OrderStatus.REJECTED)
        self.assertIn("Connection Refused", result.error_message)
        
        saved_order = self.store.get_order(draft_order.id)
        self.assertEqual(saved_order.status, OrderStatus.REJECTED)

    def test_sync_order_status_update(self):
        # Setup an order in SUBMITTED state locally
        local_order = self._create_draft_order()
        local_order.status = OrderStatus.SUBMITTED
        local_order.id = "broker_id_sync"
        self.store.save_order(local_order)

        # Mock broker returning FILLED state
        broker_order = local_order.model_copy()
        broker_order.status = OrderStatus.FILLED
        broker_order.filled_at = datetime.utcnow()
        self.mock_broker.get_order.return_value = broker_order

        # Execute sync
        result = self.manager.sync_order_status("broker_id_sync")

        self.assertEqual(result.status, OrderStatus.FILLED)
        self.assertIsNotNone(result.filled_at)
        
        # Verify store updated
        saved_order = self.store.get_order("broker_id_sync")
        self.assertEqual(saved_order.status, OrderStatus.FILLED)

    def test_cancel_active_order(self):
        # Setup active order
        local_order = self._create_draft_order()
        local_order.status = OrderStatus.ACCEPTED
        local_order.id = "broker_id_cancel"
        self.store.save_order(local_order)

        # Mock broker cancellation
        cancelled_broker_order = local_order.model_copy()
        cancelled_broker_order.status = OrderStatus.CANCELLED
        cancelled_broker_order.cancelled_at = datetime.utcnow()
        self.mock_broker.cancel_order.return_value = cancelled_broker_order

        result = self.manager.cancel_active_order("broker_id_cancel")

        self.assertEqual(result.status, OrderStatus.CANCELLED)
        self.assertIsNotNone(result.cancelled_at)
        
        saved_order = self.store.get_order("broker_id_cancel")
        self.assertEqual(saved_order.status, OrderStatus.CANCELLED)

if __name__ == '__main__':
    unittest.main()
