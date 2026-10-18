"""Shared conditions layer: how much slower than free-flow the roads are at a given time.

One place decides it, so every bot's road legs (drive, rideshare, lift, bus, the drive to a park & ride lot) are timed the
same way. A departure soon uses OBSERVED conditions when a source has them; a later one uses the typical profile for that
hour, labelled ESTIMATED. (Phase 6 plugs TomTom-observed delays into `_observed`.)
"""
from __future__ import annotations

from datetime import datetime

from .config import REALTIME_HORIZON_MIN, traffic_multiplier


def _observed(when: datetime) -> float | None:
    """Observed slowdown factor for `when`, or None when no live source is available (nothing is invented)."""
    return None


def road_factor(when: datetime, now: datetime | None = None) -> tuple[float, str]:
    """(multiplier on free-flow travel time, provenance)."""
    if now is not None and abs((when - now).total_seconds()) <= REALTIME_HORIZON_MIN * 60:
        obs = _observed(when)
        if obs is not None:
            return obs, "observed"
    return traffic_multiplier(when.hour), "typical hour (ESTIMATED)"
