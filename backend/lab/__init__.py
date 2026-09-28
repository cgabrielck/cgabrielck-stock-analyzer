"""Lab citation-try extras — optional, default OFF, never place orders.

Enable with env flags (see ``backend.lab.flags``). Missing packages degrade
gracefully to VADER / json.loads / CSV summaries.
"""
from __future__ import annotations

from backend.lab.flags import lab_status

__all__ = ["lab_status"]
