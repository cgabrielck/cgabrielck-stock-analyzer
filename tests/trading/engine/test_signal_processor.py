import unittest
from unittest.mock import MagicMock, patch
from backend.trading.models import OrderSide, OrderStatus
from backend.trading.risk.gates import RiskDecision
from backend.trading.engine.signal_processor import JournalWriteError, SignalProcessor

class TestSignalProcessor(unittest.TestCase):
    def setUp(self):
        self.mock_broker = MagicMock()
        self.mock_risk_engine = MagicMock()
        self.mock_order_manager = MagicMock()
        self.processor = SignalProcessor(
            broker=self.mock_broker, 
            risk_engine=self.mock_risk_engine, 
            order_manager=self.mock_order_manager
        )
        self._audit_patch = patch("backend.trading.safety.audit.log_risk_decision", return_value="aud-mock")
        self._journal_patch = patch(
            "backend.utils.provenance.journal_store.record_from_order",
            return_value={"log_id": "tl-mock"},
        )
        self.audit_mock = self._audit_patch.start()
        self.journal_mock = self._journal_patch.start()
        self.addCleanup(self._audit_patch.stop)
        self.addCleanup(self._journal_patch.stop)

    def test_process_signal_risk_rejected(self):
        # Setup Risk to reject
        self.mock_risk_engine.evaluate_order.return_value = RiskDecision(approved=False, reason="Too large")
        
        signal = {
            'symbol': 'AAPL',
            'side': 'buy',
            'quantity': 1000,
            'limit_price': 150.0
        }
        
        result_order = self.processor.process_signal(signal)
        
        self.assertEqual(result_order.status, OrderStatus.REJECTED)
        self.assertIn("Too large", result_order.error_message)
        # Should NOT reach order manager for submission
        self.mock_order_manager.submit_new_order.assert_not_called()
        # But it should be saved
        self.mock_order_manager.store.save_order.assert_called_once()

    def test_process_signal_risk_rejected_writes_journal(self):
        self.mock_risk_engine.evaluate_order.return_value = RiskDecision(
            approved=False, reason="Order notional $50,000 exceeds limit $10,000."
        )
        result = self.processor.process_signal({
            "symbol": "NVDA",
            "side": "buy",
            "quantity": 100,
            "limit_price": 500.0,
            "strategy_id": "stable",
        })
        self.assertEqual(result.status, OrderStatus.REJECTED)
        self.audit_mock.assert_called()
        self.journal_mock.assert_called()
        kwargs = self.journal_mock.call_args.kwargs
        self.assertEqual(kwargs.get("audit_id"), "aud-mock")
        self.assertEqual(kwargs.get("strategy_id"), "stable")

    def test_process_signal_risk_rejected_records_intent_id(self):
        self.mock_risk_engine.evaluate_order.return_value = RiskDecision(
            approved=False, reason="Too large"
        )
        self.processor.process_signal({
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 10,
            "limit_price": 150.0,
            "intent_id": "int-rej",
            "strategy_id": "stable",
        })
        kwargs = self.journal_mock.call_args.kwargs
        self.assertEqual(kwargs.get("intent_id"), "int-rej")

    def test_process_signal_risk_approved(self):
        # Setup Risk to approve
        self.mock_risk_engine.evaluate_order.return_value = RiskDecision(approved=True)
        
        # Mock order manager return
        mock_submitted = MagicMock()
        mock_submitted.status = OrderStatus.SUBMITTED
        self.mock_order_manager.submit_new_order.return_value = mock_submitted

        signal = {
            'symbol': 'MSFT',
            'side': 'sell',
            'quantity': 10,
            'limit_price': 300.0,
            'idempotency_key': 'sig_1'
        }
        
        result_order = self.processor.process_signal(signal)
        
        self.assertEqual(result_order.status, OrderStatus.SUBMITTED)
        self.mock_order_manager.submit_new_order.assert_called_once()
        passed = self.mock_order_manager.submit_new_order.call_args[0][0]
        self.assertEqual(passed.status, OrderStatus.RISK_APPROVED)

    def test_process_signal_risk_approved_records_intent_id(self):
        self.mock_risk_engine.evaluate_order.return_value = RiskDecision(approved=True)
        submitted = MagicMock()
        submitted.status = OrderStatus.SUBMITTED
        submitted.symbol = "MSFT"
        self.mock_order_manager.submit_new_order.return_value = submitted

        result = self.processor.process_signal({
            "symbol": "MSFT",
            "side": "sell",
            "quantity": 10,
            "limit_price": 300.0,
            "idempotency_key": "sig_ok",
            "intent_id": "int-ok",
            "strategy_id": "stable",
        })
        self.assertEqual(result.status, OrderStatus.SUBMITTED)
        self.journal_mock.assert_called()
        args, kwargs = self.journal_mock.call_args
        self.assertIs(args[0], submitted)
        self.assertEqual(kwargs.get("intent_id"), "int-ok")
        self.assertEqual(kwargs.get("strategy_id"), "stable")

    def test_process_signal_journal_raise_after_submit_is_not_happy_submitted(self):
        """Desk honesty: journal raise after allow+submit is not silent SUBMITTED."""
        self.mock_risk_engine.evaluate_order.return_value = RiskDecision(approved=True)
        submitted = MagicMock()
        submitted.status = OrderStatus.SUBMITTED
        submitted.symbol = "MSFT"
        submitted.id = "ord-jfail"
        self.mock_order_manager.submit_new_order.return_value = submitted
        self.journal_mock.side_effect = RuntimeError("disk full")

        with self.assertRaises(JournalWriteError) as ctx:
            result = self.processor.process_signal({
                "symbol": "MSFT",
                "side": "buy",
                "quantity": 10,
                "limit_price": 300.0,
                "idempotency_key": "sig_jfail",
                "intent_id": "int-jfail",
                "strategy_id": "stable",
            })
            self.fail(f"process_signal returned {result.status} after journal raise")

        self.assertIn("journal_write_failed", str(ctx.exception))
        self.assertIn("disk full", str(ctx.exception))
        self.assertIs(ctx.exception.order, submitted)
        self.mock_order_manager.submit_new_order.assert_called_once()

    def test_signal_processor_does_not_import_backend_api(self):
        from pathlib import Path

        src = Path("backend/trading/engine/signal_processor.py").read_text(encoding="utf-8")
        self.assertNotIn("from backend.api", src)
        self.assertNotIn("import backend.api", src)
        self.assertIn("backend.utils.provenance.journal_store", src)

if __name__ == '__main__':
    unittest.main()
