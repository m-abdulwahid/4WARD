"""TomTom live traffic: in memory only (never stored), 5 minute TTL, budgeted, and it can never fail a route."""
import builtins
import logging
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from backend import config, conditions, planner, schedule, tomtom
from backend.db import store

TZ = ZoneInfo("America/Toronto")
FAKE_KEY = "TESTKEY-do-not-leak-0123456789"


def flow(cur=17, free=34, closure=False):
    return {"flowSegmentData": {"currentSpeed": cur, "freeFlowSpeed": free, "currentTravelTime": 91, "freeFlowTravelTime": 45, "confidence": 0.9,
                                "roadClosure": closure, "frc": "FRC3", "coordinates": {"coordinate": [{"latitude": 43.6, "longitude": -79.4}]}}}


def install(handler):
    calls = []

    def wrapped(request: httpx.Request):
        calls.append(request)
        return handler(request)

    tomtom._transport = httpx.MockTransport(wrapped)
    return calls


@pytest.fixture(autouse=True)
def with_key(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", FAKE_KEY)
    yield
    tomtom._transport = None


async def poll():
    await tomtom.refresh(force=True)


# -- what a poll does ------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_poll_is_one_request_per_segment_and_keeps_the_latest_ratio_in_memory():
    calls = install(lambda r: httpx.Response(200, json=flow(17, 34)))
    await poll()
    assert len(calls) == len(tomtom.SEGMENTS) == 10
    assert {tuple(r.url.params["point"].split(",")) for r in calls} == {(str(s["lat"]), str(s["lon"])) for s in tomtom.SEGMENTS}
    live = tomtom.latest()
    assert set(live) == {s["id"] for s in tomtom.SEGMENTS} and all(v["ratio"] == 0.5 for v in live.values())
    factor, age = tomtom.live_factor("queen-spadina")
    assert factor == 2.0 and 0 <= age < 5                       # 17 km/h where 34 is free flow: twice as slow


@pytest.mark.asyncio
async def test_values_expire_after_five_minutes_and_are_dropped_from_memory():
    install(lambda r: httpx.Response(200, json=flow()))
    await poll()
    assert tomtom.latest()
    old = time.time() + config.TOMTOM_TTL_S + 1
    assert tomtom.latest(now=old) == {} and tomtom._values == {}          # gone, not merely hidden
    assert tomtom.live_factor("queen-spadina", now=old) is None
    assert config.TOMTOM_TTL_S == 300


@pytest.mark.asyncio
async def test_a_fresh_cache_means_no_new_requests_and_nothing_polls_in_the_background():
    calls = install(lambda r: httpx.Response(200, json=flow()))
    assert calls == []                                                    # importing and idling make no requests
    await tomtom.refresh()
    first = len(calls)
    await tomtom.refresh()
    await tomtom.refresh()
    assert len(calls) == first == 10                                      # demand-driven: at most one poll per TTL


@pytest.mark.asyncio
async def test_closed_or_unusable_segments_say_nothing_about_traffic():
    install(lambda r: httpx.Response(200, json=flow(3, 3, closure=True)))
    await poll()
    assert tomtom.latest() == {} and tomtom.status()["live"] is False
    install(lambda r: httpx.Response(200, json={"flowSegmentData": {"currentSpeed": 5, "freeFlowSpeed": 0}}))
    tomtom.reset()
    await poll()
    assert tomtom.latest() == {}


def test_factor_is_clamped_to_sane_bounds():
    now = time.time()
    tomtom._values["a"] = {"ratio": 1.2, "current": 60, "free": 50, "at": now}        # faster than free flow: never below x1
    tomtom._values["b"] = {"ratio": 0.01, "current": 1, "free": 100, "at": now}      # near standstill: capped
    assert tomtom.live_factor("a")[0] == 1.0 and tomtom.live_factor("b")[0] == 4.0


# -- never stored ---------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_nothing_from_tomtom_is_ever_written_anywhere(monkeypatch, caplog, tmp_path):
    writes: list = []
    real_open = builtins.open

    def guarded_open(file, mode="r", *a, **k):
        if any(c in str(mode) for c in "wax+"):
            writes.append(("open", str(file)))
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(Path, "write_text", lambda self, *a, **k: writes.append(("write_text", str(self))))
    monkeypatch.setattr(Path, "write_bytes", lambda self, *a, **k: writes.append(("write_bytes", str(self))))
    monkeypatch.setattr(store, "_exec", lambda *a, **k: writes.append(("db", a[0][:40] if a else "")))
    install(lambda r: httpx.Response(200, json=flow(23, 41)))
    with caplog.at_level(logging.DEBUG):
        await poll()
        tomtom.status()
        tomtom.profile_for([[43.6486, -79.3954], [43.6486, -79.3900]])
        # a failing poll must not log the request URL (it carries the key) either
        install(lambda r: (_ for _ in ()).throw(httpx.ConnectError("boom connecting to " + str(r.url))))
        tomtom._state["backoff_until"] = 0
        tomtom._values.clear()
        await poll()
    assert writes == [], f"TomTom data reached storage: {writes}"
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert FAKE_KEY not in logged and "23" not in logged.split("httpx")[0] and "flowSegmentData" not in logged
    assert FAKE_KEY not in str(tomtom.status()) and "currentSpeed" not in str(tomtom.status())


def test_storing_is_off_by_default_and_no_storage_code_exists(monkeypatch):
    assert config.TOMTOM_STORE is False and tomtom.status()["stored"] is False
    assert not [n for n in dir(tomtom) if any(w in n.lower() for w in ("persist", "save", "insert", "write", "dump"))]
    monkeypatch.setattr(config, "TOMTOM_STORE", True)                      # flipping the flag only reports the request; it stores nothing
    assert tomtom.store_requested() is True and tomtom.status()["stored"] is False
    assert not hasattr(store, "insert_traffic_flow") and not hasattr(store, "upsert_traffic_flow")


def test_the_request_budget_stays_under_eighty_percent_of_the_free_allowance():
    per_day = config.TOMTOM_DAILY_REQUEST_BUDGET
    assert per_day <= 0.8 * config.TOMTOM_OLDER_FREE_PER_DAY
    assert per_day * 30 <= 0.8 * config.TOMTOM_FREE_REQUESTS_PER_MONTH


@pytest.mark.asyncio
async def test_polling_stops_for_the_day_when_the_budget_is_used_up():
    calls = install(lambda r: httpx.Response(200, json=flow()))
    tomtom._state.update(day=tomtom._today(), used_today=config.TOMTOM_DAILY_REQUEST_BUDGET - 5)
    await poll()
    assert calls == [] and tomtom.status()["state"] == "budget_exhausted" and tomtom.latest() == {}
    tomtom._state.update(used_today=0)
    await poll()
    assert len(calls) == 10 and tomtom._state["used_today"] == 10


# -- falling back (a route is never failed) -------------------------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("make,state", [
    (lambda r: httpx.Response(401, json={"error": "Forbidden"}), "key_rejected"),
    (lambda r: httpx.Response(403, text="Forbidden"), "key_rejected"),
    (lambda r: httpx.Response(429, text="slow down"), "rate_limited"),
    (lambda r: httpx.Response(500, text="boom"), "unreachable"),
    (lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")), "unreachable"),
])
async def test_tomtom_problems_fall_back_to_typical_and_back_off(make, state):
    calls = install(make)
    await poll()
    s = tomtom.status()
    assert s["state"] == state and s["live"] is False and "ESTIMATED" in s["label"] and tomtom.latest() == {}
    n = len(calls)
    await tomtom.refresh()                                                  # backing off: it does not hammer a rejecting API
    assert len(calls) == n


