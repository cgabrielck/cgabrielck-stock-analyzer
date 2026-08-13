from typing import Dict, List
import pandas as pd

class CompsAnalyzer:
    """
    Comparable-company valuation workflow.
    """
    def __init__(self, target_symbol: str, target_metrics: Dict[str, float]):
        """
        Args:
            target_symbol: The company being valued.
            target_metrics: Dictionary of key metrics (e.g., 'EPS', 'EBITDA', 'Revenue', 'BookValue').
        """
        self.target_symbol = target_symbol
        self.target_metrics = target_metrics
        self.peers: pd.DataFrame = pd.DataFrame()

    def load_peers(self, peer_data: List[Dict]):
        """
        Loads peer data.
        Args:
            peer_data: List of dicts, e.g. [{"symbol": "MSFT", "PE": 30.5, "EV_EBITDA": 20.1, "PS": 10.2}]
        """
        self.peers = pd.DataFrame(peer_data)
        if not self.peers.empty:
            self.peers.set_index('symbol', inplace=True)

    def calculate_peer_multiples(self) -> Dict[str, Dict[str, float]]:
        """
        Calculates median and mean for each multiple across peers.
        """
        if self.peers.empty:
            return {}
            
        stats = {}
        for column in self.peers.columns:
            stats[column] = {
                "median": self.peers[column].median(),
                "mean": self.peers[column].mean(),
                "min": self.peers[column].min(),
                "max": self.peers[column].max()
            }
        return stats

    def calculate_implied_valuation(self, multiple_name: str, metric_name: str, use_median: bool = True) -> float:
        """
        Calculates the implied value based on a peer multiple and the target's underlying metric.
        e.g., multiple_name='PE', metric_name='EPS' -> Implied Price = Peer PE * Target EPS
        """
        stats = self.calculate_peer_multiples()
        if multiple_name not in stats:
            raise ValueError(f"Multiple {multiple_name} not found in peer data.")
            
        if metric_name not in self.target_metrics:
             raise ValueError(f"Metric {metric_name} not found in target metrics.")
             
        target_metric_val = self.target_metrics[metric_name]
        multiple_val = stats[multiple_name]['median'] if use_median else stats[multiple_name]['mean']
        
        return target_metric_val * multiple_val
