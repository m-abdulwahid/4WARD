"""Controlled-access highways (DVP, Gardiner, 400-series, QEW, 407 ...) from the bundled OSM extract.

Used for one rule: a walking or cycling leg must never run along a highway you cannot legally use on foot or by bike,
and a trip that starts in a car on one must first drive to the next valid exit. Everything is offline (data/highways.json,
built by `python -m backend.build_highways`); if the file is missing the checks return "unknown", never "fine".
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .geo import haversine_km

FILE = Path(__file__).resolve().parent.parent / "data" / "highways.json"
CELL = 0.005  # degrees, about 500 m
M_PER_DEG_LAT = 110_540.0
_index: dict | None = None


def _load() -> dict:
    global _index
    if _index is not None:
        return _index
    idx: dict = {"cells": {}, "ways": [], "junctions": [], "ok": False}
    try:
        raw = json.loads(FILE.read_text())
        for w in raw["ways"]:
            wi = len(idx["ways"])
            idx["ways"].append({"name": w.get("name") or w.get("ref") or "controlled-access highway", "ref": w.get("ref", "")})
            pts = w["pts"]
            for a, b in zip(pts, pts[1:]):
                for ci in _cells_of(a, b):
                    idx["cells"].setdefault(ci, []).append((a[0], a[1], b[0], b[1], wi))
        idx["junctions"] = raw["junctions"]
        idx["ok"] = True
    except Exception:
        pass
    _index = idx
    return idx


def available() -> bool:
    return _load()["ok"]


def _cells_of(a, b) -> set[tuple[int, int]]:
    out = set()
    steps = max(1, int(max(abs(a[0] - b[0]), abs(a[1] - b[1])) / (CELL / 2)))
    for k in range(steps + 1):
        t = k / steps
        out.add((int((a[0] + (b[0] - a[0]) * t) / CELL), int((a[1] + (b[1] - a[1]) * t) / CELL)))
    return out


def _seg_dist_m(lat, lon, s) -> float:
    kx = math.cos(math.radians(lat)) * 111_320.0
    ax, ay = (s[1] - lon) * kx, (s[0] - lat) * M_PER_DEG_LAT
    bx, by = (s[3] - lon) * kx, (s[2] - lat) * M_PER_DEG_LAT
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L2))
    return math.hypot(ax + t * dx, ay + t * dy)


def _nearest_seg(lat: float, lon: float, within_m: float):
    idx = _load()
    if not idx["ok"]:
        return None
    r = int(within_m / 500) + 1
    ci, cj = int(lat / CELL), int(lon / CELL)
    best = None
    for di in range(-r, r + 1):
        for dj in range(-r, r + 1):
            for s in idx["cells"].get((ci + di, cj + dj), ()):
                d = _seg_dist_m(lat, lon, s)
                if d <= within_m and (best is None or d < best[0]):
                    best = (d, s)
    return best


def nearest(lat: float, lon: float, within_m: float = 60.0) -> tuple[float, str] | None:
    """(distance in metres, highway name) of the closest controlled-access way within `within_m`, else None."""
    b = _nearest_seg(lat, lon, within_m)
    return (b[0], _load()["ways"][b[1][4]]["name"]) if b else None


def on_highway(lat: float, lon: float, tol_m: float = 25.0) -> str | None:
    """Name of the controlled-access highway a point sits on, else None (also None when the data file is missing)."""
    n = nearest(lat, lon, tol_m)
    return n[1] if n else None


def _resample(geometry: list[list[float]], spacing_m: float) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in zip(geometry, geometry[1:]):
        d = haversine_km(tuple(a), tuple(b)) * 1000
        n = max(1, int(d / spacing_m))
        out += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n)]
    if geometry:
        out.append(tuple(geometry[-1]))
    return out


def _heading(lat0: float, lon0: float, lat1: float, lon1: float) -> float:
    """Direction in degrees (0-180, a line has no sense of travel) of the short step from point 0 to point 1."""
    kx = math.cos(math.radians(lat0)) * 111_320.0
    return math.degrees(math.atan2((lat1 - lat0) * M_PER_DEG_LAT, (lon1 - lon0) * kx)) % 180


def leg_on_highway(geometry: list[list[float]], tol_m: float = 6.0, min_run_m: float = 75.0, max_angle: float = 30.0,
                   spacing_m: float = 25.0) -> str | None:
    """Name of the highway a (walking/cycling) leg runs ALONG, else None. The leg must stay within `tol_m` of a
    controlled-access way and head the same way as it (within `max_angle` degrees) for `min_run_m` metres. Crossing a
    highway on a bridge or underpass, even a wide one, heads across it and does not count."""
    if not available() or len(geometry) < 2:
        return None
    samples = _resample(geometry, spacing_m)
    run, name = 0.0, None
    for (la0, lo0), (la1, lo1) in zip(samples, samples[1:]):
        near = _nearest_seg(la0, lo0, tol_m)
        if not near:
            run = 0.0
            continue
        s = near[1]
        diff = abs(_heading(la0, lo0, la1, lo1) - _heading(s[0], s[1], s[2], s[3]))
        diff = min(diff, 180 - diff)
        if diff <= max_angle:
            run, name = run + spacing_m, _load()["ways"][s[4]]["name"]
            if run >= min_run_m:
                return name
        else:
            run = 0.0
    return None


def next_exit(origin: tuple[float, float], dest: tuple[float, float], max_km: float = 25.0) -> dict | None:
    """The nearest exit (motorway_junction) that is not farther from the destination than the origin is: where a
    driver on a highway can first leave it on the way to `dest`. An ESTIMATE of 'next valid exit' (direction of
    travel is not known, so progress toward the destination is the tiebreak)."""
    idx = _load()
    if not idx["ok"]:
        return None
    base = haversine_km(origin, dest)
    best = None
    for j in idx["junctions"]:
        p = (j["lat"], j["lon"])
        d_o = haversine_km(origin, p)
        if d_o > max_km or d_o < 0.3 or haversine_km(p, dest) > base + 0.5:
            continue
        if best is None or d_o < best[0]:
            best = (d_o, j)
    if not best:
        return None
    j = best[1]
    return {"lat": j["lat"], "lon": j["lon"], "name": j.get("name") or (f"Exit {j['ref']}" if j.get("ref") else "next highway exit"), "distance_km": round(best[0], 2)}
