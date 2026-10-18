"""Time accuracy: real scheduled departures, retiming on the clock, leave-by, arrive-by, observed delays."""
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend import build_timetable, conditions, planner, realtime, schedule, timetable, trip
from backend.config import BOARDING_BUFFER_S, TRANSFER_BUFFER_S

TZ = ZoneInfo("America/Toronto")


def dt(day: int, h: int, m: int = 0, s: int = 0) -> datetime:
    return datetime(2026, 10, day, h, m, s, tzinfo=TZ)   # Oct 5 2026 is a Monday


# -- a tiny synthetic GTFS feed ------------------------------------------------------------------------------------------
def write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def feed(tmp_path, monkeypatch):
    """One subway-like line S1..S4 (direction 0). Weekday service 'WK' every 10 min 06:00-06:50 plus a trip that runs past
    midnight; service 'WE' (weekend) hourly. Oct 12 is a holiday: WK removed, WE added. A short-turn trip serves only S2-S4."""
    d = tmp_path / "gtfs"
    d.mkdir()
    write_csv(d / "routes.txt", [{"route_id": "L", "route_type": "1", "route_short_name": "L", "route_long_name": "Test line", "route_color": "112233"}])
    stops = [("S1", 43.60), ("S2", 43.61), ("S3", 43.62), ("S4", 43.63)]
    write_csv(d / "stops.txt", [{"stop_id": s, "stop_name": s, "stop_lat": lat, "stop_lon": -79.40} for s, lat in stops])
    trips, times = [], []

    def add(tid, svc, first_secs, picks=(0, 1, 2, 3)):
        trips.append({"route_id": "L", "service_id": svc, "trip_id": tid, "direction_id": "0"})
        for seq, k in enumerate(picks):
            t = first_secs + k * 300  # 5 minutes between stops
            hh, mm, ss = t // 3600, t % 3600 // 60, t % 60
            times.append({"trip_id": tid, "stop_sequence": seq, "stop_id": stops[k][0], "arrival_time": f"{hh:02d}:{mm:02d}:{ss:02d}", "departure_time": f"{hh:02d}:{mm:02d}:{ss:02d}"})

    for i in range(6):
        add(f"wk{i}", "WK", 6 * 3600 + i * 600)
    add("late", "WK", 24 * 3600 + 20 * 60)            # 00:20 the next morning, written as 24:20:00
    add("short", "WK", 6 * 3600 + 5 * 60 + 300, (1, 2, 3))  # starts at S2 at 06:10, serves only S2-S4
    add("we0", "WE", 8 * 3600)
    add("we1", "WE", 9 * 3600)
    write_csv(d / "trips.txt", trips)
    write_csv(d / "stop_times.txt", times)
    write_csv(d / "calendar.txt", [
        {"service_id": "WK", "monday": 1, "tuesday": 1, "wednesday": 1, "thursday": 1, "friday": 1, "saturday": 0, "sunday": 0, "start_date": "20261001", "end_date": "20261031"},
        {"service_id": "WE", "monday": 0, "tuesday": 0, "wednesday": 0, "thursday": 0, "friday": 0, "saturday": 1, "sunday": 1, "start_date": "20261001", "end_date": "20261031"}])
    write_csv(d / "calendar_dates.txt", [{"service_id": "WK", "date": "20261012", "exception_type": 2}, {"service_id": "WE", "date": "20261012", "exception_type": 1}])
    write_csv(d / "feed_info.txt", [{"feed_publisher_name": "T", "feed_start_date": "20261001", "feed_end_date": "20261031"}])
    net_line = {"route_id": "L", "direction": "0", "name": "L Test line", "type": "subway", "color": "#112233",
                "stops": [{"id": s, "name": s, "lat": lat, "lon": -79.40} for s, lat in stops]}
    src = {"dir": d, "prefix": "", "label": "synthetic", "rail_types": {"1": "subway"}, "headway_min": {"1": 10}, "skip_prefix": None}
    meta, lines = build_timetable.build_source(src, [net_line])
    out = tmp_path / "timetable.json"
    out.write_text(json.dumps({"sources": [meta], "lines": lines}))
    monkeypatch.setattr(timetable, "FILE", out)
    timetable.reload()
    yield
    monkeypatch.undo()
    timetable.reload()


