"""City of Toronto road restrictions/closures (open.toronto.ca, dataset "road-restrictions").

Off by default (ROAD_RESTRICTIONS_ENABLED unset or not "true"): this is a real-time, ~4 MB feed and
not yet wired into any route's timing, so there is no reason to fetch it until that integration
exists. The dataset's own licence field reads "License not specified" on the portal (verified
2026-10-18); the portal's standard terms (Open Government Licence - Toronto) allow copying and
commercial use with attribution ("Contains information licensed under the Open Government Licence -
Toronto"), so this is used under that licence, attributed here and wherever it reaches the UI.

Refreshed at most once a day (matches the "refresh Toronto road restrictions daily" ask); never
stored in Tiger Data -- this is the city's own open feed, not TomTom, but there's still no use for a
derivative database of a 4 MB feed when the live source is free and real-time.
"""
from __future__ import annotations

import os
import time

import httpx

FEED_URL = "https://secure.toronto.ca/opendata/cart/road_restrictions/v3?format=json"
CACHE_TTL_S = 24 * 3600

_cache: tuple[float, list[dict]] | None = None


def enabled() -> bool:
    return os.environ.get("ROAD_RESTRICTIONS_ENABLED", "").strip().lower() == "true"


def _normalize(row: dict) -> dict | None:
    try:
        lat, lon = float(row["latitude"]), float(row["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "id": row.get("id"),
        "road": row.get("road"),
        "name": row.get("name"),
        "lat": lat,
        "lon": lon,
        "description": row.get("description"),
        "direction": row.get("directionsAffected"),
        "type": row.get("type"),
        "max_impact": row.get("maxImpact"),
        "curr_impact": row.get("currImpact"),
        "start_ts": int(row["startTime"]) // 1000 if row.get("startTime") else None,
        "end_ts": int(row["endTime"]) // 1000 if row.get("endTime") else None,
    }


def _fetch_now() -> list[dict]:
    with httpx.Client(timeout=20) as cl:
        r = cl.get(FEED_URL)
        r.raise_for_status()
        data = r.json()
    closures = data.get("Closure") or []
    now = time.time()
    out = []
    for row in closures:
        if str(row.get("expired", "0")) not in ("0", "False", "false"):
            continue
        n = _normalize(row)
        if n and (n["end_ts"] is None or n["end_ts"] >= now):
            out.append(n)
    return out


def active_closures() -> list[dict]:
    """Currently-active, non-expired closures, refreshed at most once a day. Empty list if the
    feature is off or the feed is unreachable -- never invents data, never blocks route computation."""
    global _cache
    if not enabled():
        return []
    now = time.time()
    if _cache is not None and now - _cache[0] < CACHE_TTL_S:
        return _cache[1]
    try:
        rows = _fetch_now()
    except Exception:
        return _cache[1] if _cache is not None else []
    _cache = (now, rows)
    return rows
