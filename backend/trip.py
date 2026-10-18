"""Multi-stop trips: route each consecutive pair, carry vehicle state across legs, and combine the per-leg
candidates into WHOLE-TRIP candidates with the same shape planner.plan() returns for one leg.

Because a trip candidate looks like a single-leg candidate (id, legs, duration_min, cost, co2_kg, ...), the four
bots rank trips with bots/optimize.py exactly as they rank single legs, and the optimum guarantee carries over.

How it works
  1. planner.plan() runs once per consecutive pair of points (every mode), timed from the estimated arrival at the
     previous stop (the fastest option there + dwell + any time-window wait). Later legs are ESTIMATES for that reason.
  2. Each leg's candidates are pruned to the non-dominated set within a vehicle class, so combining stays small.
  3. A depth-first search chains one candidate per leg. Own vehicles (car, bike, e-bike) are carried: you can only
     drive/ride a vehicle that is with you; it stays where you left it. A `via` point must be passed through in the
     same mode (no mode change at a pass-through). Stop dwell and time windows are applied to the schedule.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import math
import time
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, field_validator

from . import pareto, planner
from .geo import haversine_km

TORONTO = ZoneInfo("America/Toronto")
MAX_STOPS = 6
MAX_STOPS_REORDER = 3  # reordering times every directed pair; 3 stops = 12 pairs
MAX_COMBOS = 20000
CACHE_TTL_S = 120
VEHICLE_BY_ID = {"drive": "car", "park_ride": "car", "bike": "bike", "ebike": "ebike"}
LETTERS = "ABCDEFGHIJ"


class Stop(BaseModel):
    name: str
    lat: float | None = None
    lon: float | None = None
    type: Literal["stop", "via", "pickup", "dropoff", "errand"] = "stop"
    dwell_min: float = 0
    window: tuple[str, str] | None = None  # ("HH:MM", "HH:MM"): arrive inside it; early arrivals wait

    @field_validator("dwell_min")
    @classmethod
    def _non_negative(cls, v: float) -> float:
        return max(0.0, float(v))


def _clock(s: str, base: datetime) -> datetime:
    h, m = s.split(":")[:2]
    return base.replace(hour=int(h), minute=int(m), second=0, microsecond=0)


def _hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _vehicle_ok(cid: str, vehicles: dict, origin: tuple[float, float]) -> bool:
    v = VEHICLE_BY_ID.get(cid)
    if not v:
        return True
    loc = vehicles.get(v)
    return loc is not None and haversine_km(loc, origin) < 0.15


def _after(cand: dict, vehicles: dict) -> dict:
    out = dict(vehicles)
    v = VEHICLE_BY_ID.get(cand["id"])
    if v == "car":
        out["car"] = tuple(cand["legs"][0]["to"]) if cand["id"] == "park_ride" else tuple(cand["legs"][-1]["to"])
    elif v:
        out[v] = tuple(cand["legs"][-1]["to"])
    return out


def _metrics(c: dict) -> tuple:
    rel = (c.get("reliability") or {}).get("reliability_pct")
    return (c["duration_min"], c["cost"], c["co2_kg"], c["walk_m"], c["transfers"], -c.get("bag_friendliness", 1.0),
            -(rel if rel is not None else -1))


def _prune(cands: list[dict], keep: int | None = None) -> list[dict]:
    """Drop candidates that another one in the same vehicle class beats or ties on every metric."""
    pool = [c for c in cands if c.get("available") and c.get("duration_min") is not None and not c.get("violates")]
    out = []
    for c in pool:
        vc, mc = VEHICLE_BY_ID.get(c["id"]), _metrics(c)
        dominated = any(VEHICLE_BY_ID.get(o["id"]) == vc and o is not c
                        and all(x <= y for x, y in zip(_metrics(o), mc)) and _metrics(o) != mc for o in pool)
        if not dominated:
            out.append(c)
    if keep and len(out) > keep:  # safety valve: best per objective plus one per vehicle class
        picked: dict[str, dict] = {}
        for key in ("duration_min", "cost", "co2_kg"):
            for c in sorted(out, key=lambda c: c[key])[:2]:
                picked[c["id"]] = c
        for c in out:
            if VEHICLE_BY_ID.get(c["id"]):
                picked.setdefault(c["id"], c)
        out = list(picked.values())
    return out


def _combine(points: list[dict], leg_sets: list[list[dict]], constraints: planner.Constraints, depart: datetime,
             order_prefix: str = "") -> list[dict]:
    """Depth-first chain of one candidate per leg with vehicle state, dwell, windows. Returns trip candidates."""
    n = len(leg_sets)
    start_vehicles = {}
    here = (points[0]["lat"], points[0]["lon"])
    for flag, v in (("has_car", "car"), ("has_bike", "bike"), ("has_ebike", "ebike")):
        if getattr(constraints, flag):
            start_vehicles[v] = here
    deadline = _clock(constraints.arrive_by, depart) if constraints.arrive_by else None
    trips: list[dict] = []

    def walk(i: int, now: datetime, picks: list[dict], vehicles: dict, sched: list[dict], bad: list[str]) -> None:
        if len(trips) > MAX_COMBOS:
            return
        if i == n:
            trips.append(_build(points, picks, sched, bad, depart, now, deadline, order_prefix))
            return
        origin = (points[i]["lat"], points[i]["lon"])
        dest_pt = points[i + 1]
        for c in leg_sets[i]:
            if not _vehicle_ok(c["id"], vehicles, origin):
                continue
            if i > 0 and points[i]["type"] == "via" and c["id"] != picks[-1]["id"]:
                continue  # passing through: same mode on both sides
            arrive = now + timedelta(minutes=c["duration_min"])
            wait, viol = 0.0, list(bad)
            if dest_pt.get("window"):
                ws, we = _clock(dest_pt["window"][0], depart), _clock(dest_pt["window"][1], depart)
                if arrive < ws:
                    wait = (ws - arrive).total_seconds() / 60
                elif arrive > we:
                    viol.append(f"window:{dest_pt['name']}")
            dwell = dest_pt["dwell_min"] if i + 1 < len(points) - 1 else 0.0
            depart_next = arrive + timedelta(minutes=wait + dwell)
            entry = {"arrive": arrive, "wait_min": round(wait, 1), "dwell_min": dwell, "depart": depart_next,
                     "vehicles": _after(c, vehicles)}
            walk(i + 1, depart_next, picks + [c], entry["vehicles"], sched + [entry], viol)

    walk(0, depart, [], start_vehicles, [], [])
    return trips


def _build(points, picks, sched, bad, depart, finish, deadline, order_prefix) -> dict:
    legs, notes, schedule = [], [], []
    for i, c in enumerate(picks):
        for l in c["legs"]:
            legs.append({**l, "segment": i, "cand_id": c["id"]})
        for note in c.get("notes", []):
            notes.append(f"Leg {LETTERS[points[i]['idx']]} to {LETTERS[points[i + 1]['idx']]}: {note}")
    t = depart
    schedule.append({"label": LETTERS[points[0]["idx"]], "name": points[0]["name"], "kind": "origin", "arrive": None,
                     "depart": _hm(depart), "dwell_min": 0.0, "wait_min": 0.0})
    for i, e in enumerate(sched):
        last = i == len(sched) - 1
        p = points[i + 1]
        schedule.append({"label": LETTERS[p["idx"]], "name": p["name"], "kind": "destination" if last else p["type"],
                         "arrive": _hm(e["arrive"]), "wait_min": e["wait_min"], "dwell_min": e["dwell_min"],
                         "depart": None if last else _hm(e["depart"]), "window": p.get("window")})
    total_min = (finish - depart).total_seconds() / 60
    arrive_dt = finish
    rels = [c["reliability"] for c in picks if c.get("reliability")]
    cand = {
        "id": order_prefix + "|".join(c["id"] for c in picks),
        "title": " then ".join(c["title"] for c in picks),
        "available": True, "legs": legs, "schedule": schedule,
        "duration_min": round(total_min, 1), "cost": round(sum(c["cost"] for c in picks), 2),
        "co2_kg": round(sum(c["co2_kg"] for c in picks), 3), "transfers": sum(c["transfers"] for c in picks),
        "walk_m": sum(c["walk_m"] for c in picks), "bag_friendliness": round(min(c.get("bag_friendliness", 1.0) for c in picks), 2),
        "depart": _hm(depart), "arrive": _hm(arrive_dt), "notes": notes + ["Later legs are timed from the estimated arrival at each stop (ESTIMATED)"],
    }
    viol = list(bad) + [v for c in picks for v in (c.get("violates") or [])]
    if viol:
        cand["violates"] = sorted(set(viol))
    if deadline:
        cand["meets_deadline"] = arrive_dt <= deadline
        cand["leave_by"] = _hm(deadline - timedelta(minutes=total_min))
    if len(rels) == len([c for c in picks if any(l.get("route_id") for l in c["legs"])]) and rels:
        p = 1.0
        for r in rels:
            p *= r["reliability_pct"] / 100
        cand["reliability"] = {"reliability_pct": round(100 * p), "history_days": min(r["history_days"] for r in rels),
                               "basis": "product of per-leg on-time chances (ESTIMATED, assumes legs are independent)"}
    return cand


def _limit(leg_sets: list[list[dict]]) -> list[list[dict]]:
    """Keep the number of chained combinations bounded: if too many, trim each leg to its best few."""
    if math.prod(len(s) for s in leg_sets) <= MAX_COMBOS:
        return leg_sets
    return [_prune(s, keep=5) for s in leg_sets]


_cache: dict[str, tuple[float, asyncio.Task]] = {}


async def plan_trip(origin: dict, stops: list[dict], destination: dict, constraints: planner.Constraints,
                    optimize_order: bool = False) -> dict:
    """origin/destination: {name, lat, lon}; stops: [{name, lat, lon, type, dwell_min, window}] in the order typed.
    Identical requests inside CACHE_TTL_S share one computation (the engine and the page every bot operates must
    show the same set, and four bots would otherwise recompute the same trip four times)."""
    if len(stops) > MAX_STOPS:
        raise ValueError(f"At most {MAX_STOPS} stops are supported")
    if optimize_order and len(stops) > MAX_STOPS_REORDER:
        raise ValueError(f"Reordering supports at most {MAX_STOPS_REORDER} stops; keep your order or remove some stops")
    key = json.dumps([origin, stops, destination, constraints.model_dump(), optimize_order], sort_keys=True, default=str)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S and not (hit[1].done() and hit[1].exception()):
        return await hit[1]
    for k in [k for k, v in _cache.items() if time.time() - v[0] >= CACHE_TTL_S]:
        _cache.pop(k, None)
    task = asyncio.ensure_future(_plan_trip(origin, stops, destination, constraints, optimize_order))
    _cache[key] = (time.time(), task)
    return await task


async def _leg_candidates(a: dict, b: dict, constraints: planner.Constraints, depart: datetime, final: bool) -> tuple[list[dict], dict]:
    c = constraints.model_copy(update={
        "depart_at": _hm(depart), "arrive_by": constraints.arrive_by if final else None,
        "stay_hours": (b["dwell_min"] / 60 if b["dwell_min"] and not final else constraints.stay_hours)})
    plan = await planner.plan((a["lat"], a["lon"]), (b["lat"], b["lon"]), c)
    return plan["candidates"], plan


async def _plan_trip(origin, stops, destination, constraints, optimize_order) -> dict:
    now = datetime.now(TORONTO)
    depart = _clock(constraints.depart_at, now) if constraints.depart_at else now.replace(second=0, microsecond=0)

    def point(p: dict, idx: int, kind: str) -> dict:
        return {"name": p["name"], "lat": p["lat"], "lon": p["lon"], "type": kind if kind in ("origin", "destination") else p.get("type", "stop"),
                "dwell_min": 0.0 if p.get("type") == "via" else float(p.get("dwell_min") or 0), "window": p.get("window"), "idx": idx}

    pts_in = [point(origin, 0, "origin")] + [point(s, i + 1, "stop") for i, s in enumerate(stops)] + [point(destination, len(stops) + 1, "destination")]
    orders = [list(range(1, len(stops) + 1))]
    if optimize_order and len(stops) > 1:
        orders = [list(p) for p in itertools.permutations(range(1, len(stops) + 1))]
    last = len(pts_in) - 1
    trips: list[dict] = []
    plans: list[dict] = []

    if len(orders) == 1:
        # fixed order: time each leg from the estimated arrival at the previous stop
        seq = [0] + orders[0] + [last]
        leg_sets, est = [], depart
        for i in range(len(seq) - 1):
            a, b = pts_in[seq[i]], pts_in[seq[i + 1]]
            cands, plan = await _leg_candidates(a, b, constraints, est, i == len(seq) - 2)
            plans.append(plan)
            leg_sets.append(_prune(cands))
            if not leg_sets[-1]:
                break
            fastest = min(c["duration_min"] for c in leg_sets[-1])
            est = est + timedelta(minutes=fastest + (b["dwell_min"] if i < len(seq) - 2 else 0))
            if b.get("window"):
                est = max(est, _clock(b["window"][0], depart))
        if len(leg_sets) == len(seq) - 1 and all(leg_sets):
            trips = _combine([pts_in[i] for i in seq], _limit(leg_sets), constraints, depart)
    else:
        # reordering: every directed pair is planned once, at the trip's departure hour (ESTIMATED), in parallel
        need = {(a, b) for o in orders for a, b in zip([0] + o, o + [last])}
        results = await asyncio.gather(*[_leg_candidates(pts_in[a], pts_in[b], constraints, depart, b == last) for a, b in sorted(need)])
        by_pair = {pair: _prune(r[0]) for pair, r in zip(sorted(need), results)}
        plans = [r[1] for r in results]
        for o in orders:
            seq = [0] + o + [last]
            sets = [by_pair[(a, b)] for a, b in zip(seq, seq[1:])]
            if all(sets):
                prefix = "".join(LETTERS[i] for i in seq) + ":"
                trips += _combine([pts_in[i] for i in seq], _limit(sets), constraints, depart, prefix)

    pareto.mark(trips)
    base = plans[0] if plans else {}
    return {"origin": [origin["lat"], origin["lon"]], "destination": [destination["lat"], destination["lon"]],
            "depart": _hm(depart), "candidates": trips, "traffic_multiplier": base.get("traffic_multiplier", 1.0),
            "points": [{"label": LETTERS[p["idx"]], "name": p["name"], "lat": p["lat"], "lon": p["lon"], "type": p["type"],
                        "dwell_min": p["dwell_min"], "window": p.get("window")} for p in pts_in],
            "multi_stop": True, "optimize_order": bool(optimize_order and len(stops) > 1),
            "data_notes": (base.get("data_notes") or []) + ["Multi-stop: each leg is routed separately; legs after the first are timed from estimated arrivals (ESTIMATED)"]}
