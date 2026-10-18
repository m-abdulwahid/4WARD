"""Rules checked on the REAL planner (offline: REPLAY=1 serves cached/estimated street legs, local GTFS graph)."""
import pytest

from backend import planner
from backend.geo import haversine_km

UNION = (43.6453, -79.3806)
HIGH_PARK = (43.6465, -79.4637)
ALL = ["walk", "bike", "ebike", "bikeshare", "drive", "rideshare", "transit", "park_ride"]


@pytest.fixture
async def plan(monkeypatch):
    monkeypatch.setenv("REPLAY", "1")
    c = planner.Constraints(modes=ALL, has_car=True, has_bike=True, has_ebike=True, depart_at="08:30")
    return await planner.plan(UNION, HIGH_PARK, c)


@pytest.mark.asyncio
async def test_every_parking_leg_has_a_cost_and_a_walking_connection(plan):
    drive = next(c for c in plan["candidates"] if c["id"] == "drive")
    assert drive["legs"][0]["mode"] == "car" and drive["legs"][0]["cost_breakdown"]["parking"] > 0
    assert drive["legs"][-1]["mode"] == "walk" and "parking" in drive["legs"][-1]["label"].lower()
    assert drive["legs"][-1]["duration_s"] > 0
    pr = next(c for c in plan["candidates"] if c["id"] == "park_ride" and c.get("available"))
    lot_leg = pr["legs"][1]
    assert lot_leg["mode"] == "walk" and "Park and walk" in lot_leg["label"] and lot_leg["duration_s"] > 0
    assert pr["legs"][0]["cost"] >= 0           # the lot fee is modelled (it can be 0 where lots are free)
    assert any("lot fee" in n or "Park at" in n for n in pr["notes"])


@pytest.mark.asyncio
async def test_no_candidate_teleports_between_legs(plan):
    for c in plan["candidates"]:
        if not c.get("available"):
            continue
        legs = c["legs"]
        for a, b in zip(legs, legs[1:]):
            assert haversine_km(tuple(a["to"]), tuple(b["from"])) < 0.15, f"{c['id']}: {a['label']} -> {b['label']}"
        assert haversine_km(tuple(legs[0]["from"]), UNION) < 0.15 and haversine_km(tuple(legs[-1]["to"]), HIGH_PARK) < 0.15


@pytest.mark.asyncio
async def test_a_lift_is_only_planned_when_asked_for(monkeypatch):
    monkeypatch.setenv("REPLAY", "1")
    normal = await planner.plan(UNION, HIGH_PARK, planner.Constraints(modes=ALL, has_car=True, depart_at="08:30"))
    assert "lift" not in {c["id"] for c in normal["candidates"]}
    lift = await planner.plan(UNION, HIGH_PARK, planner.Constraints(modes=["lift"], depart_at="08:30"))
    assert [c["id"] for c in lift["candidates"]] == ["lift"]
    c = lift["candidates"][0]
    assert c["cost"] == 0 and len(c["legs"]) == 1 and c["co2_kg"] > 0
