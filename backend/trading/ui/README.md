# Trading UI Module

Lab / legacy Streamlit dashboard for the auto-trading engine (not the product Desk). Product UI is FastAPI + `web/static` (ADR-003). This module provides interfaces for:
1. Viewing account summary (cash, buying power, positions).
2. Simulating orders via the Shadow Trading engine.
3. Reviewing the **L2** `OrderStore` ledger (`backend/trading/storage.py`). This is **not** ADR-003 IntentStore / Intent v0.
4. Running manual reconciliation against the broker.

It is currently integrated into the main `backend/app.py` via the sidebar.