# -- timetable ---------------------------------------------------------------------------------------------------------
def test_weekday_next_departure_is_a_real_scheduled_time(feed):
    # Monday Oct 5, standing at S1 at 06:12: trips leave S1 at :00 :10 :20 ... so the next is 06:20, not an average wait
    assert timetable.next_departure("L|0", 0, dt(5, 6, 12)) == dt(5, 6, 20)
    assert timetable.next_departure("L|0", 0, dt(5, 6, 20)) == dt(5, 6, 20)       # at or after
    assert timetable.next_departure("L|0", 2, dt(5, 6, 12)) == dt(5, 6, 20)       # S3 is 10 min after S1: the 06:10 trip leaves S3 at 06:20


def test_service_ended_and_next_day_start(feed):
    # after the last weekday trip the next one is the after-midnight trip written as 24:20 (it belongs to the previous service day)
    assert timetable.next_departure("L|0", 0, dt(5, 20, 0)) == dt(6, 0, 20)


def test_after_midnight_trip_found_from_the_next_calendar_day(feed):
    assert timetable.next_departure("L|0", 0, dt(6, 0, 10)) == dt(6, 0, 20)


def test_weekend_and_holiday_calendars(feed):
    assert timetable.next_departure("L|0", 0, dt(10, 7, 0)) == dt(10, 8, 0)       # Saturday Oct 10: weekend service
    assert timetable.next_departure("L|0", 0, dt(12, 6, 0)) == dt(12, 8, 0)       # Monday Oct 12 holiday: weekday service removed, weekend added


def test_short_turn_trip_boards_only_where_it_runs(feed):
    # the 06:10 short turn starts at S2: boarding at S1 never uses it, boarding at S2 can
    assert timetable.next_departure("L|0", 0, dt(5, 6, 8)) == dt(5, 6, 10)       # the regular 06:10 full-run trip (not the short turn)
    s2 = timetable.next_departure("L|0", 1, dt(5, 6, 14))
    assert s2 is not None and s2 >= dt(5, 6, 14)


def test_outside_the_feed_there_is_no_departure(feed):
    assert timetable.next_departure("L|0", 0, dt(5, 6, 0) + timedelta(days=60)) is None
    assert timetable.next_departure("NOPE|0", 0, dt(5, 6, 0)) is None


def test_ride_seconds_come_from_the_schedule(feed):
    assert timetable.ride_seconds("L|0", 0, 3) == 900.0


# -- retime --------------------------------------------------------------------------------------------------------------
def walk(minutes):
    return {"mode": "walk", "label": "walk", "from": [0, 0], "to": [0, 0], "distance_km": 0.3, "duration_s": minutes * 60, "geometry": [[0, 0]]}


def rail(from_idx=0, to_idx=3, route_id="L", line="L|0", est_wait=300, est_ride=900):
    return {"mode": "subway", "label": "ride", "from": [0, 0], "to": [0, 0], "distance_km": 3, "duration_s": est_wait + est_ride, "geometry": [[0, 0]],
            "route_id": route_id, "timing": "rail", "line_key": line, "from_idx": from_idx, "to_idx": to_idx, "est_wait_s": est_wait, "est_ride_s": est_ride}


def road(base_s=600, extra=0):
    return {"mode": "car", "label": "drive", "from": [0, 0], "to": [0, 0], "distance_km": 5, "duration_s": base_s, "geometry": [[0, 0]],
            "timing": "road", "base_s": base_s, "fixed_extra_s": extra}


def test_retime_waits_for_the_real_departure_and_sums_exactly(feed, monkeypatch):
    monkeypatch.setattr(realtime, "route_delay_s", lambda r: None)
    legs, arrive = schedule.retime([walk(5), rail(), walk(4)], dt(5, 6, 3), now=dt(5, 6, 0))
    # at the stop 06:08; buffer 60 s -> catch the 06:10 departure; ride 15 min; walk 4 min
    assert legs[1]["depart_at"] == "06:08" and legs[1]["wait_s"] == 120 and legs[1]["scheduled"] is True
    assert legs[1]["duration_s"] == 120 + 900
    assert arrive == dt(5, 6, 10) + timedelta(minutes=15 + 4)
    starts = [datetime.fromisoformat(l["start_iso"]) for l in legs]
    ends = [datetime.fromisoformat(l["end_iso"]) for l in legs]
    assert all(a < b for a, b in zip(starts, starts[1:])) and all(e <= s for e, s in zip(ends, starts[1:]))   # ETAs strictly increase
    assert sum(l["duration_s"] for l in legs) == (arrive - dt(5, 6, 3)).total_seconds()                       # parts sum to the total
    assert ends[-1] - starts[0] == arrive - dt(5, 6, 3)                                                        # total = last arrival - first departure


