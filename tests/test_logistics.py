"""Traveler/vehicle state rules: starting in a car, highways, lifts (dropped off / picked up), park at, get off at,
returning to the car, and the walking/cycling-on-a-highway ban."""
import json
from datetime import datetime

import pytest

from backend import highways, planner, trip

A = {"name": "A place", "lat": 43.65, "lon": -79.40}
B = {"name": "B place", "lat": 43.66, "lon": -79.38}
C = {"name": "C place", "lat": 43.70, "lon": -79.36}
D = {"name": "D place", "lat": 43.72, "lon": -79.30}
LOT = (43.69, -79.31)
REAL_ON_HIGHWAY = highways.on_highway  # the autouse fixture below replaces it for trip tests; highways tests need the real one


def cand(cid, a, b, minutes, cost=0.0, co2=0.0, mode=None, to=None):
    mode = mode or ("car" if cid in ("drive", "park_ride", "lift") else "walk")
    leg = {"mode": mode, "label": cid, "from": [a["lat"], a["lon"]], "to": list(to or (b["lat"], b["lon"])), "distance_km": 1.0,
           "duration_s": minutes * 60, "geometry": [[a["lat"], a["lon"]], list(to or (b["lat"], b["lon"]))], "cost": cost, "co2_kg": co2}
    return {"id": cid, "title": cid, "available": True, "legs": [leg], "duration_min": minutes, "cost": cost, "co2_kg": co2,
            "transfers": 0, "walk_m": 0, "bag_friendliness": 1.0, "notes": []}


@pytest.fixture(autouse=True)
def fake_planner(monkeypatch):
    trip._cache.clear()
    calls = []

    async def plan(o, d, c):
        calls.append((o, d, tuple(c.modes)))
        a, b = {"lat": o[0], "lon": o[1]}, {"lat": d[0], "lon": d[1]}
        out = []
        for m in c.modes:
            if m == "lift":
                out.append(cand("lift", a, b, 9, co2=1.5))
            elif m == "drive":
                out.append(cand("drive", a, b, 10, cost=5, co2=2) if c.has_car else {**cand("drive", a, b, 1), "available": False})
            elif m == "park_ride" and c.has_car:
                out.append(cand("park_ride", a, b, 15, cost=4, co2=1, to=LOT))
            elif m == "transit":
                out.append(cand("transit", a, b, 20, cost=3, co2=0.5))
            elif m == "walk":
                out.append(cand("walk", a, b, 60))
        return {"candidates": out, "traffic_multiplier": 1.0, "data_notes": []}

    monkeypatch.setattr(trip.planner, "plan", plan)
    monkeypatch.setattr(trip.highways, "on_highway", lambda lat, lon, tol_m=25.0: None)
    return calls


ALL = ["walk", "drive", "park_ride", "transit"]


def cons(**kw):
    return planner.Constraints(depart_at="09:00", modes=ALL, **kw)


def stop(p, **kw):
    return {**p, "type": "stop", "dwell_min": 0, "window": None, **kw}


def ids(out):
    return {x["id"] for x in out["candidates"]}


# -- starting in a car ------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_starting_in_a_car_means_the_first_leg_is_driven():
    out = await trip.plan_trip(A, [stop(B)], C, cons(start_in_car=True))
    assert all(i.split("|")[0] in ("drive", "park_ride") for i in ids(out))
    assert out["candidates"][0]["schedule"][0]["traveler"] == "driving"


@pytest.mark.asyncio
async def test_starting_in_a_car_implies_having_a_car():
    assert planner.Constraints(start_in_car=True).has_car is True


@pytest.mark.asyncio
async def test_start_on_a_highway_drives_to_the_next_exit_first(monkeypatch):
    exit_pt = {"lat": 43.655, "lon": -79.39, "name": "Exit 12", "distance_km": 1.0}
    monkeypatch.setattr(trip.highways, "on_highway", lambda lat, lon, tol_m=25.0: "Don Valley Parkway")
    monkeypatch.setattr(trip.highways, "next_exit", lambda o, d, max_km=25.0: exit_pt)

    async def osm_leg(mode, a, b):
        return {"distance_km": 2.0, "duration_s": 180.0, "geometry": [list(a), list(b)], "provenance": "estimated"}

    monkeypatch.setattr(trip.osm, "leg", osm_leg)
    out = await trip.plan_trip(A, [], B, cons(start_in_car=True))
    assert out["points"][1]["type"] == "exit" and out["points"][1]["name"] == "Exit 12"
    for c in out["candidates"]:
        first, second = c["id"].split("|")
        assert first == "drive_exit" and second in ("drive", "park_ride")   # still in the car at the exit: keep driving
    c = out["candidates"][0]
    assert [s["kind"] for s in c["schedule"]][:2] == ["origin", "exit"] and "Don Valley Parkway" in c["legs"][0]["label"]
    assert c["legs"][0]["cost"] > 0 and c["legs"][0]["mode"] == "car"      # fuel, but no parking at an exit


