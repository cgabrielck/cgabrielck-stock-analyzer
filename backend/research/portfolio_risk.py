import pandas as pd
from typing import Dict, List, Optional
import numpy as np

class PortfolioRiskAnalyzer:
    """
    Analyzes portfolio risk, including correlations, concentration, and downside metrics.
    """
    
    def __init__(self, price_history: pd.DataFrame, positions: Dict[str, float]):
        """
        Args:
            price_history: DataFrame where columns are symbols and index is dates, values are daily closing prices.
            positions: Dictionary mapping symbol to position weight (0.0 to 1.0).
        """
        self.price_history = price_history
        self.returns = price_history.pct_change().dropna()
        self.positions = positions

    def calculate_correlation_matrix(self) -> pd.DataFrame:
        """Calculates the correlation matrix of daily returns."""
        if self.returns.empty:
            return pd.DataFrame()
        return self.returns.corr()

    def identify_correlation_clusters(self, threshold: float = 0.80) -> List[tuple]:
        """
        Identifies pairs of assets with correlation above the threshold.
        """
        corr_matrix = self.calculate_correlation_matrix()
        if corr_matrix.empty:
            return []
            
        high_corr_pairs = []
        symbols = corr_matrix.columns
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                sym1 = symbols[i]
                sym2 = symbols[j]
                # Only check if both are in positions to save time/noise, or check all if needed.
                # Assuming we want to check all available in price_history.
                corr = corr_matrix.iloc[i, j]
                if abs(corr) >= threshold:
                    high_corr_pairs.append((sym1, sym2, corr))
        return high_corr_pairs

    def calculate_portfolio_volatility(self) -> float:
        """
        Calculates annualized portfolio volatility.
        """
        if not self.positions or self.returns.empty:
            return 0.0
            
        symbols = list(self.positions.keys())
        weights = np.array([self.positions[sym] for sym in symbols])
        
        # Ensure returns data exists for all symbols
        available_symbols = [s for s in symbols if s in self.returns.columns]
        if len(available_symbols) != len(symbols):
            # Recalculate weights for available symbols only
            weights = np.array([self.positions[sym] for sym in available_symbols])
            if sum(weights) > 0:
                weights = weights / sum(weights) # re-normalize
            else:
                 return 0.0
                 
        cov_matrix = self.returns[available_symbols].cov() * 252 # Annualize
        port_variance = np.dot(weights.T, np.dot(cov_matrix, weights))
        return np.sqrt(port_variance)

    def calculate_value_at_risk(self, confidence_level: float = 0.95) -> float:
        """
        Calculates historical Value at Risk (VaR) for the portfolio.
        Returns the percentage loss.
        """
        if not self.positions or self.returns.empty:
            return 0.0

        symbols = list(self.positions.keys())
        # Filter for available symbols
        available_symbols = [s for s in symbols if s in self.returns.columns]
        if not available_symbols:
            return 0.0
            
        weights = np.array([self.positions[s] for s in available_symbols])
        if sum(weights) > 0:
             weights = weights / sum(weights)
             
        # Calculate historical daily portfolio returns
        port_returns = self.returns[available_symbols].dot(weights)
        
        # Historical VaR
        var = np.percentile(port_returns, 100 * (1 - confidence_level))
        return abs(var) # Return as a positive loss percentage

    def calculate_maximum_drawdown(self) -> float:
        """
        Calculates the maximum drawdown of the portfolio historically.
        """
        if not self.positions or self.returns.empty:
            return 0.0
            
        symbols = list(self.positions.keys())
        available_symbols = [s for s in symbols if s in self.returns.columns]
        if not available_symbols:
            return 0.0
            
        weights = np.array([self.positions[s] for s in available_symbols])
        if sum(weights) > 0:
             weights = weights / sum(weights)
             
        port_returns = self.returns[available_symbols].dot(weights)
        cumulative_returns = (1 + port_returns).cumprod()
        peak = cumulative_returns.cummax()
        drawdown = (cumulative_returns - peak) / peak
        return abs(drawdown.min())

    def analyze_concentration(self, sector_mapping: Optional[Dict[str, str]] = None) -> Dict:
        """
        Analyzes concentration risk (single position and sector).
        """
        results = {
            "max_single_position_weight": max(self.positions.values()) if self.positions else 0.0,
            "sector_exposure": {}
        }
        
        if sector_mapping:
            for sym, weight in self.positions.items():
                sector = sector_mapping.get(sym, "Unknown")
                results["sector_exposure"][sector] = results["sector_exposure"].get(sector, 0.0) + weight
                
        return results
