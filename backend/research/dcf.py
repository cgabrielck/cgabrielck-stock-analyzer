import pandas as pd
from typing import Dict, Optional
from datetime import datetime, timezone

class DCFModel:
    """
    Discounted Cash Flow (DCF) model supporting three scenarios (Bull, Base, Bear)
    and sensitivity analysis on WACC and Terminal Growth Rate.
    """
    
    def __init__(self, 
                 free_cash_flows: pd.Series, 
                 shares_outstanding: float, 
                 net_debt: float, 
                 wacc: float = 0.10, 
                 terminal_growth_rate: float = 0.02):
        """
        Args:
            free_cash_flows: Projected FCFs for the next N years (e.g., Year 1 to 5).
            shares_outstanding: Total number of shares.
            net_debt: Total Debt - Cash & Equivalents.
            wacc: Weighted Average Cost of Capital (Discount Rate).
            terminal_growth_rate: Perpetual growth rate after the projection period.
        """
        self.fcf = free_cash_flows
        self.shares = shares_outstanding
        self.net_debt = net_debt
        self.base_wacc = wacc
        self.base_tgr = terminal_growth_rate
        self.timestamp = datetime.now(timezone.utc)

    def calculate_enterprise_value(self, wacc: float, tgr: float, fcf_multiplier: float = 1.0) -> float:
        """
        Calculates the Enterprise Value based on given WACC, TGR, and a multiplier for FCFs (for scenarios).
        """
        if wacc <= tgr:
            # WACC must be greater than TGR for the terminal value formula to work
            return 0.0
            
        discounted_fcf = 0.0
        for year, cf in enumerate(self.fcf, start=1):
            discounted_fcf += (cf * fcf_multiplier) / ((1 + wacc) ** year)
            
        final_year_fcf = self.fcf.iloc[-1] * fcf_multiplier
        terminal_value = (final_year_fcf * (1 + tgr)) / (wacc - tgr)
        discounted_tv = terminal_value / ((1 + wacc) ** len(self.fcf))
        
        return discounted_fcf + discounted_tv

    def get_implied_share_price(self, enterprise_value: float) -> float:
        """Calculates equity value per share from Enterprise Value."""
        equity_value = enterprise_value - self.net_debt
        return max(equity_value / self.shares, 0.0) # Price cannot be negative

    def run_scenario_analysis(self) -> Dict[str, Dict]:
        """
        Runs Bull, Base, and Bear scenarios modifying the FCF projections and WACC/TGR slightly.
        """
        scenarios = {
            "Bear": {"fcf_mult": 0.8, "wacc_adj": 0.02, "tgr_adj": -0.01},
            "Base": {"fcf_mult": 1.0, "wacc_adj": 0.0, "tgr_adj": 0.0},
            "Bull": {"fcf_mult": 1.2, "wacc_adj": -0.01, "tgr_adj": 0.01}
        }
        
        results = {}
        for name, params in scenarios.items():
            ev = self.calculate_enterprise_value(
                wacc=self.base_wacc + params['wacc_adj'],
                tgr=self.base_tgr + params['tgr_adj'],
                fcf_multiplier=params['fcf_mult']
            )
            price = self.get_implied_share_price(ev)
            results[name] = {
                "Enterprise Value": ev,
                "Implied Share Price": price,
                "Assumed WACC": self.base_wacc + params['wacc_adj'],
                "Assumed TGR": self.base_tgr + params['tgr_adj']
            }
            
        return results

    def run_sensitivity_analysis(self) -> pd.DataFrame:
        """
        Generates a sensitivity table for the Base scenario varying WACC and TGR.
        """
        wacc_range = [self.base_wacc - 0.02, self.base_wacc - 0.01, self.base_wacc, self.base_wacc + 0.01, self.base_wacc + 0.02]
        tgr_range = [self.base_tgr - 0.01, self.base_tgr - 0.005, self.base_tgr, self.base_tgr + 0.005, self.base_tgr + 0.01]
        
        table = []
        for w in wacc_range:
            row = {}
            for t in tgr_range:
                ev = self.calculate_enterprise_value(wacc=w, tgr=t)
                price = self.get_implied_share_price(ev)
                row[f"{t*100:.1f}%"] = price
            table.append(row)
            
        df = pd.DataFrame(table, index=[f"{w*100:.1f}%" for w in wacc_range])
        df.index.name = "WACC \\ TGR"
        return df
