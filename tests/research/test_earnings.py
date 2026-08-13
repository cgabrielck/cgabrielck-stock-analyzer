import unittest
from datetime import datetime
from backend.research.earnings import EarningsWorkflow

class TestEarningsWorkflow(unittest.TestCase):
    
    def setUp(self):
        self.workflow = EarningsWorkflow()

    def test_earnings_preview(self):
        dt = datetime(2023, 10, 25)
        preview = self.workflow.build_earnings_preview("AAPL", dt, 1.20, 80000000000)
        
        self.assertEqual(preview["symbol"], "AAPL")
        self.assertEqual(preview["consensus_estimates"]["EPS"], 1.20)
        self.assertEqual(preview["status"], "pending_report")

    def test_post_earnings_beat(self):
        dt = datetime(2023, 10, 25)
        preview = self.workflow.build_earnings_preview("AAPL", dt, 1.20, 80000000000)
        
        review = self.workflow.process_post_earnings(preview, actual_eps=1.30, actual_rev=82000000000, guidance_update="Raised Q4")
        
        self.assertTrue(review["analysis"]["eps_beat"])
        self.assertTrue(review["analysis"]["rev_beat"])
        self.assertGreater(review["analysis"]["eps_surprise_pct"], 0)
        self.assertEqual(review["analysis"]["guidance_sentiment"], "Raised Q4")

    def test_post_earnings_miss(self):
        dt = datetime(2023, 10, 25)
        preview = self.workflow.build_earnings_preview("TSLA", dt, 0.80, 25000000000)
        
        review = self.workflow.process_post_earnings(preview, actual_eps=0.75, actual_rev=24000000000)
        
        self.assertFalse(review["analysis"]["eps_beat"])
        self.assertFalse(review["analysis"]["rev_beat"])
        self.assertLess(review["analysis"]["eps_surprise_pct"], 0)
        self.assertEqual(review["analysis"]["guidance_sentiment"], "Unknown")

if __name__ == '__main__':
    unittest.main()