def test_boarding_buffer_means_a_vehicle_leaving_right_now_is_missed(feed, monkeypatch):
    monkeypatch.setattr(realtime, "route_delay_s", lambda r: None)
    legs, _ = schedule.retime([rail()], dt(5, 6, 9, 30), now=dt(5, 6, 0))     # arrive 06:09:30, the 06:10 leaves in 30 s < the 60 s buffer
    assert legs[0]["wait_s"] == (dt(5, 6, 20) - dt(5, 6, 9, 30)).total_seconds()
    legs, _ = schedule.retime([rail()], dt(5, 6, 8, 30), now=dt(5, 6, 0))     # 90 s ahead: catchable
    assert legs[0]["wait_s"] == 90


def test_transfers_use_the_transfer_buffer(feed, monkeypatch):
    monkeypatch.setattr(realtime, "route_delay_s", lambda r: None)
    monkeypatch.setattr(schedule, "BOARDING_BUFFER_S", 60)
    monkeypatch.setattr(schedule, "TRANSFER_BUFFER_S", 240)
    legs, _ = schedule.retime([rail(0, 3), rail(0, 3)], dt(5, 6, 0), now=dt(5, 6, 0))
    first_arrive = datetime.fromisoformat(legs[0]["end_iso"])
    second_dep = datetime.fromisoformat(legs[1]["start_iso"]) + timedelta(seconds=legs[1]["wait_s"])
    assert second_dep - first_arrive >= timedelta(seconds=240)
    assert BOARDING_BUFFER_S == 60 and TRANSFER_BUFFER_S == 60            # the shipped defaults are configurable settings


def test_observed_delay_shifts_a_soon_departure_but_not_a_far_one(feed, monkeypatch):
    monkeypatch.setattr(realtime, "route_delay_s", lambda r: 120.0)
    soon, _ = schedule.retime([rail()], dt(5, 6, 3), now=dt(5, 6, 0))
    assert soon[0]["wait_s"] == 420 + 120 and "observed delay 120 s" in soon[0]["provenance"]    # 06:04 buffer -> 06:10 train, +2 min late
    far, _ = schedule.retime([rail()], dt(5, 6, 3), now=dt(5, 3, 0))           # 3 hours away: typical, not observed
    assert far[0]["wait_s"] == 420 and "observed" not in far[0]["provenance"]


def test_a_line_without_a_timetable_falls_back_to_a_labelled_estimate(feed):
    legs, arrive = schedule.retime([rail(line="X|0", route_id="X", est_wait=300, est_ride=900)], dt(5, 6, 3), now=dt(5, 6, 0))
    assert legs[0]["wait_s"] == 300 and legs[0]["scheduled"] is False and "ESTIMATED" in legs[0]["provenance"]
    assert arrive == dt(5, 6, 3) + timedelta(minutes=20)


def test_road_legs_use_the_conditions_at_the_moment_they_start(monkeypatch):
    rush, _ = schedule.retime([road(600)], dt(5, 8, 0), now=dt(5, 8, 0))
    calm, _ = schedule.retime([road(600)], dt(5, 11, 0), now=dt(5, 11, 0))
    night, _ = schedule.retime([road(600)], dt(5, 2, 0), now=dt(5, 2, 0))
    assert rush[0]["duration_s"] == 600 * 1.55 and calm[0]["duration_s"] == 600 * 1.2 and night[0]["duration_s"] == 600
    assert "ESTIMATED" in rush[0]["traffic_source"]
    pickup, _ = schedule.retime([road(600, extra=240)], dt(5, 2, 0), now=dt(5, 2, 0))
    assert pickup[0]["duration_s"] == 840 and pickup[0]["wait_s"] == 240


def test_observed_road_conditions_override_typical_only_for_departures_soon(monkeypatch):
    monkeypatch.setattr(conditions, "_observed", lambda when: 2.0)
    soon, _ = schedule.retime([road(600)], dt(5, 8, 10), now=dt(5, 8, 0))
    far, _ = schedule.retime([road(600)], dt(5, 14, 0), now=dt(5, 8, 0))
    assert soon[0]["duration_s"] == 1200 and soon[0]["traffic_source"] == "observed"
    assert far[0]["duration_s"] == 600 * 1.2 and "ESTIMATED" in far[0]["traffic_source"]


