import unittest
from datetime import datetime, timedelta, timezone
from backend.research.comps import CompsAnalyzer
from backend.research.thesis import InvestmentThesis, Catalyst

class TestResearchWorkflows(unittest.TestCase):
    
    def test_comps_analyzer(self):
        target_metrics = {"EPS": 5.0, "EBITDA": 1000}
        analyzer = CompsAnalyzer("AAPL", target_metrics)
        
        peer_data = [
            {"symbol": "MSFT", "PE": 30.0, "EV_EBITDA": 20.0},
            {"symbol": "GOOG", "PE": 25.0, "EV_EBITDA": 15.0},
            {"symbol": "META", "PE": 20.0, "EV_EBITDA": 10.0}
        ]
        
        analyzer.load_peers(peer_data)
        
        stats = analyzer.calculate_peer_multiples()
        self.assertEqual(stats["PE"]["median"], 25.0)
        self.assertEqual(stats["EV_EBITDA"]["mean"], 15.0)
        
        # Implied Price = Median PE (25.0) * Target EPS (5.0) = 125.0
        implied_price = analyzer.calculate_implied_valuation("PE", "EPS", use_median=True)
        self.assertEqual(implied_price, 125.0)
        
        # Implied EV = Mean EV_EBITDA (15.0) * Target EBITDA (1000) = 15000
        implied_ev = analyzer.calculate_implied_valuation("EV_EBITDA", "EBITDA", use_median=False)
        self.assertEqual(implied_ev, 15000.0)

    def test_thesis_tracker(self):
        now = datetime.now(timezone.utc)
        
        thesis = InvestmentThesis(
            symbol="NVDA",
            core_thesis="AI chip dominance",
            bull_case="Sustained 50% YoY growth",
            bear_case="Competition catches up by 2026",
            review_date=now + timedelta(days=90)
        )
        
        thesis.add_evidence("Q3 Earnings beat by 20%", supports=True)
        thesis.add_evidence("New competitor chip announced", supports=False)
        
        self.assertEqual(len(thesis.supporting_evidence), 1)
        self.assertEqual(len(thesis.disconfirming_evidence), 1)
        
        # Test blackout period
        earnings_date = now + timedelta(days=5)
        cat = Catalyst(
            id="cat1", date=earnings_date, event_type="Earnings", 
            description="Q4 Release", impact_direction="Uncertain",
            blackout_period_days=2
        )
        thesis.catalysts.append(cat)
        
        # Current date (now) is 5 days away from event, blackout is 2 days. Should be False.
        self.assertFalse(thesis.is_in_blackout_period(now))
        
        # Simulate being 1 day before earnings
        one_day_before = earnings_date - timedelta(days=1)
        self.assertTrue(thesis.is_in_blackout_period(one_day_before))

if __name__ == '__main__':
    unittest.main()
