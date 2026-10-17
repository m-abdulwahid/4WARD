"""Turns (origin, destination, modes, constraints) into scored itineraries.

Numbers (time/cost/emissions) are computed here, deterministically. LLM bots only decide
WHICH modes/constraints to try; they never produce route numbers.
"""
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from . import bikeshare, fares, osm, transit
from .config import SPEEDS_KMH, traffic_multiplier
from .geo import haversine_km

ALL_MODES = ["walk", "bike", "ebike", "bikeshare", "drive", "transit", "park_ride"]
NEEDS = {"bike": "has_bike", "ebike": "has_ebike", "drive": "has_car", "park_ride": "has_car"}

PARK_RIDE_LOTS = [
    ("Finch Station", 43.7807, -79.4148), ("Kennedy Station", 43.7323, -79.2637),
    ("Kipling Station", 43.6372, -79.5361), ("Don Mills Station", 43.7756, -79.3463),
    ("Wilson Station", 43.7342, -79.4504), ("Islington Station", 43.6454, -79.5245),
    ("Downsview Park Station", 43.7534, -79.4784),
]


class Constraints(BaseModel):
    modes: list[str] = Field(default_factory=lambda: list(ALL_MODES))
    depart_at: str | None = None       # "HH:MM" local time, default now
    arrive_by: str | None = None       # "HH:MM"
    bags: int = 0
    no_presto: bool = False
    has_car: bool = False
    ev: bool = False
    has_bike: bool = False
    has_ebike: bool = False
    avoid_stairs: bool = False
    max_walk_m: int | None = None
    stay_hours: float | None = None
    rides_this_week: int = 0


def _hhmm(s: str | None, base: datetime) -> datetime | None:
    if not s:
        return None
    h, m = s.split(":")[:2]
    t = base.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
    return t


def _leg(mode, label, a, b, g: dict, cost=0.0, co2=0.0, dur=None, extra=None):
    return {"mode": mode, "label": label, "from": list(a), "to": list(b),
            "distance_km": g["distance_km"], "duration_s": g["duration_s"] if dur is None else dur,
            "geometry": g["geometry"], "provenance": g.get("provenance", "estimated"),
            "cost": round(cost, 2), "co2_kg": round(co2, 3), **(extra or {})}


def _finish(cand_id: str, title: str, legs: list[dict], c: Constraints, depart: datetime, hour: int,
            transfers: int, notes: list[str], extra_cost=0.0, extra: dict | None = None) -> dict:
    dur_s = sum(l["duration_s"] for l in legs)
    cost = round(sum(l.get("cost", 0) for l in legs) + extra_cost, 2)
    co2 = round(sum(l.get("co2_kg", 0) for l in legs), 3)
    walk_m = round(sum(l["distance_km"] for l in legs if l["mode"] == "walk") * 1000)
    biking_m = round(sum(l["distance_km"] for l in legs if l["mode"] in ("bike", "ebike", "bikeshare")) * 1000)
    # bag friendliness 0..1 (1 = easy). Heuristic, labeled estimate.
    bag = 1.0
    if c.bags:
        bag -= min(0.6, walk_m / 2500) + 0.12 * transfers
        if biking_m:
            bag -= 0.5
        if any(l["mode"] == "subway" for l in legs) and c.avoid_stairs:
            bag -= 0.1
    bag = round(max(0.0, min(1.0, bag)), 2)
    arrive = depart + timedelta(seconds=dur_s)
    cand = {
        "id": cand_id, "title": title, "legs": legs, "duration_min": round(dur_s / 60, 1),
        "cost": cost, "co2_kg": co2, "transfers": transfers, "walk_m": walk_m,
        "bag_friendliness": bag, "depart": depart.strftime("%H:%M"), "arrive": arrive.strftime("%H:%M"),
        "notes": notes, "available": True,
    }
    if c.max_walk_m is not None and walk_m > c.max_walk_m:
        cand["notes"] = notes + [f"Walking {walk_m} m exceeds your {c.max_walk_m} m limit"]
        cand["violates"] = ["max_walk"]
    ab = _hhmm(c.arrive_by, depart)
    if ab:
        cand["meets_deadline"] = arrive <= ab
        cand["leave_by"] = (ab - timedelta(seconds=dur_s)).strftime("%H:%M")
    cand.update(extra or {})
    return cand


