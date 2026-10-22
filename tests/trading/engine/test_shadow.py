import unittest
from unittest.mock import MagicMock
from datetime import datetime
from backend.trading.models import Order, OrderSide, OrderType, OrderStatus
from backend.trading.engine.shadow import ShadowTradingEngine
from backend.trading.engine.order_manager import OrderManager

class TestShadowTradingEngine(unittest.TestCase):
    def setUp(self):
        self.mock_store = MagicMock()
        self.mock_broker = MagicMock()
        self.order_manager = OrderManager(broker=self.mock_broker, store=self.mock_store)
        self.shadow_engine = ShadowTradingEngine(self.order_manager)

    def test_simulate_submission_limit_order(self):
        order = Order(
            id="test_1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10,
            limit_price=150.0,
            idempotency_key="key_1",
            status=OrderStatus.RISK_APPROVED
        )
        
        simulated = self.shadow_engine.simulate_submission(order)
        
        # No bars → not a fill. Limit must not become filled_avg_price.
        self.assertNotEqual(simulated.status, OrderStatus.FILLED)
        self.assertIsNone(simulated.filled_avg_price)
        self.assertIsNotNone(simulated.submitted_at)
        self.assertEqual(self.mock_store.save_order.call_count, 2)
        self.mock_broker.submit_order.assert_not_called()

    def test_simulate_submission_wrong_state(self):
        order = Order(
            id="test_2",
            symbol="MSFT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=5,
            idempotency_key="key_2",
            status=OrderStatus.DRAFT  # Needs to be RISK_APPROVED
        )
        
        simulated = self.shadow_engine.simulate_submission(order)
        
        # State should not change
        self.assertEqual(simulated.status, OrderStatus.DRAFT)
        self.mock_store.save_order.assert_not_called()

if __name__ == '__main__':
    unittest.main()
