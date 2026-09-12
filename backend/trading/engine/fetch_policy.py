"""Worker universe fetch policy — cap, batching, always include open holdings."""
from __future__ import annotations

import os
from typing import Iterable, List, Optional, Sequence


def parse_universe_cap(raw: Optional[str], universe_len: int) -> int:
    """Empty / 0 / all / full → entire universe. Otherwise clamp to 1..len."""
    text = str(raw or "").strip().lower()
    nlen = max(int(universe_len), 0)
    if text in ("", "0", "all", "full", "none"):
        return nlen
    try:
        n = int(text)
    except (TypeError, ValueError):
        return nlen
    if n <= 0:
        return nlen
    return max(1, min(n, nlen))


def parse_fetch_batch(raw: Optional[str] = None) -> int:
    text = str(raw if raw is not None else os.getenv("WORKER_FETCH_BATCH", "8"))
    try:
        return max(1, int(text.strip() or "8"))
    except (TypeError, ValueError):
        return 8


def parse_fetch_pause_sec(raw: Optional[str] = None) -> float:
    text = str(raw if raw is not None else os.getenv("WORKER_FETCH_PAUSE_SEC", "0.4"))
    try:
        return max(0.0, float(text.strip() or "0.4"))
    except (TypeError, ValueError):
        return 0.4


def env_universe_cap(universe_len: int) -> int:
    return parse_universe_cap(os.getenv("WORKER_UNIVERSE_CAP"), universe_len)


def symbols_to_fetch(
    universe: Sequence[str],
    held: Iterable[str],
    cap: int,
) -> List[str]:
    """Open positions first (exits must never miss data), then up to *cap* universe names."""
    seen = set()
    out: List[str] = []
    for ticker in held:
        symbol = str(ticker or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append(symbol)
    added = 0
    limit = max(int(cap), 0)
    for ticker in universe:
        symbol = str(ticker or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        if added >= limit:
            break
        seen.add(symbol)
        out.append(symbol)
        added += 1
    return out