def test_retime_never_mutates_its_input(feed):
    legs = [walk(5), rail(), road()]
    before = json.dumps(legs, sort_keys=True)
    schedule.retime(legs, dt(5, 6, 3), now=dt(5, 6, 0))
    assert json.dumps(legs, sort_keys=True) == before


# -- leave by ----------------------------------------------------------------------------------------------------------------
def test_latest_departure_for_a_fixed_duration_trip_is_deadline_minus_duration():
    d = schedule.latest_departure([walk(30)], dt(5, 8, 30), now=dt(5, 6, 0))
    assert d == dt(5, 8, 0)


def test_latest_departure_is_the_latest_that_arrives_on_time(feed, monkeypatch):
    monkeypatch.setattr(realtime, "route_delay_s", lambda r: None)
    legs = [walk(5), rail(), walk(4)]
    deadline = dt(5, 6, 40)
    d = schedule.latest_departure(legs, deadline, now=dt(5, 5, 0))
    assert d is not None
    assert schedule.retime(legs, d, now=dt(5, 5, 0))[1] <= deadline                                    # respected
    assert schedule.retime(legs, d + timedelta(minutes=1), now=dt(5, 5, 0))[1] > deadline                # and one minute later would be late


def test_no_workable_departure_returns_none(feed, monkeypatch):
    monkeypatch.setattr(realtime, "route_delay_s", lambda r: None)
    assert schedule.latest_departure([walk(60 * 6)], dt(5, 8, 0), now=dt(5, 3, 0), window_min=300) is None


# -- the planner end to end (offline: cached/estimated street legs, local GTFS graph) ------------------------------------------
UNION = (43.6453, -79.3806)
HIGH_PARK = (43.6465, -79.4637)
ALL = ["walk", "bike", "ebike", "bikeshare", "drive", "rideshare", "transit", "park_ride"]


def check_clock_invariants(c):
    legs = c["legs"]
    starts = [datetime.fromisoformat(l["start_iso"]) for l in legs]
    ends = [datetime.fromisoformat(l["end_iso"]) for l in legs]
    assert all(a <= b for a, b in zip(starts, ends)) and all(e == s for e, s in zip(ends, starts[1:])), c["id"]       # one continuous clock
    assert all(a < b for a, b in zip(starts, starts[1:])) or len(legs) == 1
    total = (ends[-1] - starts[0]).total_seconds()
    assert abs(sum(l["duration_s"] for l in legs) - total) < 1.0, c["id"]                                            # parts sum to the total
    assert abs(c["duration_min"] * 60 - total) <= 3.0, c["id"]                                                      # total = last arrival - first departure
    assert c["depart_iso"] == legs[0]["start_iso"] and c["arrive_iso"] == legs[-1]["end_iso"]
    assert c["timezone"] == "America/Toronto"


@pytest.mark.asyncio
async def test_every_candidate_keeps_one_consistent_clock(monkeypatch):
    monkeypatch.setenv("REPLAY", "1")
    plan = await planner.plan(UNION, HIGH_PARK, planner.Constraints(modes=ALL, has_car=True, has_bike=True, depart_at="08:30"))
    avail = [c for c in plan["candidates"] if c.get("available")]
    assert len(avail) >= 5
    for c in avail:
        check_clock_invariants(c)
        assert c["depart"] == "08:30"


@pytest.mark.asyncio
async def test_transit_uses_real_departures_with_a_wait_on_the_leg(monkeypatch):
    monkeypatch.setenv("REPLAY", "1")
    plan = await planner.plan(UNION, HIGH_PARK, planner.Constraints(modes=["transit"], depart_at="08:30"))
    rail_cand = next(c for c in plan["candidates"] if c["id"] == "transit_rail")
    scheduled = [l for l in rail_cand["legs"] if l.get("timing") == "rail"]
    assert scheduled and all("wait_s" in l for l in scheduled)
    if timetable.available() and timetable.services_on("", datetime.now(TZ).date()):   # the shipped feed has service today
        covered = [l for l in scheduled if timetable.covers(l["line_key"])]
        assert covered and all(l["scheduled"] and "GTFS" in l["provenance"] for l in covered)
    bus = next(c for c in plan["candidates"] if c["id"] == "transit_bus")
    assert any("estimated" in l["provenance"].lower() for l in bus["legs"])              # the generic bus is labelled an estimate


