import unittest
import pandas as pd
from backend.research.dcf import DCFModel

class TestDCFModel(unittest.TestCase):
    def setUp(self):
        # 5 years of projected FCFs
        fcf = pd.Series([100, 110, 121, 133, 146])
        self.dcf = DCFModel(
            free_cash_flows=fcf,
            shares_outstanding=10,
            net_debt=50,
            wacc=0.10,
            terminal_growth_rate=0.02
        )

    def test_calculate_enterprise_value(self):
        # Manual check logic approximation:
        # PV of FCFs: 100/1.1 + 110/1.21 + 121/1.331 + 133/1.464 + 146/1.61 = ~463
        # TV: (146 * 1.02) / (0.10 - 0.02) = 148.92 / 0.08 = 1861.5
        # PV of TV: 1861.5 / (1.1^5) = 1861.5 / 1.61 = ~1156
        # Total EV approx: 463 + 1156 = 1619
        ev = self.dcf.calculate_enterprise_value(wacc=0.10, tgr=0.02)
        self.assertAlmostEqual(ev, 1610.07, places=1)

    def test_get_implied_share_price(self):
        ev = 1619.64
        # Equity = EV - NetDebt = 1619.64 - 50 = 1569.64
        # Price = Equity / Shares = 1569.64 / 10 = 156.96
        price = self.dcf.get_implied_share_price(ev)
        self.assertAlmostEqual(price, 156.96, places=1)

    def test_scenario_analysis(self):
        results = self.dcf.run_scenario_analysis()
        
        self.assertIn("Bull", results)
        self.assertIn("Base", results)
        self.assertIn("Bear", results)
        
        # Bull should have highest price
        self.assertGreater(results["Bull"]["Implied Share Price"], results["Base"]["Implied Share Price"])
        # Base should be greater than Bear
        self.assertGreater(results["Base"]["Implied Share Price"], results["Bear"]["Implied Share Price"])

    def test_sensitivity_analysis(self):
        df = self.dcf.run_sensitivity_analysis()
        
        # 5x5 matrix
        self.assertEqual(df.shape, (5, 5))
        
        # Check that higher WACC leads to lower price (first column top vs bottom)
        top_left_price = df.iloc[0, 0] # Lowest WACC, Lowest TGR
        bottom_left_price = df.iloc[-1, 0] # Highest WACC, Lowest TGR
        self.assertGreater(top_left_price, bottom_left_price)

if __name__ == '__main__':
    unittest.main()
