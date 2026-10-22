from .broker import BrokerAdapter
from .models import Order, OrderStatus, AccountSummary, Position
from .engine.order_manager import OrderManager, OrderStore, InMemoryOrderStore
from .engine.shadow import ShadowTradingEngine
from .engine.signal_processor import SignalProcessor
from .engine.worker import TradingWorker
from .storage import JSONOrderStore
from .alpaca_broker import AlpacaBroker
from .risk.gates import RiskEngine, RiskLimits, RiskDecision
from .reconciliation.service import ReconciliationService, ReconciliationReport

# Streamlit dashboard is lab/legacy — do not import it at package load
# (IntentStore / H1 consume must not require Streamlit).
def __getattr__(name):
    if name == "render_trading_dashboard":
        from .ui.dashboard import render_trading_dashboard

        return render_trading_dashboard
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "BrokerAdapter", "Order", "OrderStatus", "AccountSummary", "Position",
    "OrderManager", "OrderStore", "InMemoryOrderStore", "JSONOrderStore",
    "ShadowTradingEngine", "SignalProcessor", "TradingWorker", "AlpacaBroker",
    "RiskEngine", "RiskLimits", "RiskDecision", "ReconciliationService",
    "ReconciliationReport", "render_trading_dashboard",
]
