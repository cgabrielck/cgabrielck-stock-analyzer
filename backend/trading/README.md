# ALPHA//DESK Trading Engine

This module contains the core execution logic for the auto-trading upgrade. It is designed to be safe, auditable, and decoupled from the specific brokerage implementation.

## Structure

*   `models.py`: Pydantic data models for Orders, Positions, Account State, etc.
*   `broker.py`: The abstract `BrokerAdapter` interface.
*   `alpaca_broker.py`: The concrete implementation of the Alpaca REST API (defaulting to Paper Trading).
*   `storage.py`: Durable storage implementation (`JSONOrderStore`) for order intent ledgers.
*   `engine/`:
    *   `order_manager.py`: State machine for orders, handling idempotency and broker submission.
    *   `signal_processor.py`: The entry point for strategy signals. Evaluates risk and forwards to the Order Manager.
    *   `shadow.py`: A simulation engine to test execution logic without hitting the broker API.
    *   `worker.py`: Background thread for syncing states and running reconciliation.
*   `risk/`: Deterministic risk gates (order size, portfolio concentration, total exposure).
*   `reconciliation/`: Services to compare local ledgers against the broker's source of truth.
*   `ui/`: The Streamlit dashboard components for monitoring and manual intervention.

## Status

**Stage 2 tooling complete.** Strategy signals flow through `SignalProcessor` into
`ShadowTradingEngine` (default CLI `--mode shadow`) or Alpaca paper. Durable
ledgers default to SQLite via `create_order_store()`. Accumulate multi-month
shadow evidence and seal with `scripts/run_evaluation.py --seal` before Stage 3
(`docs/PAPER_VALIDATION_RUNBOOK.md`).