@pytest.mark.asyncio
async def test_start_on_a_highway_with_no_exit_is_impossible(monkeypatch):
    monkeypatch.setattr(trip.highways, "on_highway", lambda lat, lon, tol_m=25.0: "Gardiner Expressway")
    monkeypatch.setattr(trip.highways, "next_exit", lambda o, d, max_km=25.0: None)
    with pytest.raises(trip.TripImpossible, match="Gardiner Expressway"):
        await trip.plan_trip(A, [], B, cons(start_in_car=True))


# -- lifts: dropped off / picked up -----------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dropped_off_at_the_destination_is_a_free_lift_with_no_parking(fake_planner):
    out = await trip.plan_trip(A, [], {**D, "type": "dropoff"}, cons(has_car=True))
    assert ids(out) == {"lift"}
    c = out["candidates"][0]
    assert c["cost"] == 0 and len(c["legs"]) == 1                       # no fare, no parking leg
    assert c["schedule"][-1]["arrived_as"] == "passenger" and c["schedule"][-1]["traveler"] == "passenger"
    assert fake_planner[0][2] == ("lift",)


@pytest.mark.asyncio
async def test_dropped_off_does_not_move_your_car():
    out = await trip.plan_trip(A, [stop(B, type="dropoff")], C, cons(has_car=True))
    for c in out["candidates"]:
        first, second = c["id"].split("|")
        assert first == "lift"
        assert second != "drive"                                          # your car is still at A; you are on foot at B
    assert out["candidates"][0]["schedule"][1]["car"] == "parked at A place"


@pytest.mark.asyncio
async def test_picked_up_at_a_stop_means_the_next_leg_is_a_lift():
    out = await trip.plan_trip(A, [stop(B, type="pickup")], C, cons())
    assert {i.split("|")[1] for i in ids(out)} == {"lift"}
    assert {i.split("|")[0] for i in ids(out)} >= {"transit", "walk"}   # getting to the pick-up point is free choice


@pytest.mark.asyncio
async def test_picked_up_where_the_trip_ends_is_impossible():
    with pytest.raises(trip.TripImpossible, match="picked up"):
        await trip.plan_trip(A, [], {**B, "type": "pickup"}, cons())


@pytest.mark.asyncio
async def test_a_ride_can_start_at_the_origin():
    out = await trip.plan_trip({**A, "type": "pickup"}, [], B, cons())
    assert ids(out) == {"lift"}


# -- park at / get off at ----------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_park_at_a_stop_arrives_driving_and_the_car_stays_there():
    out = await trip.plan_trip(A, [stop(B, type="park")], C, cons(has_car=True))
    for c in out["candidates"]:
        assert c["id"].split("|")[0] == "drive"
        assert c["schedule"][1]["car"] == "parked at B place" and c["schedule"][1]["traveler"] == "on_foot"
    # leaving B: the car is parked there, so driving on needs the walk back to it (a parking connection)
    both = next(c for c in out["candidates"] if c["id"] == "drive|drive")
    assert any(l["label"].startswith("Walk back to your car") for l in both["legs"])
    assert next(c for c in out["candidates"] if c["id"] == "drive|transit")["schedule"][2]["car"] == "parked at B place"


@pytest.mark.asyncio
async def test_a_parked_car_stays_where_it_was_parked():
    out = await trip.plan_trip(A, [stop(B), stop(C)], D, cons(has_car=True))
    assert "drive|transit|drive" not in ids(out)       # parked at B, you are at C: you cannot drive it from C
    assert "drive|drive|drive" in ids(out)


@pytest.mark.asyncio
async def test_park_at_needs_a_car():
    with pytest.raises(trip.TripImpossible, match="have a car"):
        await trip.plan_trip(A, [stop(B, type="park")], C, cons(has_car=False))


@pytest.mark.asyncio
async def test_get_off_at_a_stop_means_arriving_by_transit():
    out = await trip.plan_trip(A, [stop(B, type="alight")], C, cons())
    assert {i.split("|")[0] for i in ids(out)} == {"transit"}


