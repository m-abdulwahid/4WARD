"""Live road-speed ratio from TomTom Flow Segment Data, in memory only.

TomTom's terms prohibit storing their data (no derivative database) and allow caching only for as
long as their own response headers say to. So nothing from TomTom ever reaches Tiger Data, SQLite,
a file, or a log line -- just a short-TTL in-memory read of "how much slower than free-flow are the
roads right now," shared by every bot's road and surface-transit legs (backend/conditions.py).

Polled at most once per TOMTOM_CACHE_TTL_S, only when a trip is actually being planned (there is no
background loop), and capped well under TomTom's free quota for the day (backend/config.py).
"""
from __future__ import annotations

import logging
import time
from datetime import date

import httpx

from .config import TOMTOM_CACHE_TTL_S, TOMTOM_CORRIDOR_POINTS, TOMTOM_DAILY_CAP

# httpx logs "HTTP Request: GET <url>" at INFO by default, and the TomTom URL carries the API key as a
# query parameter. Silencing this logger is the only way to guarantee the key never reaches a log file
# or stdout via a mechanism outside our own code.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

FLOW_URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"

_cache: tuple[float, float] | None = None  # (fetched_at, ratio); never anything from TomTom itself
_day: str | None = None
_count_today = 0


def _reset_if_new_day() -> None:
    global _day, _count_today
    today = date.today().isoformat()
    if _day != today:
        _day = today
        _count_today = 0


def _fetch_ratio(api_key: str) -> float | None:
    """Mean (freeFlowSpeed / currentSpeed) across the corridor points, clamped to a sane range so one
    bad reading can't blow up every route's timing. None on any failure -- the caller falls back to
    the typical-hour profile, never invents a number."""
    global _count_today
    ratios: list[float] = []
    with httpx.Client(timeout=5) as cl:
        for lat, lon in TOMTOM_CORRIDOR_POINTS:
            if _count_today >= TOMTOM_DAILY_CAP:
                break
            try:
                r = cl.get(FLOW_URL, params={"point": f"{lat},{lon}", "key": api_key})
                _count_today += 1
                if r.status_code != 200:
                    continue
                seg = r.json().get("flowSegmentData") or {}
                cur, free = seg.get("currentSpeed"), seg.get("freeFlowSpeed")
                if cur and free and cur > 0:
                    ratios.append(free / cur)
            except Exception:
                continue
    if not ratios:
        return None
    mean = sum(ratios) / len(ratios)
    return max(0.7, min(mean, 3.0))


def road_ratio(api_key: str | None) -> float | None:
    """Shared multiplier on free-flow travel time right now, or None if TomTom has nothing fresh
    (unconfigured, unreachable, rejected the key, or the day's request cap is already spent)."""
    global _cache
    if not api_key:
        return None
    _reset_if_new_day()
    now = time.time()
    if _cache is not None and now - _cache[0] < TOMTOM_CACHE_TTL_S:
        return _cache[1]
    if _count_today >= TOMTOM_DAILY_CAP:
        return _cache[1] if _cache is not None else None
    ratio = _fetch_ratio(api_key)
    if ratio is None:
        return _cache[1] if _cache is not None else None
    _cache = (now, ratio)
    return ratio


def requests_today() -> int:
    """For the Tiger Data drawer / debugging only: a count, never the data itself."""
    _reset_if_new_day()
    return _count_today
