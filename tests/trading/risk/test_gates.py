import unittest
from backend.trading.models import Order, OrderSide, OrderType, AccountSummary, Position
from backend.trading.risk.gates import RiskEngine, RiskLimits, RiskDecision

class TestRiskEngine(unittest.TestCase):
    def setUp(self):
        self.limits = RiskLimits(
            max_order_notional=10000.0,
            max_position_concentration=0.25,
            max_total_exposure=0.90
        )
        self.engine = RiskEngine(self.limits)
        
        self.base_account = AccountSummary(
            cash=50000.0,
            buying_power=50000.0,
            portfolio_value=100000.0,
            positions=[
                Position(id="1", symbol="AAPL", quantity=100, average_entry_price=150.0) # $15,000
            ]
        )

    def _create_order(self, symbol="MSFT", qty=10, limit=200.0):
        return Order(
            id="test_1",
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=qty,
            limit_price=limit,
            idempotency_key="key1"
        )

    def test_approve_valid_order(self):
        order = self._create_order(qty=10, limit=200.0) # $2,000 notional
        decision = self.engine.evaluate_order(order, self.base_account)
        self.assertTrue(decision.approved)

    def test_reject_market_order_no_price(self):
        order = self._create_order()
        order.limit_price = None
        decision = self.engine.evaluate_order(order, self.base_account)
        self.assertFalse(decision.approved)
        self.assertIn("reference price", decision.reason)

    def test_reject_max_order_notional(self):
        order = self._create_order(qty=100, limit=200.0) # $20,000 notional > $10,000 limit
        decision = self.engine.evaluate_order(order, self.base_account)
        self.assertFalse(decision.approved)
        self.assertIn("exceeds limit", decision.reason)

    def test_reject_insufficient_buying_power(self):
        account = self.base_account.model_copy()
        account.buying_power = 1000.0
        
        order = self._create_order(qty=10, limit=200.0) # $2,000 notional
        decision = self.engine.evaluate_order(order, account)
        self.assertFalse(decision.approved)
        self.assertIn("Insufficient buying power", decision.reason)

    def test_reject_position_concentration(self):
        # Already have $15k AAPL. Max is 25% of 100k = $25k.
        # Try to buy $11k more AAPL -> $26k total -> Reject.
        self.engine.limits.max_order_notional = 20000.0
        
        order = self._create_order(symbol="AAPL", qty=100, limit=110.0) # $11,000
        decision = self.engine.evaluate_order(order, self.base_account)
        
        self.assertFalse(decision.approved)
        self.assertIn("concentration", decision.reason)

    def test_reject_total_exposure(self):
        account = self.base_account.model_copy()
        # Max exposure is 90% of 100k = $90k; existing = $85k, new order $10k → $95k → reject
        account.positions = [
             Position(id="1", symbol="AAPL", quantity=500, average_entry_price=170.0) # $85,000
        ]
        
        order = self._create_order(qty=50, limit=200.0) # $10,000
        decision = self.engine.evaluate_order(order, account)
        
        self.assertFalse(decision.approved)
        self.assertIn("exposure", decision.reason)

if __name__ == '__main__':
    unittest.main()
