"""Shared conditions layer: how much slower than free-flow the roads are at a given time, for ALL bots.

One place decides it, so every road leg (drive, rideshare, lift, the drive to a park & ride lot, the generic bus) and every
streetcar leg is timed the same way:

  * a departure soon (within REALTIME_HORIZON_MIN of now) uses TomTom live conditions on the downtown stretches of the leg
    (backend/tomtom.py: latest values in memory, never stored), weighted by how many metres of the leg lie on each corridor;
  * everything else (the rest of the leg, later departures, TomTom unavailable or rejected) uses the typical profile for that
    hour, labelled ESTIMATED. A route is never failed because TomTom is unreachable.
"""
from __future__ import annotations

from datetime import datetime

from . import tomtom
from .config import REALTIME_HORIZON_MIN, traffic_multiplier

TYPICAL = "typical hour (ESTIMATED)"
LIVE = tomtom.LABEL_LIVE


def _soon(when: datetime, now: datetime | None) -> bool:
    return now is not None and abs((when - now).total_seconds()) <= REALTIME_HORIZON_MIN * 60


def leg_conditions(leg: dict | None, when: datetime, now: datetime | None = None) -> dict:
    """{"factor": slowdown vs free flow for this leg, "typical": the typical factor, "source": label, "live_share": 0..1,
    "as_of": age in seconds of the oldest live value used, or None}."""
    typ = traffic_multiplier(when.hour)
    out = {"factor": typ, "typical": typ, "source": TYPICAL, "live_share": 0.0, "age_s": None}
    prof = (leg or {}).get("corridors")
    if not prof or not _soon(when, now):
        return out
    total_m = max(1.0, float(leg.get("distance_km", 0)) * 1000)
    live_m, weighted, ages = 0.0, 0.0, []
    for sid, metres in prof.items():
        lf = tomtom.live_factor(sid)
        if lf is None:
            continue
        live_m += metres
        weighted += metres * lf[0]
        ages.append(lf[1])
    if live_m <= 0:
        return out
    live_m = min(live_m, total_m)
    out.update(factor=(weighted + (total_m - live_m) * typ) / total_m, source=LIVE, live_share=round(live_m / total_m, 2), age_s=round(max(ages)))
    return out


def road_factor(when: datetime, now: datetime | None = None, leg: dict | None = None) -> tuple[float, str]:
    """(multiplier on free-flow travel time, provenance)."""
    c = leg_conditions(leg, when, now)
    return c["factor"], c["source"]


async def prepare(when: datetime, now: datetime | None = None) -> None:
    """Make sure live conditions are fresh before planning a trip that starts soon. Demand-driven (no background polling), at
    most one TomTom poll per TTL, and it can never fail a plan: any problem just means the typical profile is used."""
    if _soon(when, now or datetime.now(when.tzinfo)):
        try:
            await tomtom.refresh()
        except Exception:  # noqa: BLE001
            pass