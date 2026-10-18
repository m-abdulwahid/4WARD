"""Multi-stop trips with logistics: route each consecutive pair, carry traveler and vehicle state across legs, and
combine the per-leg candidates into WHOLE-TRIP candidates with the same shape planner.plan() returns for one leg.

Because a trip candidate looks like a single-leg candidate (id, legs, duration_min, cost, co2_kg, ...), the four bots
rank trips with bots/optimize.py exactly as they rank single legs, and the optimum guarantee carries over.

How it works
  1. planner.plan() runs once per consecutive pair of points (every mode), timed from the estimated arrival at the
     previous stop (the fastest option there + dwell + any time-window wait). Later legs are ESTIMATES for that reason.
  2. Each leg's candidates are pruned to the non-dominated set within a vehicle class, so combining stays small.
  3. A depth-first search chains one candidate per leg while carrying the traveler's state:
       traveler_state  on_foot | cycling | driving | passenger | on_transit
       car             where it is, whether you are in it, and whether it is within reach
     Rules: you can only drive a car that is with you; a parked car stays where it was parked; mode changes happen
     only at stops, stations, parking places and pick-up / drop-off points (never mid-route); a `via` point is passed
     in the same mode; "dropped off" / "picked up" legs are lifts from someone else (no fare, no parking for you, their
     car does not continue with you); "park at X" arrives driving and leaves the car there; "get off at X" arrives by
     transit; starting in a car means the first leg is driven, and on a controlled-access highway it is a drive to the
     next exit. Walking and cycling legs along such highways are rejected in planner.py (backend/highways.py).
  4. Plan the return to the car (only when the user said Yes): a later stop that needs the car after it was left
     behind gets a "go back to the car" leg first, and a car you will need AFTER the trip gets a final leg back to it.
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

from . import fares, highways, osm, pareto, planner, reliability, schedule
from .config import LEAVE_BY_SEARCH_MIN, PARKING_WALK_MIN, traffic_multiplier
from .geo import haversine_km

TORONTO = ZoneInfo("America/Toronto")
MAX_STOPS = 6
MAX_STOPS_REORDER = 3  # reordering times every directed pair; 3 stops = 12 pairs
MAX_COMBOS = 20000
CACHE_TTL_S = 120
LETTERS = "ABCDEFGHIJ"
CAR_IDS = ("drive", "park_ride", "drive_exit")
NON_CAR_MODES = ["walk", "bike", "ebike", "bikeshare", "rideshare", "transit"]
NEAR_KM = 0.15
VEHICLE_LEG_MODES = {"bus": {"bus"}, "subway": {"subway"}, "streetcar": {"lrt_streetcar"}, "train": {"go_train", "subway"}}
StopType = Literal["stop", "via", "pickup", "dropoff", "errand", "park", "alight"]


class TripImpossible(ValueError):
    """The trip as described cannot be planned (a logistics rule makes it impossible); the message says why."""


class Stop(BaseModel):
    name: str
    lat: float | None = None
    lon: float | None = None
    type: StopType = "stop"
    dwell_min: float = 0
    window: tuple[str, str] | None = None  # ("HH:MM", "HH:MM"): arrive inside it; early arrivals wait
    vehicle: Literal["bus", "subway", "streetcar", "train", "transit"] | None = None  # for type "alight": what you get off

    @field_validator("dwell_min")
    @classmethod
    def _non_negative(cls, v: float) -> float:
        return max(0.0, float(v))


def _clock(s: str, base: datetime) -> datetime:
    h, m = s.split(":")[:2]
    return base.replace(hour=int(h), minute=int(m), second=0, microsecond=0)


def _deadline(arrive_by: str | None, depart: datetime) -> datetime | None:
    """The arrive-by time; one that is already behind the departure means tomorrow."""
    if not arrive_by:
        return None
    d = _clock(arrive_by, depart)
    return d + timedelta(days=1) if d < depart else d


def _hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _near(a, b) -> bool:
    return a is not None and b is not None and haversine_km(tuple(a), tuple(b)) < NEAR_KM


def _eff_id(c: dict) -> str:
    """The id that decides vehicle rules (a 'go back to the car, then drive' composite counts as its driving part)."""
    return c.get("car_id", c["id"])


def _car_loc(c: dict) -> tuple[float, float] | None:
    leg = next((l for l in c["legs"] if l["mode"] == "car"), None)
    return tuple(leg["to"]) if leg else None


# -- state -----------------------------------------------------------------------------------------------------------
def _init_state(constraints: planner.Constraints, origin: tuple[float, float]) -> dict:
    return {"car": origin if constraints.has_car else None, "in_car": bool(constraints.start_in_car and constraints.has_car),
            "at_hand": True, "bike": origin if constraints.has_bike else None, "ebike": origin if constraints.has_ebike else None,
            "traveler": "driving" if constraints.start_in_car else "on_foot"}


def _forced(points: list[dict], i: int) -> dict:
    """Which kind of leg the user's wording demands between points[i] and points[i + 1]."""
    a, b = points[i], points[i + 1]
    out: dict = {}
    if a["type"] == "pickup" or b["type"] == "dropoff":
        out["lift"] = True
    if b["type"] == "park":
        out["drive"] = True
    if b["type"] == "alight":
        out["transit"] = b.get("vehicle") or "transit"
    return out


