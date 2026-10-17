"""Street-network legs (foot / bike / car) from the public OSM routing servers, with disk cache.

Cache doubles as replay mode: set REPLAY=1 to never touch the network (falls back to estimates
for anything not cached). Every result says whether it was `live`, `cached`, or `estimated`.
"""
import json
import os
from pathlib import Path

import httpx

from .config import SPEEDS_KMH
from .geo import haversine_km

CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "route_cache.json"
PROFILE = {"walk": "foot", "bike": "bike", "drive": "car"}
_cache: dict | None = None


def _cache_get() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_FILE.read_text())
        except Exception:
            _cache = {}
    return _cache


def _key(mode: str, a, b) -> str:
    return f"{mode}|{a[0]:.4f},{a[1]:.4f}|{b[0]:.4f},{b[1]:.4f}"


def _estimate(mode: str, a, b) -> dict:
    d = haversine_km(a, b) * 1.3  # street factor
    kmh = {"walk": SPEEDS_KMH["walk"], "bike": SPEEDS_KMH["bike"], "drive": SPEEDS_KMH["car"]}[mode]
    return {"distance_km": round(d, 3), "duration_s": d / kmh * 3600,
            "geometry": [[a[0], a[1]], [b[0], b[1]]], "provenance": "estimated"}


async def leg(mode: str, a: tuple[float, float], b: tuple[float, float]) -> dict:
    """mode in walk|bike|drive. Returns {distance_km, duration_s, geometry[[lat,lon]], provenance}."""
    if haversine_km(a, b) < 0.01:
        return {"distance_km": 0.0, "duration_s": 0.0, "geometry": [list(a)], "provenance": "live"}
    cache = _cache_get()
    k = _key(mode, a, b)
    if k in cache:
        return {**cache[k], "provenance": "cached"}
    if os.environ.get("REPLAY") == "1":
        return _estimate(mode, a, b)
    url = (f"https://routing.openstreetmap.de/routed-{PROFILE[mode]}/route/v1/driving/"
           f"{a[1]},{a[0]};{b[1]},{b[0]}")
    try:
        async with httpx.AsyncClient(timeout=10) as cl:
            r = await cl.get(url, params={"overview": "full", "geometries": "geojson", "steps": "false"})
            rt = r.json()["routes"][0]
        out = {"distance_km": round(rt["distance"] / 1000, 3), "duration_s": rt["duration"],
               "geometry": [[c[1], c[0]] for c in rt["geometry"]["coordinates"]]}
        cache[k] = out
        try:
            CACHE_FILE.write_text(json.dumps(cache))
        except Exception:
            pass
        return {**out, "provenance": "live"}
    except Exception:
        return _estimate(mode, a, b)
