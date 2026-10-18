import pytest

from backend import planner, trip

A = {"name": "A place", "lat": 43.65, "lon": -79.40}
B = {"name": "B place", "lat": 43.66, "lon": -79.38}
C = {"name": "C place", "lat": 43.70, "lon": -79.36}
D = {"name": "D place", "lat": 43.72, "lon": -79.30}


def leg_cand(cid, a, b, minutes, cost=0.0, co2=0.0, title=None, walk=0):
    leg = {"mode": "car" if cid in ("drive", "park_ride") else "walk", "label": cid, "from": [a["lat"], a["lon"]], "to": [b["lat"], b["lon"]],
           "distance_km": 1.0, "duration_s": minutes * 60, "geometry": [[a["lat"], a["lon"]], [b["lat"], b["lon"]]], "cost": cost, "co2_kg": co2}
    return {"id": cid, "title": title or cid, "available": True, "legs": [leg], "duration_min": minutes, "cost": cost, "co2_kg": co2,
            "transfers": 0, "walk_m": walk, "bag_friendliness": 1.0, "notes": []}


@pytest.fixture(autouse=True)
def fake_planner(monkeypatch):
    trip._cache.clear()
    calls = []

    async def plan(o, d, c):
        calls.append((o, d, c.depart_at, c.arrive_by, c.stay_hours))
        a, b = {"lat": o[0], "lon": o[1]}, {"lat": d[0], "lon": d[1]}
        return {"candidates": [leg_cand("drive", a, b, 10, cost=5, co2=2) if c.has_car else {**leg_cand("drive", a, b, 1), "available": False},
                               leg_cand("transit", a, b, 20, cost=3, co2=0.5), leg_cand("walk", a, b, 60, walk=2000)],
                "traffic_multiplier": 1.0, "data_notes": []}

    monkeypatch.setattr(trip.planner, "plan", plan)
    return calls


def cons(**kw):
    return planner.Constraints(depart_at="09:00", **kw)


def stop(p, **kw):
    return {**p, "type": "stop", "dwell_min": 0, "window": None, **kw}


@pytest.mark.asyncio
async def test_chain_sums_legs_and_dwell_and_schedule_increases():
    out = await trip.plan_trip(A, [stop(B, dwell_min=15)], C, cons())
    c = next(x for x in out["candidates"] if x["id"] == "transit|transit")
    assert c["duration_min"] == 20 + 15 + 20          # two legs plus the 15 min stay at B
    assert c["cost"] == 6.0 and c["co2_kg"] == 1.0
    assert [s["label"] for s in c["schedule"]] == ["A", "B", "C"]
    b = c["schedule"][1]
    assert (b["arrive"], b["dwell_min"], b["depart"]) == ("09:20", 15, "09:35")
    assert c["schedule"][2]["arrive"] == "09:55" and c["arrive"] == "09:55"
    times = [s["arrive"] for s in c["schedule"][1:]]
    assert times == sorted(times)
    assert {l["segment"] for l in c["legs"]} == {0, 1}
    assert out["points"][1]["label"] == "B"


@pytest.mark.asyncio
async def test_you_can_only_drive_a_car_that_is_with_you(fake_planner):
    out = await trip.plan_trip(A, [stop(B)], C, cons(has_car=True))
    ids = {x["id"] for x in out["candidates"]}
    assert "drive|drive" in ids                        # the car follows you
    assert "transit|drive" not in ids                  # transit to B left the car at A: you cannot drive it from B
    assert "drive|transit" in ids                      # fine: drive to B, then leave the car there and take transit


@pytest.mark.asyncio
async def test_no_car_means_no_driving_anywhere():
    out = await trip.plan_trip(A, [stop(B)], C, cons(has_car=False))
    assert not any("drive" in x["id"] for x in out["candidates"])


@pytest.mark.asyncio
async def test_via_point_must_be_passed_in_the_same_mode():
    out = await trip.plan_trip(A, [stop(B, type="via")], C, cons(has_car=True))
    for x in out["candidates"]:
        first, second = x["id"].split("|")
        assert first == second


@pytest.mark.asyncio
async def test_time_window_adds_wait_or_violates():
    early = await trip.plan_trip(A, [stop(B, window=("09:40", "10:00"))], C, cons())
    c = next(x for x in early["candidates"] if x["id"] == "transit|transit")
    assert c["schedule"][1]["wait_min"] == 20 and c["schedule"][1]["depart"] == "09:40"   # arrived 09:20, waits for 09:40
    assert c["duration_min"] == 20 + 20 + 20 and not c.get("violates")
    late = await trip.plan_trip(A, [stop(B, window=("08:00", "08:30"))], C, cons())
    assert all("window:B place" in x["violates"] for x in late["candidates"])


@pytest.mark.asyncio
async def test_deadline_and_leave_by_use_the_whole_trip():
    out = await trip.plan_trip(A, [stop(B, dwell_min=10)], C, cons(arrive_by="10:00"))
    c = next(x for x in out["candidates"] if x["id"] == "transit|transit")
    assert c["meets_deadline"] is True and c["leave_by"] == "09:10"
    out = await trip.plan_trip(A, [stop(B, dwell_min=10)], C, cons(arrive_by="09:30"))
    assert next(x for x in out["candidates"] if x["id"] == "transit|transit")["meets_deadline"] is False