def _allowed(c: dict, st: dict, origin: tuple[float, float], forced: dict) -> bool:
    cid = _eff_id(c)
    if forced.get("lift") and cid != "lift":
        return False
    if forced.get("drive") and cid != "drive":
        return False
    if forced.get("transit"):
        if not cid.startswith("transit"):
            return False
        modes = VEHICLE_LEG_MODES.get(forced["transit"])
        if modes and not any(l["mode"] in modes for l in c["legs"]):
            return False  # "get off the subway at X" cannot be met by a bus route
    if cid == "lift" and not forced.get("lift"):
        return False  # a free ride only exists where the user described one
    if st["in_car"] and cid not in CAR_IDS:
        return False  # still in the car at a place where it cannot be parked (start, highway exit): keep driving
    if cid == "drive_exit":
        return st["in_car"]
    if cid in ("drive", "park_ride"):
        return _near(st["car"], c["fetch_car_at"] if c.get("fetch_car_at") else origin) and not (c.get("fetch_car_at") and st["in_car"])
    if cid == "bike":
        return _near(st["bike"], origin)
    if cid == "ebike":
        return _near(st["ebike"], origin)
    return True


def _apply(c: dict, st: dict, dest: tuple[float, float]) -> dict:
    cid, s = _eff_id(c), dict(st)
    if cid == "drive":
        s.update(car=_car_loc(c) or dest, in_car=False, at_hand=False, traveler="on_foot")
    elif cid == "drive_exit":
        s.update(car=dest, in_car=True, at_hand=True, traveler="driving")
    elif cid == "park_ride":
        s.update(car=_car_loc(c), in_car=False, at_hand=False, traveler="on_transit")
    elif cid in ("bike", "ebike"):
        s.update(**{cid: dest}, traveler="cycling")
    elif cid == "lift":
        s["traveler"] = "passenger"
    elif cid.startswith("transit"):
        s["traveler"] = "on_transit"
    elif cid == "bikeshare":
        s["traveler"] = "cycling"
    elif cid == "rideshare":
        s["traveler"] = "passenger"
    else:
        s["traveler"] = "on_foot"
    return s


def _arrived_as(c: dict) -> str:
    cid = _eff_id(c)
    return {"drive": "driving", "drive_exit": "driving", "lift": "passenger", "rideshare": "passenger", "park_ride": "on_transit",
            "bike": "cycling", "ebike": "cycling", "bikeshare": "cycling"}.get(cid, "on_transit" if cid.startswith("transit") else "on_foot")


def _car_text(st: dict, points: list[dict]) -> str:
    if st["car"] is None:
        return "no car"
    if st["in_car"]:
        return "you are in it"
    for p in points:
        if _near(st["car"], (p["lat"], p["lon"])):
            return f"parked at {p['name']}"
    return "parked at a park & ride lot"


# -- candidate shaping ------------------------------------------------------------------------------------------------
def _metrics(c: dict) -> tuple:
    rel = (c.get("reliability") or {}).get("reliability_pct")
    return (c["duration_min"], c["cost"], c["co2_kg"], c["walk_m"], c["transfers"], -c.get("bag_friendliness", 1.0),
            -(rel if rel is not None else -1))


def _vclass(c: dict) -> str | None:
    cid = _eff_id(c)
    return cid if cid in ("drive", "park_ride", "bike", "ebike", "drive_exit", "lift") else None


