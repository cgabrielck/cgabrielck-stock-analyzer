import logging
from typing import List, Tuple
from dataclasses import dataclass

from backend.trading.broker import BrokerAdapter
from backend.trading.models import AccountSummary, Position

logger = logging.getLogger(__name__)

@dataclass
class ReconciliationReport:
    is_match: bool
    discrepancies: List[str]

class ReconciliationService:
    def __init__(self, broker: BrokerAdapter):
        self.broker = broker

    def reconcile_positions(self, local_positions: List[Position]) -> ReconciliationReport:
        """
        Compares local position records against the broker's truth.
        """
        discrepancies = []
        
        try:
            broker_summary = self.broker.get_account_summary()
            broker_positions = {p.symbol: p for p in broker_summary.positions}
        except Exception as e:
            return ReconciliationReport(
                is_match=False, 
                discrepancies=[f"Failed to fetch broker positions: {str(e)}"]
            )

        local_positions_map = {p.symbol: p for p in local_positions}

        # Check for positions that exist locally but not at broker
        for symbol, local_pos in local_positions_map.items():
            if symbol not in broker_positions:
                discrepancies.append(f"Position mismatch: {symbol} exists locally (qty: {local_pos.quantity}) but not at broker.")
            else:
                broker_pos = broker_positions[symbol]
                if abs(local_pos.quantity - broker_pos.quantity) > 0.001: # Handle float precision
                     discrepancies.append(f"Quantity mismatch for {symbol}: Local={local_pos.quantity}, Broker={broker_pos.quantity}")

        # Check for positions that exist at broker but not locally
        for symbol, broker_pos in broker_positions.items():
             if symbol not in local_positions_map:
                  discrepancies.append(f"Position mismatch: {symbol} exists at broker (qty: {broker_pos.quantity}) but not locally.")

        return ReconciliationReport(
            is_match=len(discrepancies) == 0,
            discrepancies=discrepancies
        )
