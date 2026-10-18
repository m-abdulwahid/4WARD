"""Observed TTC delay per route, from the GTFS-RT polls the collector stores (ttc_route_snapshot)."""
from __future__ import annotations

import time

from .config import REALTIME_MAX_AGE_S
from .db import store

_cache: dict[str, tuple[float, tuple[float, float] | None]] = {}
TTL_S = 30


def route_delay_s(route_id: str) -> float | None:
    """Average delay in seconds on a TTC route right now (negative = early), or None if the newest poll is missing or older
    than REALTIME_MAX_AGE_S. GO and the local bus agencies have no realtime feed here, so they always return None."""
    if route_id.startswith(("GO_", "YRT_", "DRT_", "MIWAY_")):
        return None
    hit = _cache.get(route_id)
    if hit and time.time() - hit[0] < TTL_S:
        got = hit[1]
    else:
        try:
            got = store.latest_route_delay(route_id, REALTIME_MAX_AGE_S)
        except Exception:
            got = None
        _cache[route_id] = (time.time(), got)
    return got[0] if got else None