@pytest.mark.asyncio
async def test_no_key_means_no_requests_and_typical_speeds(monkeypatch):
    monkeypatch.delenv("TOMTOM_API_KEY")
    calls = install(lambda r: httpx.Response(200, json=flow()))
    await tomtom.refresh()
    assert calls == [] and tomtom.status()["state"] == "no_key" and "ESTIMATED" in tomtom.status()["label"]


@pytest.mark.asyncio
async def test_a_route_is_computed_even_when_tomtom_rejects_the_key(monkeypatch):
    monkeypatch.setenv("REPLAY", "1")
    install(lambda r: httpx.Response(401, json={"error": "Forbidden"}))
    c = planner.Constraints(modes=["drive", "walk"], has_car=True)             # departs now: a poll is attempted and fails
    plan = await planner.plan((43.6453, -79.3806), (43.6465, -79.4637), c)
    drive = next(x for x in plan["candidates"] if x["id"] == "drive")
    road = next(l for l in drive["legs"] if l.get("timing") == "road")
    assert drive["available"] and "ESTIMATED" in road["traffic_source"]
    assert tomtom.status()["state"] == "key_rejected"


# -- using the live ratio -----------------------------------------------------------------------------------------------------------
def test_a_leg_on_a_downtown_corridor_gets_that_corridors_profile_and_a_far_one_does_not():
    on_queen = [[43.6486, -79.4000], [43.6486, -79.3900]]
    prof = tomtom.profile_for(on_queen)
    assert "queen-spadina" in prof and sum(prof.values()) > 500
    assert tomtom.profile_for([[43.9, -78.9], [43.95, -78.9]]) == {}            # Oshawa: nowhere near the corridors