def _unavailable(cid, title, why):
    return {"id": cid, "title": title, "available": False, "reason": why, "legs": [], "duration_min": None,
            "cost": None, "co2_kg": None, "transfers": None, "walk_m": None}


async def plan(origin: tuple[float, float], dest: tuple[float, float], c: Constraints) -> dict:
    now = datetime.now()
    depart = _hhmm(c.depart_at, now) or now
    hour = depart.hour
    tm = traffic_multiplier(hour)
    out: list[dict] = []
    modes = [m for m in c.modes if m in ALL_MODES]

    for m in modes:
        flag = NEEDS.get(m)
        if flag and not getattr(c, flag):
            label = {"has_car": "a car", "has_bike": "a bike", "has_ebike": "an e-bike"}[flag]
            out.append(_unavailable(m, m, f"Requires {label} (toggle it in constraints)"))
            continue

        if m == "walk":
            g = await osm.leg("walk", origin, dest)
            out.append(_finish("walk", "Walk", [_leg("walk", "Walk", origin, dest, g)], c, depart, hour, 0,
                               ["No cost, no emissions"] + (["Long walk with bags"] if c.bags and g["distance_km"] > 1.5 else [])))

        elif m in ("bike", "ebike"):
            g = await osm.leg("bike", origin, dest)
            dur = g["duration_s"] * (SPEEDS_KMH["bike"] / SPEEDS_KMH["ebike"] if m == "ebike" else 1)
            if m == "ebike":
                cost = g["distance_km"] * 0.02
                co2 = fares.emissions_kg("ebike", g["distance_km"])
            else:
                cost, co2 = 0.0, 0.0
            lg = _leg(m, "Ride your e-bike" if m == "ebike" else "Ride your bike", origin, dest, g, cost, co2, dur)
            out.append(_finish(m, "E-bike" if m == "ebike" else "Bike", [lg], c, depart, hour, 0,
                               ["Uses bike lanes/low-stress streets where OSM maps them"] + (["Hard to carry bags"] if c.bags else [])))

        elif m == "bikeshare":
            snap = await bikeshare.stations()
            st = snap["stations"]
            s1 = bikeshare.nearest(st, *origin, need="bikes")
            s2 = bikeshare.nearest(st, *dest, need="docks")
            if not s1 or not s2:
                out.append(_unavailable("bikeshare", "Bike Share", "No Bike Share station with bikes/docks nearby"))
                continue
            p1, p2 = (s1["lat"], s1["lon"]), (s2["lat"], s2["lon"])
            w1, rd, w2 = await osm.leg("walk", origin, p1), await osm.leg("bike", p1, p2), await osm.leg("walk", p2, dest)
            ride_min = rd["duration_s"] / 60
            use_e = s1["ebikes"] > 0
            dur = rd["duration_s"] * (SPEEDS_KMH["bike"] / SPEEDS_KMH["ebike"] if use_e else 1)
            legs = [
                _leg("walk", f"Walk to {s1['name']}", origin, p1, w1),
                _leg("bikeshare", f"{'E-bike' if use_e else 'Bike'} Share {s1['name']} → {s2['name']}", p1, p2, rd,
                     fares.bikeshare_cost(dur / 60, use_e), fares.emissions_kg("bikeshare_ebike" if use_e else "bikeshare", rd["distance_km"]), dur),
                _leg("walk", "Walk to destination", p2, dest, w2),
            ]
            out.append(_finish("bikeshare", "Bike Share", legs, c, depart, hour, 0,
                               [f"{s1['bikes']} bikes ({s1['ebikes']} e-bikes) at pickup, {s2['docks']} free docks at drop-off ({snap['provenance']} data)"],
                               extra={"data_source": f"bikeshare:{snap['provenance']}"}))

        elif m == "drive":
            g = await osm.leg("drive", origin, dest)
            dur = g["duration_s"] * tm
            fuel = fares.driving_cost(g["distance_km"], c.ev)
            park = fares.parking_cost(dest[0], dest[1], c.stay_hours)
            prob = fares.parking_find_probability(dest[0], dest[1], hour)
            co2 = fares.emissions_kg("ev" if c.ev else "car", g["distance_km"])
            lg = _leg("car", f"Drive ({'EV' if c.ev else 'gas'})", origin, dest, g, fuel + park, co2, dur)
            lg["cost_breakdown"] = {"fuel_or_charge_and_wear": fuel, "parking": park}
            out.append(_finish("drive", "Drive", [lg], c, depart, hour, 0,
                               [f"Parking ${park:.2f} (estimated rate), {int(prob*100)}% chance of finding a spot quickly (estimate)",
                                f"Traffic factor x{tm} for {hour:02d}:00 (typical-day estimate, not live)"],
                               extra={"parking_find_probability": prob}))

        elif m == "transit":
            for opt in (await transit.rail_option(origin, dest, hour), await transit.bus_option(origin, dest, hour)):
                if not opt:
                    continue
                legs = opt["legs"]
                total_min = sum(l["duration_s"] for l in legs) / 60
                fare = fares.transit_trip_fare(opt["boardings"], total_min, not c.no_presto, c.rides_this_week)
                for l in legs:
                    if l["mode"] in ("subway", "lrt_streetcar", "bus"):
                        l["co2_kg"] = fares.emissions_kg(l["mode"], l["distance_km"])
                        l["cost"] = 0.0
                    l.setdefault("co2_kg", 0.0)
                    l.setdefault("cost", 0.0)
                first_ride = next(l for l in legs if l["mode"] != "walk")
                first_ride["cost"] = fare
                for l in legs:
                    if l["mode"] in ("bus",):
                        l["duration_s"] = l["duration_s"]  # estimated bus already includes traffic factor
                kind = "rail" if opt["kind"] == "transit_rail" else "bus"
                notes = [("Presto" if not c.no_presto else "Cash fare") + f" ${fare:.2f}" + (", transfers free within 2 h" if opt["boardings"] > 1 else "")]
                if kind == "bus":
                    notes.append("Bus leg is an ESTIMATE (no stop-level schedule routing)")
                out.append(_finish(f"transit_{kind}", f"TTC {'subway/streetcar' if kind == 'rail' else 'bus'}", legs, c, depart, hour,
                                   max(0, opt["boardings"] - 1), notes))

        elif m == "park_ride":
            lot = min(PARK_RIDE_LOTS, key=lambda p: haversine_km(origin, (p[1], p[2])) + 0.6 * haversine_km((p[1], p[2]), dest) * 0 )
            # choose the lot closest to origin that lies roughly toward the destination
            cands = [p for p in PARK_RIDE_LOTS if haversine_km((p[1], p[2]), dest) < haversine_km(origin, dest)]
            lot = min(cands or PARK_RIDE_LOTS, key=lambda p: haversine_km(origin, (p[1], p[2])))
            lp = (lot[1], lot[2])
            d1 = await osm.leg("drive", origin, lp)
            opt = await transit.rail_option(lp, dest, hour)
            if not opt:
                out.append(_unavailable("park_ride", "Park & ride", "No rail option from the nearest commuter lot"))
                continue
            dur = d1["duration_s"] * tm
            drive_cost = fares.driving_cost(d1["distance_km"], c.ev) + float(fares.A["ttc_lot_parking_day"]["value"])
            legs = [_leg("car", f"Drive to {lot[0]} lot", origin, lp, d1, drive_cost,
                         fares.emissions_kg("ev" if c.ev else "car", d1["distance_km"]), dur)] + opt["legs"][1:]
            total_min = sum(l["duration_s"] for l in legs) / 60
            fare = fares.transit_trip_fare(opt["boardings"], total_min - dur / 60, not c.no_presto, c.rides_this_week)
            for l in legs[1:]:
                l.setdefault("co2_kg", 0.0)
                l.setdefault("cost", 0.0)
                if l["mode"] in ("subway", "lrt_streetcar"):
                    l["co2_kg"] = fares.emissions_kg(l["mode"], l["distance_km"])
            next(l for l in legs[1:] if l["mode"] != "walk")["cost"] = fare
            out.append(_finish("park_ride", "Park & ride", legs, c, depart, hour, max(0, opt["boardings"] - 1),
                               [f"Park at {lot[0]} (lot fee is an estimate), then TTC ${fare:.2f}"]))

    return {"origin": list(origin), "destination": list(dest), "depart": depart.strftime("%H:%M"),
            "candidates": out, "traffic_multiplier": tm,
            "data_notes": ["Road/walk/bike legs: OpenStreetMap routing", "Transit: TTC GTFS static line graph (headways estimated)",
                           "Traffic: typical-hour estimate, not live"]}