@pytest.mark.asyncio
async def test_arrive_by_plans_backwards_and_is_respected(monkeypatch):
    monkeypatch.setenv("REPLAY", "1")
    now = datetime.now(TZ)
    deadline_hm = (now + timedelta(hours=4)).strftime("%H:%M")
    plan = await planner.plan(UNION, HIGH_PARK, planner.Constraints(modes=ALL, has_car=True, arrive_by=deadline_hm))   # no depart_at: backwards
    done = [c for c in plan["candidates"] if c.get("available")]
    assert done
    on_time = [c for c in done if c["meets_deadline"]]
    assert {c["id"] for c in on_time} >= {"walk", "drive", "rideshare"}            # four hours is plenty for those
    for c in done:
        check_clock_invariants(c)
        deadline = datetime.fromisoformat(c["deadline_iso"])
        if c["meets_deadline"]:
            assert datetime.fromisoformat(c["arrive_iso"]) <= deadline
            assert c["leave_by"] == c["depart"]                                  # each route leaves at its own latest workable time
            assert datetime.fromisoformat(c["depart_iso"]) >= now.replace(second=0, microsecond=0)
        else:                                                                    # e.g. a sparse GO timetable: say so, with the earliest arrival
            assert datetime.fromisoformat(c["arrive_iso"]) > deadline and c["earliest_arrival"] == c["arrive"]


@pytest.mark.asyncio
async def test_an_impossible_deadline_says_so_and_gives_the_earliest_arrival(monkeypatch):
    monkeypatch.setenv("REPLAY", "1")
    deadline_hm = (datetime.now(TZ) + timedelta(minutes=3)).strftime("%H:%M")
    plan = await planner.plan(UNION, HIGH_PARK, planner.Constraints(modes=["walk", "drive", "transit"], has_car=True, arrive_by=deadline_hm))
    for c in [c for c in plan["candidates"] if c.get("available")]:
        assert c["meets_deadline"] is False and c["earliest_arrival"] == c["arrive"]
        assert any("Can't arrive by" in n and c["earliest_arrival"] in n for n in c["notes"])
        assert "leave_by" not in c or datetime.fromisoformat(c["depart_iso"]) <= datetime.fromisoformat(c["arrive_iso"])


@pytest.mark.asyncio
async def test_a_deadline_that_has_passed_today_means_tomorrow(monkeypatch):
    monkeypatch.setenv("REPLAY", "1")
    now = datetime.now(TZ)
    if now.hour < 1:
        pytest.skip("an hour ago was yesterday")
    deadline_hm = (now - timedelta(hours=1)).strftime("%H:%M")
    plan = await planner.plan(UNION, HIGH_PARK, planner.Constraints(modes=["walk", "transit"], arrive_by=deadline_hm))
    c = next(c for c in plan["candidates"] if c.get("available") and c["id"] == "walk")
    assert datetime.fromisoformat(c["deadline_iso"]).date() == (now + timedelta(days=1)).date()
    assert c["meets_deadline"] is True and datetime.fromisoformat(c["depart_iso"]) > now


@pytest.mark.asyncio
async def test_forward_with_a_departure_time_checks_the_deadline_and_still_offers_leave_by(monkeypatch):
    monkeypatch.setenv("REPLAY", "1")
    plan = await planner.plan(UNION, HIGH_PARK, planner.Constraints(modes=["walk"], depart_at="08:00", arrive_by="08:30"))
    walk_c = plan["candidates"][0]
    assert walk_c["depart"] == "08:00" and walk_c["meets_deadline"] is False and walk_c["earliest_arrival"] == walk_c["arrive"]
    plan = await planner.plan(UNION, HIGH_PARK, planner.Constraints(modes=["rideshare"], depart_at="08:00", arrive_by="12:00"))
    c = plan["candidates"][0]
    assert c["meets_deadline"] is True and c["leave_by"] > "08:00"


# -- trips ---------------------------------------------------------------------------------------------------------------------
def leg_cand(cid, minutes, timing=None):
    leg = {"mode": "walk", "label": cid, "from": [43.65, -79.40], "to": [43.66, -79.38], "distance_km": 1.0, "duration_s": minutes * 60,
           "geometry": [[43.65, -79.40], [43.66, -79.38]], "cost": 0.0, "co2_kg": 0.0}
    if timing:
        leg.update(timing)
    return {"id": cid, "title": cid, "available": True, "legs": [leg], "duration_min": minutes, "cost": 1.0, "co2_kg": 0.1, "transfers": 0,
            "walk_m": 0, "bag_friendliness": 1.0, "notes": []}


