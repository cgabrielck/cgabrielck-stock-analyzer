"""SignalProcessor shadow vs paper routing."""
import unittest
from unittest.mock import MagicMock

from backend.trading.engine.signal_processor import SignalProcessor
from backend.trading.engine.shadow import ShadowTradingEngine
from backend.trading.models import OrderStatus
from backend.trading.risk.gates import RiskDecision
from backend.trading.engine.order_manager import InMemoryOrderStore, OrderManager


class TestSignalProcessorModes(unittest.TestCase):
    def test_shadow_mode_uses_shadow_engine_not_broker_submit(self):
        broker = MagicMock()
        broker.get_account_summary.return_value = MagicMock(
            cash=50_000, buying_power=50_000, portfolio_value=100_000, positions=[]
        )
        risk = MagicMock()
        risk.evaluate_order.return_value = RiskDecision(approved=True, reason="ok")
        store = InMemoryOrderStore()
        manager = OrderManager(broker=broker, store=store)
        shadow = ShadowTradingEngine(order_manager=manager)
        processor = SignalProcessor(
            broker, risk, manager, execution_mode="shadow", shadow_engine=shadow
        )

        result = processor.process_signal(
            {
                "symbol": "AAPL",
                "side": "buy",
                "quantity": 10,
                "limit_price": 100.0,
                "idempotency_key": "shadow-aapl-1",
            }
        )
        self.assertEqual(result.status, OrderStatus.FILLED)
        broker.submit_order.assert_not_called()
        self.assertIsNotNone(store.get_order(result.id))

    def test_paper_mode_submits_via_order_manager(self):
        broker = MagicMock()
        broker.get_account_summary.return_value = MagicMock(
            cash=50_000, buying_power=50_000, portfolio_value=100_000, positions=[]
        )

        def _submit(order):
            order.status = OrderStatus.SUBMITTED
            return order

        broker.submit_order.side_effect = _submit
        risk = MagicMock()
        risk.evaluate_order.return_value = RiskDecision(approved=True, reason="ok")
        store = InMemoryOrderStore()
        manager = OrderManager(broker=broker, store=store)
        processor = SignalProcessor(broker, risk, manager, execution_mode="paper")

        result = processor.process_signal(
            {
                "symbol": "MSFT",
                "side": "buy",
                "quantity": 5,
                "limit_price": 200.0,
                "idempotency_key": "paper-msft-1",
                "order_class": "bracket",
                "take_profit_price": 220.0,
                "stop_loss_price": 180.0,
            }
        )
        self.assertEqual(result.status, OrderStatus.SUBMITTED)
        broker.submit_order.assert_called_once()
        submitted = broker.submit_order.call_args[0][0]
        self.assertEqual(submitted.order_class, "bracket")
        self.assertEqual(submitted.take_profit_price, 220.0)


if __name__ == "__main__":
    unittest.main()
