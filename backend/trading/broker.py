from abc import ABC, abstractmethod
from typing import List, Optional

from backend.trading.models import AccountSummary, Order, OrderStatus

class BrokerAdapter(ABC):
    @abstractmethod
    def get_account_summary(self) -> AccountSummary:
        """Retrieves the current account summary."""
        pass

    @abstractmethod
    def submit_order(self, order: Order) -> Order:
        """Submits an order to the broker."""
        pass

    @abstractmethod
    def get_order(self, order_id: str) -> Optional[Order]:
        """Retrieves the status of a specific order."""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> Order:
        """Cancels an existing order."""
        pass

    @abstractmethod
    def list_positions(self) -> List[dict]:
        """Lists all open positions."""
        pass