@pytest.mark.asyncio
async def test_trip_legs_are_timed_at_the_moment_they_really_start(monkeypatch):
    trip._cache.clear()

    async def plan(o, d, c):
        return {"candidates": [leg_cand("drive", 10, {"mode": "car", "timing": "road", "base_s": 600})], "traffic_multiplier": 1.0, "data_notes": []}

    monkeypatch.setattr(trip.planner, "plan", plan)
    A = {"name": "A", "lat": 43.65, "lon": -79.40}
    B = {"name": "B", "lat": 43.66, "lon": -79.38}
    C = {"name": "C", "lat": 43.70, "lon": -79.36}
    cons = planner.Constraints(modes=ALL, has_car=True, depart_at="06:50")
    out = await trip.plan_trip(A, [{**B, "type": "stop", "dwell_min": 20, "window": None}], C, cons)
    c = out["candidates"][0]
    # leg 1 starts 06:50 (free flow x1.0), the stop lasts 20 min, leg 2 starts at about 07:19 in the rush (x1.55)
    assert c["legs"][0]["duration_s"] == 600
    second = [l for l in c["legs"] if l["segment"] == 1 and l.get("timing") == "road"][0]       # (leg 2 also has the walk back to the car)
    assert second["duration_s"] == 600 * 1.55 and second["traffic_source"].startswith("typical")


@pytest.mark.asyncio
async def test_trip_leave_by_uses_the_deadline_tomorrow_when_it_has_passed(monkeypatch):
    trip._cache.clear()

    async def plan(o, d, c):
        return {"candidates": [leg_cand("walk", 30)], "traffic_multiplier": 1.0, "data_notes": []}

    monkeypatch.setattr(trip.planner, "plan", plan)
    A = {"name": "A", "lat": 43.65, "lon": -79.40}
    B = {"name": "B", "lat": 43.66, "lon": -79.38}
    C = {"name": "C", "lat": 43.70, "lon": -79.36}
    cons = planner.Constraints(modes=ALL, depart_at="09:00", arrive_by="08:00")          # 08:00 is before the 09:00 departure: tomorrow
    out = await trip.plan_trip(A, [{**B, "type": "stop", "dwell_min": 0, "window": None}], C, cons)
    c = out["candidates"][0]
    assert c["meets_deadline"] is True and c["leave_by"] == "07:00"                       # 60 min of walking: leave by 07:00 the next morning


# -- choosing paths that actually run ------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_line_with_no_service_then_is_not_chosen_for_the_path(monkeypatch):
    """Kipling Station to Union Station: GO's Milton line is the cheapest path by headway, but it does not run on Sundays."""
    import math
    from backend import transit

    monkeypatch.setenv("REPLAY", "1")
    sunday, monday = dt(18, 8, 0), dt(19, 8, 0)
    milton = "GO_09261126-MI|1"   # Milton -> Union Station
    if not (timetable.available() and timetable.covers(milton)
            and timetable.wait_estimate_s(milton, sunday) == math.inf and timetable.wait_estimate_s(milton, monday) < math.inf):
        pytest.skip("the shipped GTFS feed does not cover these dates")
    kipling, union = (43.6372, -79.5361), (43.6453, -79.3806)
    sun = await transit.rail_option(kipling, union, 8, at=sunday)
    assert sun is not None and not any(l.get("route_id", "").startswith("GO_09261126-MI") for l in sun["legs"])
    assert any(l.get("route_id") in ("1", "2") for l in sun["legs"])                    # the subway runs
    mon = await transit.rail_option(kipling, union, 8, at=monday)
    assert mon is not None


def test_wait_estimate_is_half_the_average_gap_between_real_departures(feed):
    w = timetable.wait_estimate_s("L|0", dt(5, 6, 0))
    assert w == 300.0                                       # trips every 10 minutes: a typical wait is 5 minutes
    assert timetable.wait_estimate_s("NOPE|0", dt(5, 6, 0)) is None                   # no timetable: the caller keeps its estimate
    assert timetable.wait_estimate_s("L|0", dt(5, 6, 0) + timedelta(days=60)) == float("inf")      # outside the feed: no service