# -- needing the car later: refuse to leave it behind, or plan the way back --------------------------------------------
@pytest.mark.asyncio
async def test_needing_the_car_after_the_trip_forbids_leaving_it_behind():
    out = await trip.plan_trip(A, [], D, cons(has_car=True, car_needed_after=True))
    by = {c["id"]: c for c in out["candidates"]}
    assert not by["drive"].get("violates")                         # the car comes with you
    assert "car_left_behind" in by["transit"]["violates"] and "car_left_behind" in by["park_ride"]["violates"]


@pytest.mark.asyncio
async def test_plan_the_return_to_the_car_adds_a_final_leg_back_to_it():
    out = await trip.plan_trip(A, [], D, cons(has_car=True, car_needed_after=True, plan_car_return=True))
    by = {c["id"]: c for c in out["candidates"]}
    t = by["transit|return:transit"]
    assert not t.get("violates") and t["schedule"][-1]["kind"] == "car" and t["schedule"][-1]["name"] == "Your car"
    assert t["duration_min"] == 20 + 20 and t["cost"] == 6.0       # there and back
    assert t["arrive"] == "09:40" and t.get("meets_deadline") is None
    assert "drive" in by and not by["drive"].get("violates")        # driving all the way needs no return
    assert any(k.startswith("park_ride|return:") for k in by)       # the lot is a place the car can be left


@pytest.mark.asyncio
async def test_the_deadline_is_judged_at_the_destination_not_at_the_car():
    out = await trip.plan_trip(A, [], D, cons(has_car=True, car_needed_after=True, plan_car_return=True, arrive_by="09:25"))
    t = next(c for c in out["candidates"] if c["id"] == "transit|return:transit")
    assert t["meets_deadline"] is True and t["arrive"] == "09:40"   # reaches D at 09:20, the car at 09:40


@pytest.mark.asyncio
async def test_a_later_stop_that_needs_the_car_can_fetch_it_when_asked():
    base = await trip.plan_trip(A, [stop(B), stop(C, type="park")], D, cons(has_car=True))
    assert not any("fetch:" in i for i in ids(base))
    assert "transit|drive|drive" not in ids(base)                     # car left at A, C must be driven to: impossible
    out = await trip.plan_trip(A, [stop(B), stop(C, type="park")], D, cons(has_car=True, plan_car_return=True))
    fetched = [c for c in out["candidates"] if c["id"].startswith("transit|fetch:")]
    assert fetched, "going back to the car at A and driving to C must now be possible"
    c = fetched[0]
    assert "Go back to your car" in c["title"] and any("Go back" in n or "Goes back" in n for n in c["notes"])
    assert c["schedule"][2]["car"] == "parked at C place" if len(c["schedule"]) > 2 else True


# -- the highway rule ----------------------------------------------------------------------------------------------------
def test_walking_or_cycling_along_a_controlled_access_highway_is_rejected(monkeypatch):
    legs = [{"mode": "walk", "label": "Walk", "from": [43.6, -79.4], "to": [43.61, -79.4], "distance_km": 1.0, "duration_s": 600.0,
             "geometry": [[43.6, -79.4], [43.61, -79.4]], "provenance": "live", "cost": 0.0, "co2_kg": 0.0}]
    monkeypatch.setattr(planner.highways, "leg_on_highway", lambda geom, **k: "Gardiner Expressway")
    c = planner._finish("walk", "Walk", legs, planner.Constraints(), datetime(2026, 10, 18, 9, 0), 9, 0, [])
    assert c["violates"] == ["highway_walk:Gardiner Expressway"] and "Not allowed" in c["notes"][-1]
    monkeypatch.setattr(planner.highways, "leg_on_highway", lambda geom, **k: None)
    assert "violates" not in planner._finish("walk", "Walk", legs, planner.Constraints(), datetime(2026, 10, 18, 9, 0), 9, 0, [])


def test_driving_legs_may_use_highways(monkeypatch):
    legs = [{"mode": "car", "label": "Drive", "from": [43.6, -79.4], "to": [43.61, -79.4], "distance_km": 1.0, "duration_s": 600.0,
             "geometry": [[43.6, -79.4], [43.61, -79.4]], "provenance": "live", "cost": 1.0, "co2_kg": 0.1}]
    monkeypatch.setattr(planner.highways, "leg_on_highway", lambda geom, **k: "Gardiner Expressway")
    assert "violates" not in planner._finish("drive", "Drive", legs, planner.Constraints(), datetime(2026, 10, 18, 9, 0), 9, 0, [])