def _prune(cands: list[dict], keep: int | None = None) -> list[dict]:
    """Drop candidates that another one in the same vehicle class beats or ties on every metric."""
    pool = [c for c in cands if c.get("available") and c.get("duration_min") is not None and not c.get("violates")]
    out = []
    for c in pool:
        vc, mc = _vclass(c), _metrics(c)
        dominated = any(_vclass(o) == vc and o is not c and all(x <= y for x, y in zip(_metrics(o), mc)) and _metrics(o) != mc for o in pool)
        if not dominated:
            out.append(c)
    if keep and len(out) > keep:  # safety valve: best per objective plus one per vehicle class
        picked: dict[str, dict] = {}
        for key in ("duration_min", "cost", "co2_kg"):
            for c in sorted(out, key=lambda c: c[key])[:2]:
                picked[c["id"]] = c
        for c in out:
            if _vclass(c):
                picked.setdefault(c["id"], c)
        out = list(picked.values())
    return out


def _with_walkback(c: dict) -> dict:
    """You left the car parked and now drive it again: add the walk back to it (a parking connection, ESTIMATE)."""
    minutes = PARKING_WALK_MIN["other"]
    here = c["legs"][0]["from"]
    leg = {"mode": "walk", "label": "Walk back to your car (estimated)", "from": here, "to": here, "distance_km": round(4.8 * minutes / 60, 3),
           "duration_s": minutes * 60, "geometry": [list(here)], "provenance": "estimated", "cost": 0.0, "co2_kg": 0.0}
    out = dict(c)
    out["legs"] = [leg] + list(c["legs"])
    out["duration_min"] = round(c["duration_min"] + minutes, 1)
    out["walk_m"] = c["walk_m"] + round(leg["distance_km"] * 1000)
    return out


