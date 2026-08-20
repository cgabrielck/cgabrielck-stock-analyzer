"""
Trading safety layer — kill switch, mandate gate, and audit trail.

Inspired by Vibe-Trading's institutional-grade safety design:
- Kill switch: immediate halt of all new opening positions
- Mandate gate: declarative authorization constraints
- Audit trail: immutable append-only decision log
"""
