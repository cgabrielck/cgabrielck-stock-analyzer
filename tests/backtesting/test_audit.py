import unittest
import pandas as pd
import numpy as np
from backend.backtesting.audit import BacktestAuditor

class TestBacktestAuditor(unittest.TestCase):
    def setUp(self):
        # Create dummy trade data
        self.trades = pd.DataFrame([
            {'symbol': 'AAPL', 'entry_date': '2023-01-01', 'exit_date': '2023-01-10', 'entry_price': 150, 'exit_price': 160, 'quantity': 10},
            {'symbol': 'MSFT', 'entry_date': '2023-01-05', 'exit_date': '2023-01-15', 'entry_price': 250, 'exit_price': 240, 'quantity': 5},
        ])
        
        # Create dummy returns
        dates = pd.date_range('2023-01-01', periods=252)
        # Random normal returns
        np.random.seed(42)
        self.daily_returns = pd.Series(np.random.normal(0.001, 0.01, 252), index=dates)
        # Benchmark closely correlated
        self.benchmark_returns = self.daily_returns * 0.8 + pd.Series(np.random.normal(0, 0.005, 252), index=dates)

        self.auditor = BacktestAuditor(self.trades, self.daily_returns, self.benchmark_returns)

    def test_estimate_trading_costs(self):
        # 10 AAPL @ 150 = 1500, exit @ 160 = 1600. Total = 3100
        # 5 MSFT @ 250 = 1250, exit @ 240 = 1200. Total = 2450
        # Total notional = 5550
        # 10 bps slippage = 5550 * 0.001 = 5.55
        # 2 trades * 2 sides * $1 = $4.00
        # Total expected = 9.55
        
        cost = self.auditor.estimate_trading_costs(assumed_slippage_bps=10.0, commission_per_trade=1.0)
        self.assertAlmostEqual(cost, 9.55, places=2)

    def test_benchmark_alignment(self):
        alignment = self.auditor.check_benchmark_alignment()
        
        self.assertIn('correlation', alignment)
        self.assertIn('beta', alignment)
        # Because we constructed benchmark to be correlated
        self.assertGreater(alignment['correlation'], 0.5)

    def test_evaluate_data_completeness(self):
        high = self.auditor.evaluate_data_completeness(0.02)
        self.assertIn("High Confidence", high)
        
        medium = self.auditor.evaluate_data_completeness(0.10)
        self.assertIn("Medium Confidence", medium)
        
        low = self.auditor.evaluate_data_completeness(0.20)
        self.assertIn("Low Confidence", low)

    def test_generate_audit_report(self):
        report = self.auditor.generate_audit_report(missing_data_ratio=0.01)
        
        self.assertIn('turnover_rate_estimate', report)
        self.assertIn('estimated_trading_costs', report)
        self.assertIn('benchmark_alignment', report)
        self.assertIn('confidence_level', report)
        self.assertIn('warnings', report)
        self.assertIn("High Confidence", report['confidence_level'])

if __name__ == '__main__':
    unittest.main()
