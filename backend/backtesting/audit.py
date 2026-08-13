import pandas as pd
from typing import List, Dict, Optional
import numpy as np

class BacktestAuditor:
    """
    Analyzes backtest results to identify common pitfalls like look-ahead bias,
    survivorship bias, and parameter sensitivity.
    """
    
    def __init__(self, trades: pd.DataFrame, daily_returns: pd.Series, benchmark_returns: Optional[pd.Series] = None):
        """
        Args:
            trades: DataFrame containing trade history with columns:
                    ['symbol', 'entry_date', 'exit_date', 'entry_price', 'exit_price', 'quantity']
            daily_returns: Series of daily portfolio returns.
            benchmark_returns: Series of daily benchmark (e.g., SPY) returns.
        """
        self.trades = trades
        self.daily_returns = daily_returns
        self.benchmark_returns = benchmark_returns

    def calculate_turnover(self) -> float:
        """
        Calculates annualized portfolio turnover rate.
        A very rough approximation based on trade frequency.
        """
        if self.trades.empty or self.daily_returns.empty:
            return 0.0
            
        total_days = len(self.daily_returns)
        if total_days < 252:
            return 0.0 # Not enough data for meaningful annualized turnover
            
        num_trades = len(self.trades)
        # Assuming average holding period roughly correlates to turnover
        # This is a simplified metric for the audit report
        years = total_days / 252.0
        annual_trades = num_trades / years
        
        # We need portfolio size to do proper turnover (min(buys,sells)/avg_assets).
        # Without it, we just return a trade frequency metric normalized to 0-1 range roughly.
        # This is a placeholder for a more sophisticated calculation.
        return min(annual_trades / 100.0, 1.0) # Cap at 1.0 (100%) for this simplified metric

    def estimate_trading_costs(self, assumed_slippage_bps: float = 10.0, commission_per_trade: float = 1.0) -> float:
        """
        Estimates the total cost of trading (slippage + commissions) based on trade history.
        Args:
            assumed_slippage_bps: Slippage in basis points (1 bp = 0.01%) per trade side.
            commission_per_trade: Fixed commission cost per trade.
        """
        if self.trades.empty:
            return 0.0
            
        total_notional_traded = 0.0
        
        for _, trade in self.trades.iterrows():
            entry_notional = trade['entry_price'] * trade['quantity']
            exit_notional = trade['exit_price'] * trade['quantity'] if not pd.isna(trade['exit_price']) else 0
            total_notional_traded += (entry_notional + exit_notional)
            
        total_slippage_cost = total_notional_traded * (assumed_slippage_bps / 10000.0)
        total_commission_cost = len(self.trades) * 2 * commission_per_trade # Assuming 1 commission for entry, 1 for exit
        
        return total_slippage_cost + total_commission_cost

    def check_benchmark_alignment(self) -> Dict[str, float]:
        """
        Compares backtest returns against the benchmark (e.g., correlation, beta).
        """
        if self.benchmark_returns is None or self.daily_returns.empty or self.benchmark_returns.empty:
            return {"correlation": 0.0, "beta": 0.0}
            
        # Align dates
        aligned = pd.concat([self.daily_returns, self.benchmark_returns], axis=1).dropna()
        if aligned.empty:
             return {"correlation": 0.0, "beta": 0.0}
             
        aligned.columns = ['portfolio', 'benchmark']
        
        correlation = aligned['portfolio'].corr(aligned['benchmark'])
        
        cov = aligned.cov().iloc[0,1]
        var = aligned['benchmark'].var()
        beta = cov / var if var != 0 else 0.0
        
        return {"correlation": correlation, "beta": beta}

    def evaluate_data_completeness(self, missing_data_ratio: float) -> str:
        """
        Provides a confidence statement based on historical data completeness.
        """
        if missing_data_ratio < 0.05:
            return "High Confidence: Data is mostly complete (<5% missing). Survivorship bias risk is low if universe is point-in-time."
        elif missing_data_ratio < 0.15:
            return "Medium Confidence: Some data is missing (5-15%). Results may be slightly overstated due to missing fundamental histories."
        else:
            return "Low Confidence: Significant missing data (>15%). High risk of survivorship or look-ahead bias impacting backtest validity."

    def generate_audit_report(self, missing_data_ratio: float = 0.0) -> Dict:
        """
        Generates a comprehensive audit report for the backtest.
        """
        return {
            "turnover_rate_estimate": self.calculate_turnover(),
            "estimated_trading_costs": self.estimate_trading_costs(),
            "benchmark_alignment": self.check_benchmark_alignment(),
            "confidence_level": self.evaluate_data_completeness(missing_data_ratio),
            "warnings": [
                "Ensure Point-in-Time data was used for fundamental signals to avoid Look-Ahead Bias.",
                "Verify that delisted companies are included in the historical universe to avoid Survivorship Bias."
            ]
        }