# -- highways.py on a tiny fake extract -------------------------------------------------------------------------------
@pytest.fixture
def tiny_highways(tmp_path, monkeypatch):
    data = {"ways": [{"id": 1, "name": "Fake Expressway", "ref": "", "kind": "motorway", "pts": [[43.6000, -79.4000], [43.6000, -79.3000]]}],
            "junctions": [{"lat": 43.6001, "lon": -79.3900, "ref": "7", "name": ""}, {"lat": 43.6001, "lon": -79.4400, "ref": "9", "name": ""}]}
    f = tmp_path / "highways.json"
    f.write_text(json.dumps(data))
    monkeypatch.setattr(highways, "FILE", f)
    monkeypatch.setattr(highways, "_index", None)
    monkeypatch.setattr(highways, "on_highway", REAL_ON_HIGHWAY)
    yield
    monkeypatch.setattr(highways, "_index", None)


def test_on_highway_and_leg_checks(tiny_highways):
    assert highways.available()
    assert highways.on_highway(43.6000, -79.3500) == "Fake Expressway"
    assert highways.on_highway(43.6010, -79.3500) is None                      # about 110 m away
    along = [[43.60005, -79.3800], [43.60005, -79.3700]]
    assert highways.leg_on_highway(along) == "Fake Expressway"                  # walking along it
    crossing = [[43.5995, -79.3500], [43.6005, -79.3500]]
    assert highways.leg_on_highway(crossing) is None                            # a short crossing (bridge) is not "along"
    away = [[43.6100, -79.3800], [43.6100, -79.3700]]
    assert highways.leg_on_highway(away) is None


def test_next_exit_is_the_nearest_one_that_makes_progress_toward_the_destination(tiny_highways):
    ex = highways.next_exit((43.6000, -79.3500), (43.6000, -79.4300))           # heading west
    assert ex["lon"] == -79.39 and ex["name"] == "Exit 7"
    assert highways.next_exit((43.6000, -79.3500), (43.6000, -79.4300), max_km=1.0) is None


def test_missing_data_never_claims_a_leg_is_fine_or_on_a_highway(monkeypatch, tmp_path):
    monkeypatch.setattr(highways, "FILE", tmp_path / "nope.json")
    monkeypatch.setattr(highways, "_index", None)
    monkeypatch.setattr(highways, "on_highway", REAL_ON_HIGHWAY)
    assert highways.available() is False and highways.leg_on_highway([[43.6, -79.4], [43.6, -79.3]]) is None
    assert highways.on_highway(43.6, -79.4) is None and highways.next_exit((43.6, -79.4), (43.7, -79.4)) is None
    monkeypatch.setattr(highways, "_index", None)


@pytest.mark.asyncio
async def test_get_off_the_subway_cannot_be_met_by_a_bus_route(monkeypatch):
    def transit_cand(cid, mode):
        c = cand("transit", A, B, 20, cost=3)
        c["id"] = cid
        c["legs"][0]["mode"] = mode
        return c

    async def plan(o, d, c):
        a, b = {"lat": o[0], "lon": o[1]}, {"lat": d[0], "lon": d[1]}
        return {"candidates": [transit_cand("transit_rail", "subway"), transit_cand("transit_bus", "bus"), cand("walk", a, b, 60)],
                "traffic_multiplier": 1.0, "data_notes": []}

    monkeypatch.setattr(trip.planner, "plan", plan)
    subway = await trip.plan_trip(A, [stop(B, type="alight", vehicle="subway")], C, cons())
    assert {i.split("|")[0] for i in ids(subway)} == {"transit_rail"}
    bus = await trip.plan_trip(A, [stop(B, type="alight", vehicle="bus")], C, cons())
    assert {i.split("|")[0] for i in ids(bus)} == {"transit_bus"}
    anyv = await trip.plan_trip(A, [stop(B, type="alight", vehicle="transit")], C, cons())
    assert {i.split("|")[0] for i in ids(anyv)} == {"transit_rail", "transit_bus"}

@pytest.mark.asyncio
async def test_legs_must_connect_no_teleporting(monkeypatch):
    out = await trip.plan_trip(A, [stop(B)], C, cons())
    assert all(not c.get("violates") for c in out["candidates"])
    trip._cache.clear()

    async def plan(o, d, c):
        a, b = {"lat": o[0], "lon": o[1]}, {"lat": d[0], "lon": d[1]}
        broken = cand("transit", a, b, 20)
        broken["legs"][0]["from"] = [o[0] + 0.05, o[1]]            # starts 5 km away from where you are
        return {"candidates": [broken], "traffic_multiplier": 1.0, "data_notes": []}

    monkeypatch.setattr(trip.planner, "plan", plan)
    bad = await trip.plan_trip(A, [stop(B)], C, cons())
    assert bad["candidates"] and all("teleport" in c["violates"] for c in bad["candidates"])