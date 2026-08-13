import unittest
from unittest.mock import MagicMock
from backend.trading.models import Position, AccountSummary
from backend.trading.reconciliation.service import ReconciliationService

class TestReconciliationService(unittest.TestCase):
    def setUp(self):
        self.mock_broker = MagicMock()
        self.reconciler = ReconciliationService(broker=self.mock_broker)

    def test_reconcile_match(self):
        local_positions = [
            Position(id="1", symbol="AAPL", quantity=100.0, average_entry_price=150.0),
            Position(id="2", symbol="MSFT", quantity=50.0, average_entry_price=300.0)
        ]
        
        mock_summary = AccountSummary(
            cash=10000, buying_power=20000, portfolio_value=50000,
            positions=[
                Position(id="1", symbol="AAPL", quantity=100.0, average_entry_price=151.0), # Entry price doesn't matter for qty sync
                Position(id="2", symbol="MSFT", quantity=50.0, average_entry_price=299.0)
            ]
        )
        self.mock_broker.get_account_summary.return_value = mock_summary

        report = self.reconciler.reconcile_positions(local_positions)
        
        self.assertTrue(report.is_match)
        self.assertEqual(len(report.discrepancies), 0)

    def test_reconcile_quantity_mismatch(self):
        local_positions = [
            Position(id="1", symbol="AAPL", quantity=100.0, average_entry_price=150.0)
        ]
        
        mock_summary = AccountSummary(
            cash=10000, buying_power=20000, portfolio_value=15000,
            positions=[
                Position(id="1", symbol="AAPL", quantity=90.0, average_entry_price=150.0)
            ]
        )
        self.mock_broker.get_account_summary.return_value = mock_summary

        report = self.reconciler.reconcile_positions(local_positions)
        
        self.assertFalse(report.is_match)
        self.assertEqual(len(report.discrepancies), 1)
        self.assertIn("Quantity mismatch for AAPL", report.discrepancies[0])

    def test_reconcile_missing_local(self):
        local_positions = []
        
        mock_summary = AccountSummary(
            cash=10000, buying_power=20000, portfolio_value=15000,
            positions=[
                Position(id="1", symbol="TSLA", quantity=10.0, average_entry_price=200.0)
            ]
        )
        self.mock_broker.get_account_summary.return_value = mock_summary

        report = self.reconciler.reconcile_positions(local_positions)
        
        self.assertFalse(report.is_match)
        self.assertEqual(len(report.discrepancies), 1)
        self.assertIn("exists at broker", report.discrepancies[0])

    def test_reconcile_missing_broker(self):
        local_positions = [
            Position(id="1", symbol="GOOG", quantity=5.0, average_entry_price=100.0)
        ]
        
        mock_summary = AccountSummary(cash=10000, buying_power=20000, portfolio_value=0, positions=[])
        self.mock_broker.get_account_summary.return_value = mock_summary

        report = self.reconciler.reconcile_positions(local_positions)
        
        self.assertFalse(report.is_match)
        self.assertEqual(len(report.discrepancies), 1)
        self.assertIn("exists locally", report.discrepancies[0])

if __name__ == '__main__':
    unittest.main()