# -- combining ----------------------------------------------------------------------------------------------------------
def _combine(points: list[dict], leg_sets: list[list[dict]], constraints: planner.Constraints, depart: datetime,
             order_prefix: str = "", returns: dict | None = None) -> list[dict]:
    """Depth-first chain of one candidate per leg with traveler/vehicle state, dwell, windows. Returns trip candidates.
    `returns` maps a car location key to the non-car options for getting back to it from the destination."""
    n = len(leg_sets)
    start = (points[0]["lat"], points[0]["lon"])
    deadline = _deadline(constraints.arrive_by, depart)
    dest = (points[-1]["lat"], points[-1]["lon"])
    trips: list[dict] = []
    real_now = planner._now(constraints)
    walkbacks: dict[int, dict] = {}
    timed_cache: dict[tuple, dict] = {}

    def timed(c: dict, at: datetime) -> dict:
        """The leg option re-timed to the moment it really starts (real departures, road conditions then)."""
        key = (id(c), int(at.timestamp() // 60))
        hit = timed_cache.get(key)
        if hit is None:
            legs, arr = schedule.retime(c["legs"], at, real_now)
            hit = dict(c)
            hit["legs"] = legs
            hit["duration_min"] = round((arr - at).total_seconds() / 60, 1)
            timed_cache[key] = hit
        return hit

    def walk(i: int, now: datetime, picks: list[dict], st: dict, sched: list[dict], bad: list[str]) -> None:
        if len(trips) > MAX_COMBOS:
            return
        if i == n:
            _finish_trip(points, picks, sched, bad, depart, now, deadline, order_prefix, st, constraints, dest, returns, trips)
            return
        origin = (points[i]["lat"], points[i]["lon"])
        dest_pt = points[i + 1]
        forced = _forced(points, i)
        for c0 in leg_sets[i]:
            if not _allowed(c0, st, origin, forced):
                continue
            if i > 0 and points[i]["type"] == "via" and _eff_id(c0) != _eff_id(picks[-1]):
                continue  # passing through: same mode on both sides
            if _eff_id(c0) in ("drive", "park_ride") and not c0.get("fetch_car_at") and not st["at_hand"] and not st["in_car"]:
                if id(c0) not in walkbacks:
                    walkbacks[id(c0)] = _with_walkback(c0)
                c0 = walkbacks[id(c0)]
            c = timed(c0, now)
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
            new_st = _apply(c, st, (dest_pt["lat"], dest_pt["lon"]))
            entry = {"arrive": arrive, "wait_min": round(wait, 1), "dwell_min": dwell, "depart": depart_next,
                     "arrived_as": _arrived_as(c), "state": new_st}
            walk(i + 1, depart_next, picks + [c], new_st, sched + [entry], viol)

    walk(0, depart, [], _init_state(constraints, start), [], [])
    _attach_leave_by(points, trips, deadline, depart, real_now)
    return trips


def _arrival(points: list[dict], picks: list[dict], depart: datetime, now: datetime) -> datetime:
    """Replay a chosen sequence of leg options from `depart`: real departures, stays and meeting windows. Returns the arrival
    at the destination (a return leg to the car, if any, comes after it and is not part of the deadline)."""
    t = depart
    for i, c in enumerate(picks):
        _, t = schedule.retime(c["legs"], t, now)
        nxt = points[i + 1]
        if nxt.get("window"):
            ws = _clock(nxt["window"][0], depart)
            if t < ws:
                t = ws
        if i + 1 < len(points) - 1:
            t = t + timedelta(minutes=nxt["dwell_min"])
    return t


def _latest_leave(points: list[dict], picks: list[dict], deadline: datetime, depart: datetime, now: datetime) -> datetime | None:
    """Latest whole-minute departure that still reaches the destination by `deadline`, or None."""
    return schedule.latest_boundary(lambda d: _arrival(points, picks, d, now) <= deadline, deadline - timedelta(minutes=LEAVE_BY_SEARCH_MIN), deadline)

def _attach_leave_by(points: list[dict], trips: list[dict], deadline: datetime | None, depart: datetime, now: datetime) -> None:
    """Exact "leave by" for the trips that matter. With very many trips, the best few per objective get the exact search and
    the rest keep the simple estimate (deadline minus duration)."""
    if not deadline:
        for c in trips:
            c.pop("_picks", None)
        return
    exact = trips
    if len(trips) > 1500:
        exact = []
        for key in ("duration_min", "cost", "co2_kg"):
            exact += sorted(trips, key=lambda c: c[key])[:80]
        exact += sorted(trips, key=lambda c: -(c.get("reliability") or {}).get("reliability_pct", -1))[:80]
    chosen = {id(c) for c in exact}
    for c in trips:
        picks = c.pop("_picks", None)
        if picks and id(c) in chosen:
            leave = _latest_leave(points, picks, deadline, depart, now)
            if leave is None:
                c.pop("leave_by", None)
            else:
                c["leave_by"] = _hm(leave)


def _finish_trip(points, picks, sched, bad, depart, finish, deadline, prefix, st, constraints, dest, returns, trips) -> None:
    viol = list(bad)
    ret = None
    if constraints.car_needed_after and constraints.has_car and st["car"] is not None and not _near(st["car"], dest):
        if constraints.plan_car_return and returns is not None:
            key = _loc_key(st["car"])
            for r in returns.get(key, []):
                trips.append(_build(points, picks, sched, list(viol), depart, finish, deadline, prefix, st, r, tuple(st["car"]), constraints_traveler(constraints)))
            return
        viol.append("car_left_behind")
    trips.append(_build(points, picks, sched, viol, depart, finish, deadline, prefix, st, ret, None, constraints_traveler(constraints)))


def constraints_traveler(constraints) -> str:
    return "driving" if constraints.start_in_car else "on_foot"


def _loc_key(p) -> str:
    return f"{p[0]:.3f},{p[1]:.3f}"


def _build(points, picks, sched, bad, depart, finish, deadline, order_prefix, st, ret, car_loc, start_traveler="on_foot") -> dict:
    legs, notes, schedule = [], [], []
    for i, c in enumerate(picks):
        for l in c["legs"]:
            legs.append({**l, "segment": i, "cand_id": _eff_id(c) if l["mode"] == "car" else c["id"]})
        for note in c.get("notes", []):
            notes.append(f"Leg {LETTERS[points[i]['idx']]} to {LETTERS[points[i + 1]['idx']]}: {note}")
    schedule.append({"label": LETTERS[points[0]["idx"]], "name": points[0]["name"], "kind": "origin", "arrive": None,
                     "depart": _hm(depart), "dwell_min": 0.0, "wait_min": 0.0, "arrived_as": None,
                     "traveler": start_traveler})
    for i, e in enumerate(sched):
        last = i == len(sched) - 1
        p = points[i + 1]
        schedule.append({"label": LETTERS[p["idx"]], "name": p["name"], "kind": "destination" if last else p["type"],
                         "arrive": _hm(e["arrive"]), "wait_min": e["wait_min"], "dwell_min": e["dwell_min"],
                         "depart": None if last else _hm(e["depart"]), "window": p.get("window"),
                         "arrived_as": e["arrived_as"], "traveler": e["state"]["traveler"], "car": _car_text(e["state"], points)})
    total_arrive = finish
    arrive_dt = finish
    segment = len(picks)
    if ret:
        for l in ret["legs"]:
            legs.append({**l, "segment": segment, "cand_id": ret["id"]})
        arrive_dt = finish + timedelta(minutes=ret["duration_min"])
        notes.append(f"Return to your car: {ret['title']}")
        schedule.append({"label": LETTERS[len(points)], "name": "Your car", "kind": "car", "arrive": _hm(arrive_dt), "wait_min": 0.0,
                         "dwell_min": 0.0, "depart": None, "window": None, "arrived_as": _arrived_as(ret), "traveler": "on_foot", "car": "with you"})
    picks_all = list(picks) + ([ret] if ret else [])
    total_min = (arrive_dt - depart).total_seconds() / 60
    rels = [c["reliability"] for c in picks if c.get("reliability")]
    cand = {
        "_picks": picks,
        "id": order_prefix + "|".join(c["id"] for c in picks) + (f"|return:{ret['id']}" if ret else ""),
        "title": " then ".join(c["title"] for c in picks_all),
        "available": True, "legs": legs, "schedule": schedule,
        "duration_min": round(total_min, 1), "cost": round(sum(c["cost"] for c in picks_all), 2),
        "co2_kg": round(sum(c["co2_kg"] for c in picks_all), 3), "transfers": sum(c["transfers"] for c in picks_all),
        "walk_m": sum(c["walk_m"] for c in picks_all),
        "bag_friendliness": round(min(c.get("bag_friendliness", 1.0) for c in picks_all), 2),
        "depart": _hm(depart), "arrive": _hm(arrive_dt),
        "notes": notes + ["Later legs are timed from the estimated arrival at each stop (ESTIMATED)"],
    }
    viol = list(bad) + [v for c in picks_all for v in (c.get("violates") or [])]
    if _teleports(legs, points):
        viol.append("teleport")  # no teleporting: every leg must start where the previous one ended
    if viol:
        cand["violates"] = sorted(set(viol))
        cand["notes"] += [f"Not allowed: {v}" for v in cand["violates"] if v.startswith("car_left_behind")]
    if deadline:
        cand["meets_deadline"] = total_arrive <= deadline
        cand["leave_by"] = _hm(deadline - timedelta(minutes=(total_arrive - depart).total_seconds() / 60))
    scoreable = [c for c in picks if any(l.get("route_id") or l.get("timing") in ("road", "bus_est") for l in c["legs"])]
    if len(rels) == len(scoreable) and rels:
        p = 1.0
        for r in rels:
            p *= r["reliability_pct"] / 100
        history_days = min(r["history_days"] for r in rels)
        cand["reliability"] = {"reliability_pct": round(100 * p), "history_days": history_days,
                               "basis": "product of per-leg on-time chances (ESTIMATED, assumes legs are independent)",
                               "confidence": reliability.confidence_label(history_days),
                               "delay_factors": [f for r in rels for f in (r.get("delay_factors") or [])]}
    return cand


def _teleports(legs: list[dict], points: list[dict]) -> bool:
    """True if the legs do not form one connected path from the first point to the last (gap above NEAR_KM)."""
    if not legs:
        return False
    ends = [(tuple(a["to"]), tuple(b["from"])) for a, b in zip(legs, legs[1:])]
    ends.append(((points[0]["lat"], points[0]["lon"]), tuple(legs[0]["from"])))
    return any(haversine_km(a, b) > NEAR_KM for a, b in ends)


def _limit(leg_sets: list[list[dict]]) -> list[list[dict]]:
    """Keep the number of chained combinations bounded: if too many, trim each leg to its best few."""
    if math.prod(len(s) for s in leg_sets) <= MAX_COMBOS:
        return leg_sets
    return [_prune(s, keep=5) for s in leg_sets]


# -- planning ----------------------------------------------------------------------------------------------------------
_cache: dict[str, tuple[float, asyncio.Task]] = {}


def validate_request(origin: dict, stops: list[dict], destination: dict, constraints: planner.Constraints) -> None:
    """Raise TripImpossible for logistics that cannot work, before any routing is done."""
    pts = [origin] + stops + [destination]
    if origin.get("type") in ("dropoff", "park", "alight"):
        raise TripImpossible(f"'{origin['name']}' is where you start, so you cannot be dropped off, park or get off there")
    if destination.get("type") == "pickup":
        raise TripImpossible(f"You said you are picked up at '{destination['name']}', which is where the trip ends. Where should the ride take you?")
    if any(p.get("type") == "park" for p in pts) and not constraints.has_car:
        raise TripImpossible("You said to park somewhere, but you have not said you have a car")
    if any(p.get("type") == "alight" for p in pts[:1]):
        raise TripImpossible("You cannot get off a vehicle at the place you start")


async def plan_trip(origin: dict, stops: list[dict], destination: dict, constraints: planner.Constraints,
                    optimize_order: bool = False) -> dict:
    """origin/destination: {name, lat, lon[, type]}; stops: [{name, lat, lon, type, dwell_min, window}] in the order typed.
    Identical requests inside CACHE_TTL_S share one computation (the engine and the page every bot operates must
    show the same set, and four bots would otherwise recompute the same trip four times)."""
    if len(stops) > MAX_STOPS:
        raise TripImpossible(f"At most {MAX_STOPS} stops are supported")
    if optimize_order and len(stops) > MAX_STOPS_REORDER:
        raise TripImpossible(f"Reordering supports at most {MAX_STOPS_REORDER} stops; keep your order or remove some stops")
    validate_request(origin, stops, destination, constraints)
    key = json.dumps([origin, stops, destination, constraints.model_dump(), optimize_order], sort_keys=True, default=str)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S and not (hit[1].done() and hit[1].exception()):
        return await hit[1]
    for k in [k for k, v in _cache.items() if time.time() - v[0] >= CACHE_TTL_S]:
        _cache.pop(k, None)
    task = asyncio.ensure_future(_plan_trip(origin, stops, destination, constraints, optimize_order))
    _cache[key] = (time.time(), task)
    return await task


async def _leg_candidates(a: dict, b: dict, constraints: planner.Constraints, depart: datetime, final: bool,
                          forced_lift: bool = False, modes: list[str] | None = None) -> tuple[list[dict], dict]:
    c = constraints.model_copy(update={
        "depart_at": _hm(depart), "arrive_by": constraints.arrive_by if final else None,
        "stay_hours": (b["dwell_min"] / 60 if b["dwell_min"] and not final else constraints.stay_hours)})
    if forced_lift:
        c = c.model_copy(update={"modes": [planner.LIFT]})
    elif modes is not None:
        c = c.model_copy(update={"modes": modes})
    plan = await planner.plan((a["lat"], a["lon"]), (b["lat"], b["lon"]), c)
    return plan["candidates"], plan


def _wants_lift(a: dict, b: dict) -> bool:
    return a["type"] == "pickup" or b["type"] == "dropoff"


async def _exit_leg(origin: dict, exit_pt: dict, depart: datetime) -> dict:
    """Starting in a car on a controlled-access highway: drive to the next valid exit. Fuel only (no parking, no fare)."""
    g = await osm.leg("drive", (origin["lat"], origin["lon"]), (exit_pt["lat"], exit_pt["lon"]))
    dur = g["duration_s"] * traffic_multiplier(depart.hour)
    return {"id": "drive_exit", "title": f"Drive to {exit_pt['name']}", "available": True, "duration_min": round(dur / 60, 1),
            "cost": fares.driving_cost(g["distance_km"], False), "co2_kg": fares.emissions_kg("car", g["distance_km"]), "transfers": 0, "walk_m": 0,
            "bag_friendliness": 1.0, "notes": [f"You start in your car on {origin['highway']}: first drive to the next valid exit before any other mode (ESTIMATE of the exit)"],
            "legs": [{"mode": "car", "label": f"Drive on {origin['highway']} to {exit_pt['name']}", "from": [origin["lat"], origin["lon"]],
                      "to": [exit_pt["lat"], exit_pt["lon"]], "distance_km": g["distance_km"], "duration_s": dur, "geometry": g["geometry"],
                      "timing": "road", "base_s": g["duration_s"],
                      "provenance": g.get("provenance", "estimated"), "cost": fares.driving_cost(g["distance_km"], False),
                      "co2_kg": fares.emissions_kg("car", g["distance_km"])}]}


async def _fetch_composites(i: int, points: list[dict], leg_set_before: list[list[dict]], constraints, est: datetime, final: bool) -> list[dict]:
    """For a leg that must be driven (park at X) after the car may have been left behind: for each place P the car
    could be, go back to P without a car, then drive P -> X. Only built when the user said Yes to returning to the car."""
    x, nxt = points[i], points[i + 1]
    locs: list[dict] = []
    for j in range(i):
        locs.append(points[j])
    for sets in leg_set_before:
        for c in sets:
            if c["id"] == "park_ride":
                lot = _car_loc(c)
                if lot and not any(_near(lot, (p["lat"], p["lon"])) for p in locs):
                    locs.append({"name": "the park & ride lot", "lat": lot[0], "lon": lot[1], "dwell_min": 0.0})
    out: list[dict] = []
    jobs = []
    for p in locs:
        if _near((p["lat"], p["lon"]), (x["lat"], x["lon"])):
            continue
        jobs.append(p)
    results = await asyncio.gather(*[
        asyncio.gather(_leg_candidates(x, p, constraints, est, False, modes=NON_CAR_MODES),
                       _leg_candidates(p, nxt, constraints, est, final, modes=["drive"])) for p in jobs])
    for p, ((fetch, _), (car, _)) in zip(jobs, results):
        fetch_best = _prune(fetch, keep=3)[:3]
        car_d = next((c for c in car if c["id"] == "drive" and c.get("available")), None)
        if not car_d:
            continue
        for f in fetch_best:
            legs = [{**l, "to": l["to"]} for l in f["legs"]] + list(car_d["legs"])
            out.append({"id": f"fetch:{f['id']}+drive", "car_id": "drive", "fetch_car_at": (p["lat"], p["lon"]),
                        "title": f"Go back to your car ({f['title']}), then drive", "available": True, "legs": legs,
                        "duration_min": round(f["duration_min"] + car_d["duration_min"], 1), "cost": round(f["cost"] + car_d["cost"], 2),
                        "co2_kg": round(f["co2_kg"] + car_d["co2_kg"], 3), "transfers": f["transfers"] + car_d["transfers"],
                        "walk_m": f["walk_m"] + car_d["walk_m"], "bag_friendliness": min(f.get("bag_friendliness", 1.0), car_d.get("bag_friendliness", 1.0)),
                        "notes": [f"Goes back to your car at {p['name']} first (you asked to plan the return to it)"] + list(car_d.get("notes", []))})
    return out


async def _return_options(points: list[dict], leg_sets: list[list[dict]], constraints, depart: datetime) -> dict:
    """Options for getting from the destination back to each place the car could end up (non-car modes)."""
    dest = points[-1]
    locs: dict[str, dict] = {}
    for p in points[:-1]:
        locs[_loc_key((p["lat"], p["lon"]))] = p
    for sets in leg_sets:
        for c in sets:
            if c["id"] == "park_ride":
                lot = _car_loc(c)
                if lot:
                    locs.setdefault(_loc_key(lot), {"name": "the park & ride lot", "lat": lot[0], "lon": lot[1], "dwell_min": 0.0})
    keys = [k for k, p in locs.items() if not _near((p["lat"], p["lon"]), (dest["lat"], dest["lon"]))]
    res = await asyncio.gather(*[_leg_candidates(dest, locs[k], constraints, depart, False, modes=NON_CAR_MODES) for k in keys])
    return {k: _prune(r[0], keep=3)[:3] for k, r in zip(keys, res)}


async def _plan_trip(origin, stops, destination, constraints, optimize_order) -> dict:
    now = planner._now(constraints)
    depart = _clock(constraints.depart_at, now) if constraints.depart_at else now.replace(second=0, microsecond=0)

    def point(p: dict, idx: int, kind: str) -> dict:
        typ = p.get("type") if p.get("type") in ("pickup", "dropoff", "park", "alight") or kind == "stop" else kind
        return {"name": p["name"], "lat": p["lat"], "lon": p["lon"], "type": typ or kind,
                "dwell_min": 0.0 if p.get("type") == "via" else float(p.get("dwell_min") or 0), "window": p.get("window"),
                "vehicle": p.get("vehicle"), "idx": idx}

    pts_in = [point(origin, 0, "origin")]
    exit_pt = None
    hw = highways.on_highway(origin["lat"], origin["lon"]) if constraints.start_in_car else None
    if hw:
        ex = highways.next_exit((origin["lat"], origin["lon"]), (destination["lat"], destination["lon"]))
        if not ex:
            raise TripImpossible(f"You start in your car on {hw}, but no exit toward your destination was found in the highway data")
        origin = {**origin, "highway": hw}
        exit_pt = {"name": ex["name"], "lat": ex["lat"], "lon": ex["lon"], "type": "exit", "dwell_min": 0.0, "window": None, "idx": 1}
        pts_in.append(exit_pt)
    base = len(pts_in)
    pts_in += [point(s, base + i, "stop") for i, s in enumerate(stops)]
    pts_in.append(point(destination, len(pts_in), "destination"))
    last = len(pts_in) - 1
    stop_idx = list(range(base, last))
    orders = [stop_idx]
    if optimize_order and len(stops) > 1:
        orders = [list(p) for p in itertools.permutations(stop_idx)]
    head = [0] + ([1] if exit_pt else [])
    trips: list[dict] = []
    plans: list[dict] = []

    async def leg_set(a_i: int, b_i: int, est: datetime, final: bool) -> list[dict]:
        a, b = pts_in[a_i], pts_in[b_i]
        if exit_pt and (a_i, b_i) == (0, 1):
            return [await _exit_leg(origin, exit_pt, est)]
        cands, plan = await _leg_candidates(a, b, constraints, est, final, forced_lift=_wants_lift(a, b))
        plans.append(plan)
        return _prune(cands)

    if len(orders) == 1:
        seq = head + orders[0] + [last]
        leg_sets, est = [], depart
        for i in range(len(seq) - 1):
            b = pts_in[seq[i + 1]]
            leg_sets.append(await leg_set(seq[i], seq[i + 1], est, i == len(seq) - 2))
            if not leg_sets[-1]:
                break
            fastest = min(c["duration_min"] for c in leg_sets[-1])
            est = est + timedelta(minutes=fastest + (b["dwell_min"] if i < len(seq) - 2 else 0))
            if b.get("window"):
                est = max(est, _clock(b["window"][0], depart))
        if len(leg_sets) == len(seq) - 1 and all(leg_sets):
            sp = [pts_in[i] for i in seq]
            if constraints.plan_car_return:
                est2 = depart
                for i in range(len(leg_sets)):
                    if sp[i + 1]["type"] == "park" and i > 0:
                        leg_sets[i] = leg_sets[i] + await _fetch_composites(i, sp, leg_sets[:i], constraints, est2, i == len(leg_sets) - 1)
                    est2 = est2 + timedelta(minutes=min(c["duration_min"] for c in leg_sets[i]) + sp[i + 1]["dwell_min"])
            returns = await _return_options(sp, leg_sets, constraints, depart) if (constraints.plan_car_return and constraints.car_needed_after) else None
            trips = _combine(sp, _limit(leg_sets), constraints, depart, returns=returns)
    else:
        # reordering: every directed pair is planned once, at the trip's departure hour (ESTIMATED), in parallel
        need = sorted({(a, b) for o in orders for a, b in zip(head + o, o + [last])} - ({(0, 1)} if exit_pt else set()))
        results = await asyncio.gather(*[leg_set(a, b, depart, b == last) for a, b in need])
        by_pair = dict(zip(need, results))
        if exit_pt:
            by_pair[(0, 1)] = [await _exit_leg(origin, exit_pt, depart)]
        for o in orders:
            seq = head + o + [last]
            sets = [by_pair[(a, b)] for a, b in zip(seq, seq[1:])]
            if all(sets):
                prefix = "".join(LETTERS[pts_in[i]["idx"]] for i in seq) + ":"
                sp = [pts_in[i] for i in seq]
                returns = await _return_options(sp, sets, constraints, depart) if (constraints.plan_car_return and constraints.car_needed_after) else None
                trips += _combine(sp, _limit(sets), constraints, depart, prefix, returns)

    pareto.mark(trips)
    base_plan = plans[0] if plans else {}
    return {"origin": [origin["lat"], origin["lon"]], "destination": [destination["lat"], destination["lon"]],
            "depart": _hm(depart), "candidates": trips, "traffic_multiplier": base_plan.get("traffic_multiplier", 1.0),
            "points": [{"label": LETTERS[p["idx"]], "name": p["name"], "lat": p["lat"], "lon": p["lon"], "type": p["type"],
                        "dwell_min": p["dwell_min"], "window": p.get("window")} for p in pts_in],
            "multi_stop": True, "optimize_order": bool(optimize_order and len(stops) > 1),
            "data_notes": (base_plan.get("data_notes") or []) + ["Multi-stop: each leg is routed separately; legs after the first are timed from estimated arrivals (ESTIMATED)"]}
