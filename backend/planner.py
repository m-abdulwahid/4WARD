"""Turns (origin, destination, modes, constraints) into scored itineraries.

Numbers (time/cost/emissions) are computed here, deterministically. LLM bots only decide
WHICH modes/constraints to try; they never produce route numbers.
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from . import bikeshare, fares, osm, pareto, reliability, transit
from .config import SPEEDS_KMH, traffic_multiplier
from .geo import haversine_km

TORONTO = ZoneInfo("America/Toronto")
ALL_MODES = ["walk", "bike", "ebike", "bikeshare", "drive", "rideshare", "transit", "park_ride"]
NEEDS = {"bike": "has_bike", "ebike": "has_ebike", "drive": "has_car", "park_ride": "has_car"}

PARK_RIDE_LOTS = [
    ("Finch Station", 43.7807, -79.4148, "subway"), ("Kennedy Station", 43.7323, -79.2637, "subway"),
    ("Kipling Station", 43.6372, -79.5361, "subway"), ("Don Mills Station", 43.7756, -79.3463, "subway"),
    ("Wilson Station", 43.7342, -79.4504, "subway"), ("Islington Station", 43.6454, -79.5245, "subway"),
    ("Downsview Park Station", 43.7534, -79.4784, "subway"),
    ("Milton GO", 43.52364, -79.867172, "go_train"), ("Oakville GO", 43.455594, -79.682242, "go_train"),
    ("Richmond Hill GO", 43.874307, -79.426167, "go_train"), ("Unionville GO", 43.851689, -79.314332, "go_train"),
    ("Durham College Oshawa GO", 43.870909, -78.88585, "go_train"),
]  # curated shortlist for the manual picker UI only -- the engine's own auto choice searches every station (below)

PARK_RIDE_SEARCH_N = 10  # how many of the nearest real stations get a real drive+transit quote when picking automatically


async def _best_park_ride_station(origin: tuple[float, float], dest: tuple[float, float], hour: int,
                                   tm: float) -> tuple[str, float, float, str] | None:
    """Pick the station that actually minimizes total trip time, searched over every real TTC subway
    AND GO train station (not a hand-picked shortlist of 7) -- a true discrete optimum, not a
    nearest-neighbour guess. Bounded to the PARK_RIDE_SEARCH_N most promising-by-straight-line
    candidates so a request stays fast; each of those gets a real OSRM drive time and a real Dijkstra
    transit time, not an estimate."""
    all_stations = transit.interchange_stations()
    toward = [s for s in all_stations if haversine_km((s[1], s[2]), dest) < haversine_km(origin, dest)]
    pool = sorted(toward or all_stations, key=lambda s: haversine_km(origin, (s[1], s[2])))[:PARK_RIDE_SEARCH_N]

    async def _try(s: tuple[str, float, float, str]):
        sp = (s[1], s[2])
        d, opt = await asyncio.gather(osm.leg("drive", origin, sp), transit.rail_option(sp, dest, hour))
        if not opt:
            return None
        total = d["duration_s"] * tm + sum(l["duration_s"] for l in opt["legs"])
        return (total, s)

    results = [r for r in await asyncio.gather(*(_try(s) for s in pool)) if r]
    return min(results, key=lambda r: r[0])[1] if results else None


class Constraints(BaseModel):
    modes: list[str] = Field(default_factory=lambda: list(ALL_MODES))
    depart_at: str | None = None       # "HH:MM" local time, default now
    arrive_by: str | None = None       # "HH:MM"
    park_ride_lot: str | None = None   # overrides the auto-picked "nearest lot" (see PARK_RIDE_LOTS)
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
            bag -= 0.75
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


def _apply_transit_fare(legs: list[dict], c: Constraints, boardings: int, trip_minutes: float) -> list[str]:
    """Mutates legs in place: sets co2_kg on every rail/bus leg, and cost on one leg per fare system
    actually present. A single itinerary can now legitimately mix GO and TTC legs, since they share
    one line graph (transit.py) -- that was never possible with TTC-only data. No PRESTO combined-trip
    discount is modeled for a GO+TTC trip; that is flagged in a note rather than guessed at."""
    go_legs = [l for l in legs if l["mode"] == "go_train"]
    ttc_legs = [l for l in legs if l["mode"] in ("subway", "lrt_streetcar", "bus")]
    for l in go_legs:
        l["co2_kg"] = fares.emissions_kg("go_train", l["distance_km"])
    for l in ttc_legs:
        l["co2_kg"] = fares.emissions_kg(l["mode"], l["distance_km"])
    for l in legs:
        l.setdefault("co2_kg", 0.0)
        l.setdefault("cost", 0.0)
    notes = []
    if go_legs:
        go_total = fares.go_fare(sum(l["distance_km"] for l in go_legs))
        go_legs[0]["cost"] = go_total
        notes.append(f"GO fare ${go_total:.2f} is an ESTIMATE (distance-based PRESTO fare, verify)")
    if ttc_legs:
        ttc_boardings = max(1, boardings - (1 if go_legs else 0))
        ttc_total = fares.transit_trip_fare(ttc_boardings, trip_minutes, not c.no_presto, c.rides_this_week)
        ttc_legs[0]["cost"] = ttc_total
        notes.append(("Presto" if not c.no_presto else "Cash fare") + f" ${ttc_total:.2f}" +
                     (", transfers free within 2 h" if ttc_boardings > 1 else ""))
    if go_legs and ttc_legs:
        notes.append("GO and TTC fares shown separately; PRESTO's combined-trip discount is not modeled")
    return notes


def _unavailable(cid, title, why):
    return {"id": cid, "title": title, "available": False, "reason": why, "legs": [], "duration_min": None,
            "cost": None, "co2_kg": None, "transfers": None, "walk_m": None}


async def plan(origin: tuple[float, float], dest: tuple[float, float], c: Constraints) -> dict:
    # Must be Toronto wall-clock time, not server-local: on a UTC host (Railway) datetime.now() is
    # 4-5 hours off, which silently breaks rush-hour traffic_multiplier() and reliability dow/hour matching.
    now = datetime.now(TORONTO)
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

        elif m == "rideshare":
            g = await osm.leg("drive", origin, dest)
            dur = g["duration_s"] * tm + 4 * 60  # + pickup wait
            fare = fares.rideshare_cost(g["distance_km"], dur / 60)
            lg = _leg("car", "Rideshare / taxi (fare estimated)", origin, dest, g, fare, fares.emissions_kg("car", g["distance_km"]), dur)
            out.append(_finish("rideshare", "Rideshare", [lg], c, depart, hour, 0,
                               [f"Fare ${fare:.2f} is an ESTIMATE (no surge pricing modeled)", "Door to door, easy with bags"]))

        elif m == "transit":
            rail = await transit.rail_option(origin, dest, hour)
            low = await transit.rail_option(origin, dest, hour, walk_weight=3.0)
            options = [rail]
            def _walk(o):
                return sum(l["distance_km"] for l in o["legs"] if l["mode"] == "walk")

            if low and rail and low["signature"] != rail["signature"] and _walk(low) <= _walk(rail) - 0.15:
                low["kind"] = "transit_rail_lowwalk"
                options.append(low)
            options.append(await transit.bus_option(origin, dest, hour))
            for opt in options:
                if not opt:
                    continue
                legs = opt["legs"]
                total_min = sum(l["duration_s"] for l in legs) / 60
                notes = _apply_transit_fare(legs, c, opt["boardings"], total_min)
                kind = {"transit_rail": "rail", "transit_rail_lowwalk": "rail_lowwalk"}.get(opt["kind"], "bus")
                if kind == "bus":
                    notes.append("Bus leg is an ESTIMATE (no stop-level schedule routing)")
                has_go = any(l["mode"] == "go_train" for l in legs)
                has_ttc_rail = any(l["mode"] in ("subway", "lrt_streetcar") for l in legs)
                if kind == "bus":
                    title = "TTC bus"
                elif has_go and has_ttc_rail:
                    title = "GO + TTC rail"
                elif has_go:
                    title = "GO Train"
                else:
                    title = {"rail": "TTC subway/streetcar", "rail_lowwalk": "TTC subway (less walking)"}[kind]
                out.append(_finish(f"transit_{kind}", title, legs, c, depart, hour, max(0, opt["boardings"] - 1), notes))

        elif m == "park_ride":
            chosen_lot = next((p for p in PARK_RIDE_LOTS if p[0] == c.park_ride_lot), None) if c.park_ride_lot else None
            lot = chosen_lot or await _best_park_ride_station(origin, dest, hour, tm)
            if not lot:
                out.append(_unavailable("park_ride", "Park & ride", "No usable park & ride station found"))
                continue
            name, lat, lon, kind = lot
            lp = (lat, lon)
            d1 = await osm.leg("drive", origin, lp)
            opt = await transit.rail_option(lp, dest, hour)
            if not opt:
                out.append(_unavailable("park_ride", "Park & ride", f"No transit option from {name}"))
                continue
            dur = d1["duration_s"] * tm
            lot_fee = float(fares.A["go_lot_parking_day" if kind == "go_train" else "ttc_lot_parking_day"]["value"])
            drive_cost = fares.driving_cost(d1["distance_km"], c.ev) + lot_fee
            legs = [_leg("car", f"Drive to {name}", origin, lp, d1, drive_cost,
                         fares.emissions_kg("ev" if c.ev else "car", d1["distance_km"]), dur)] + opt["legs"][1:]
            transit_legs = legs[1:]
            transit_min = sum(l["duration_s"] for l in transit_legs) / 60
            fare_notes = _apply_transit_fare(transit_legs, c, opt["boardings"], transit_min)
            lot_note = f"Park at {name} (lot fee is an estimate)" if lot_fee else f"Park at {name} (usually free for PRESTO riders, verify)"
            out.append(_finish("park_ride", "Park & ride", legs, c, depart, hour, max(0, opt["boardings"] - 1),
                               [lot_note] + fare_notes))

    pareto.mark(out)
    await asyncio.to_thread(reliability.annotate, out, c.model_dump(), depart)

    return {"origin": list(origin), "destination": list(dest), "depart": depart.strftime("%H:%M"),
            "candidates": out, "traffic_multiplier": tm,
            "data_notes": ["Road/walk/bike legs: OpenStreetMap routing", "Transit: TTC + GO Transit GTFS static line graph (headways estimated)",
                           "Traffic: typical-hour estimate, not live"]}
