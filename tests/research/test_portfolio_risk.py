import unittest
import pandas as pd
import numpy as np
from backend.research.portfolio_risk import PortfolioRiskAnalyzer

class TestPortfolioRiskAnalyzer(unittest.TestCase):
    def setUp(self):
        # Create dummy price history
        dates = pd.date_range('2023-01-01', periods=100)
        np.random.seed(42)
        
        # Create two highly correlated assets (A and B) and one independent (C)
        returns_A = np.random.normal(0.001, 0.01, 100)
        returns_B = returns_A * 0.9 + np.random.normal(0, 0.002, 100)
        returns_C = np.random.normal(0.0005, 0.015, 100)
        
        prices_A = 100 * np.exp(np.cumsum(returns_A))
        prices_B = 50 * np.exp(np.cumsum(returns_B))
        prices_C = 200 * np.exp(np.cumsum(returns_C))
        
        self.price_history = pd.DataFrame({
            'A': prices_A,
            'B': prices_B,
            'C': prices_C
        }, index=dates)
        
        self.positions = {'A': 0.4, 'B': 0.4, 'C': 0.2}
        self.analyzer = PortfolioRiskAnalyzer(self.price_history, self.positions)

    def test_correlation_clusters(self):
        clusters = self.analyzer.identify_correlation_clusters(threshold=0.8)
        # Should identify A and B
        self.assertEqual(len(clusters), 1)
        sym1, sym2, corr = clusters[0]
        self.assertIn(sym1, ['A', 'B'])
        self.assertIn(sym2, ['A', 'B'])
        self.assertGreater(corr, 0.8)

    def test_portfolio_volatility(self):
        vol = self.analyzer.calculate_portfolio_volatility()
        self.assertGreater(vol, 0.0)
        self.assertLess(vol, 1.0) # Should be a reasonable annualized number

    def test_value_at_risk(self):
        var = self.analyzer.calculate_value_at_risk(0.95)
        self.assertGreater(var, 0.0)
        self.assertLess(var, 0.1) # 95% daily VaR shouldn't be > 10% in this mock data

    def test_maximum_drawdown(self):
        mdd = self.analyzer.calculate_maximum_drawdown()
        self.assertGreaterEqual(mdd, 0.0)
        self.assertLess(mdd, 1.0)

    def test_analyze_concentration(self):
        sector_mapping = {'A': 'Tech', 'B': 'Tech', 'C': 'Finance'}
        analysis = self.analyzer.analyze_concentration(sector_mapping)
        
        self.assertEqual(analysis['max_single_position_weight'], 0.4)
        self.assertEqual(analysis['sector_exposure']['Tech'], 0.8)
        self.assertEqual(analysis['sector_exposure']['Finance'], 0.2)

if __name__ == '__main__':
    unittest.main()
