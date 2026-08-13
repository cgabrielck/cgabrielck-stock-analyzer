from typing import Dict, List, Optional
from datetime import datetime

class EarningsWorkflow:
    """
    Workflow for analyzing company earnings reports (preview and post-review).
    """

    def __init__(self):
        pass

    def build_earnings_preview(self, symbol: str, expected_date: datetime, consensus_eps: float, consensus_rev: float) -> Dict:
        """
        Structures the expectations before an earnings report.
        """
        return {
            "symbol": symbol,
            "type": "preview",
            "expected_report_date": expected_date.isoformat(),
            "consensus_estimates": {
                "EPS": consensus_eps,
                "Revenue": consensus_rev
            },
            "status": "pending_report"
        }

    def process_post_earnings(self, 
                              preview_data: Dict, 
                              actual_eps: float, 
                              actual_rev: float, 
                              guidance_update: Optional[str] = None) -> Dict:
        """
        Compares actual results against the preview expectations.
        """
        if preview_data["type"] != "preview":
            raise ValueError("Must provide a valid preview dictionary to run post-earnings analysis.")

        eps_beat = actual_eps > preview_data["consensus_estimates"]["EPS"]
        rev_beat = actual_rev > preview_data["consensus_estimates"]["Revenue"]
        
        eps_surprise_pct = ((actual_eps - preview_data["consensus_estimates"]["EPS"]) / abs(preview_data["consensus_estimates"]["EPS"])) * 100 if preview_data["consensus_estimates"]["EPS"] != 0 else 0

        return {
            "symbol": preview_data["symbol"],
            "type": "post_review",
            "report_date": preview_data["expected_report_date"], # Assume reported on expected date for simple model
            "actuals": {
                "EPS": actual_eps,
                "Revenue": actual_rev
            },
            "analysis": {
                "eps_beat": eps_beat,
                "rev_beat": rev_beat,
                "eps_surprise_pct": eps_surprise_pct,
                "guidance_sentiment": guidance_update or "Unknown"
            }
        }