def test_live_conditions_weight_each_corridor_by_the_metres_of_the_leg_on_it():
    now = time.time()
    tomtom._values["queen-spadina"] = {"ratio": 0.5, "current": 10, "free": 20, "at": now}      # x2
    leg = {"distance_km": 10.0, "corridors": {"queen-spadina": 2500}, "timing": "road", "base_s": 600}
    when = datetime(2026, 10, 5, 8, 0, tzinfo=TZ)                                                # typical 08:00 is x1.55
    c = conditions.leg_conditions(leg, when, now=when)
    assert c["source"] == tomtom.LABEL_LIVE and c["live_share"] == 0.25
    assert abs(c["factor"] - (0.25 * 2.0 + 0.75 * 1.55)) < 1e-9
    # no live value for a corridor: that stretch stays typical and ESTIMATED
    leg2 = {"distance_km": 10.0, "corridors": {"dundas-yonge": 2500}, "timing": "road", "base_s": 600}
    c2 = conditions.leg_conditions(leg2, when, now=when)
    assert c2["source"] == conditions.TYPICAL and c2["factor"] == 1.55
    # a trip that starts hours from now does not use "right now" conditions
    far = datetime(2026, 10, 5, 14, 0, tzinfo=TZ)
    assert conditions.leg_conditions(leg, far, now=when)["source"] == conditions.TYPICAL


def test_surface_transit_is_adjusted_by_live_versus_typical_conditions(monkeypatch):
    from backend import realtime, timetable

    monkeypatch.setattr(realtime, "route_delay_s", lambda r: None)
    monkeypatch.setattr(timetable, "available", lambda: True)
    when = datetime(2026, 10, 5, 8, 0, tzinfo=TZ)
    monkeypatch.setattr(timetable, "next_departure", lambda key, idx, at: at)
    monkeypatch.setattr(timetable, "ride_seconds", lambda key, a, b: 600.0)
    tomtom._values["queen-spadina"] = {"ratio": 1 / 3.1, "current": 10, "free": 31, "at": time.time()}       # x3.1 vs typical 1.55: ride x2
    leg = {"mode": "lrt_streetcar", "label": "501", "from": [0, 0], "to": [0, 0], "distance_km": 5, "duration_s": 600, "geometry": [[0, 0]],
           "route_id": "501", "timing": "rail", "line_key": "501|0", "from_idx": 0, "to_idx": 5, "est_wait_s": 0, "est_ride_s": 600,
           "corridors": {"queen-spadina": 5000}}
    legs, _ = schedule.retime([leg], when, now=when)
    assert abs(legs[0]["duration_s"] - (legs[0]["wait_s"] + 1200)) < 1 and tomtom.LABEL_LIVE in legs[0]["provenance"]
    subway = {**leg, "mode": "subway"}                                                                       # a subway does not share the road
    legs, _ = schedule.retime([subway], when, now=when)
    assert abs(legs[0]["duration_s"] - (legs[0]["wait_s"] + 600)) < 1