@pytest.mark.asyncio
async def test_each_leg_is_planned_from_its_estimated_departure(fake_planner):
    await trip.plan_trip(A, [stop(B, dwell_min=10)], C, cons(has_car=True, arrive_by="10:00"))
    first, second = fake_planner
    assert first[2] == "09:00" and first[3] is None             # first leg: trip departure, no deadline
    assert second[2] == "09:20" and second[3] == "10:00"        # fastest option (10 min drive) + 10 min dwell; deadline only on the last leg


@pytest.mark.asyncio
async def test_dwell_sets_parking_hours_for_the_leg_that_ends_at_a_stop(fake_planner):
    await trip.plan_trip(A, [stop(B, dwell_min=90)], C, cons(has_car=True))
    assert fake_planner[0][4] == 1.5


@pytest.mark.asyncio
async def test_dominated_leg_options_are_pruned_but_vehicle_classes_survive():
    a, b = {"lat": 0, "lon": 0}, {"lat": 0, "lon": 1}
    slow_walk, fast_walk = leg_cand("walk", a, b, 30, walk=10), leg_cand("walk2", a, b, 20, walk=5)
    drive = leg_cand("drive", a, b, 40, cost=9, co2=3)       # worse than walk everywhere, but it moves the car: keep it
    kept = {c["id"] for c in trip._prune([slow_walk, fast_walk, drive])}
    assert kept == {"walk2", "drive"}


@pytest.mark.asyncio
async def test_optimize_order_considers_every_ordering_and_labels_them():
    out = await trip.plan_trip(A, [stop(B), stop(C)], D, cons(), optimize_order=True)
    prefixes = {x["id"].split(":")[0] for x in out["candidates"]}
    assert prefixes == {"ABCD", "ACBD"}
    assert out["optimize_order"] is True


@pytest.mark.asyncio
async def test_order_is_kept_by_default():
    out = await trip.plan_trip(A, [stop(B), stop(C)], D, cons())
    assert all(":" not in x["id"] for x in out["candidates"])
    assert [p["label"] for p in out["points"]] == ["A", "B", "C", "D"] and out["optimize_order"] is False


@pytest.mark.asyncio
async def test_limits():
    with pytest.raises(ValueError):
        await trip.plan_trip(A, [stop(B)] * 7, C, cons())
    with pytest.raises(ValueError):
        await trip.plan_trip(A, [stop(B)] * 4, C, cons(), optimize_order=True)


@pytest.mark.asyncio
async def test_identical_requests_share_one_computation(fake_planner):
    await trip.plan_trip(A, [stop(B)], C, cons())
    n = len(fake_planner)
    await trip.plan_trip(A, [stop(B)], C, cons())
    assert len(fake_planner) == n


@pytest.mark.asyncio
async def test_reliability_combines_across_legs_including_road_only_legs_with_no_route_id(monkeypatch):
    """Regression: a multi-stop trip's reliability used to vanish whenever any leg's reliability came
    from a road/bus leg rather than a transit route_id (Phase 6b let road-only legs score too, but this
    merge still gated on route_id-only legs matching the count of scored picks). Also: confidence and
    delay_factors must survive the per-leg product, not just reliability_pct/history_days."""
    trip._cache.clear()

    async def plan(o, d, c):
        a, b = {"lat": o[0], "lon": o[1]}, {"lat": d[0], "lon": d[1]}
        if abs(o[0] - A["lat"]) < 1e-6:  # first pair: a road leg with reliability but no route_id
            cand = leg_cand("drive", a, b, 10, cost=5, co2=2)
            cand["legs"][0]["timing"] = "road"
            cand["legs"][0]["base_s"] = cand["legs"][0]["duration_s"]
            cand["reliability"] = {"reliability_pct": 90, "history_days": 0,
                                    "confidence": "default (no matching history; the percentage comes from the road-timing model only)",
                                    "delay_factors": [{"text": "road leg delay", "source": "TomTom (live, not stored)", "timestamp": 1.0, "default": False}]}
            return {"candidates": [cand], "traffic_multiplier": 1.0, "data_notes": []}
        cand = leg_cand("transit", a, b, 20, cost=3, co2=0.5)  # second pair: a transit leg with route_id
        cand["legs"][0]["route_id"] = "501"
        cand["reliability"] = {"reliability_pct": 80, "history_days": 5, "confidence": "low",
                                "delay_factors": [{"text": "transit leg delay", "source": "TTC realtime (GTFS-RT)", "timestamp": 2.0, "default": False}]}
        return {"candidates": [cand], "traffic_multiplier": 1.0, "data_notes": []}

    monkeypatch.setattr(trip.planner, "plan", plan)
    out = await trip.plan_trip(A, [stop(B)], C, cons(has_car=True))
    rel = out["candidates"][0]["reliability"]
    assert rel["reliability_pct"] == round(100 * 0.9 * 0.8)
    assert rel["history_days"] == 0
    assert rel["confidence"] == "default (no matching history; the percentage comes from the road-timing model only)"
    assert len(rel["delay_factors"]) == 2
    assert {f["text"] for f in rel["delay_factors"]} == {"road leg delay", "transit leg delay"}
