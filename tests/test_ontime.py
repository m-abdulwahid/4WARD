"""On-time probability and the "what could delay you" list."""
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend import ontime, planner, tomtom
from bots import optimize

TZ = ZoneInfo("America/Toronto")
START = datetime(2026, 10, 5, 8, 0, tzinfo=TZ)   # a Monday


def leg(kind, minutes, start=START, **kw):
    base = {"mode": "walk", "label": kind, "from": [0, 0], "to": [0, 0], "distance_km": 1, "duration_s": minutes * 60, "geometry": [[0, 0]],
            "start_iso": start.isoformat(), "wait_s": 0}
    if kind == "road":
        base.update(mode="car", timing="road", base_s=minutes * 60 / 1.55, traffic_source="typical hour (ESTIMATED)", traffic_factor=1.55)
    elif kind == "live_road":
        base.update(mode="car", timing="road", base_s=minutes * 60 / 2.0, traffic_source=tomtom.LABEL_LIVE, traffic_factor=2.0)
    elif kind == "rail":
        base.update(mode="subway", timing="rail", route_id="2", wait_s=120)
    base.update(kw)
    return base


def cand(legs, slack_min=None, **kw):
    arrive = datetime.fromisoformat(legs[-1]["start_iso"]) + timedelta(seconds=legs[-1]["duration_s"])
    c = {"id": "x", "available": True, "legs": legs, "arrive_iso": arrive.isoformat(), **kw}
    if slack_min is not None:
        c["deadline_iso"] = (arrive + timedelta(minutes=slack_min)).isoformat()
    return c


@pytest.fixture(autouse=True)
def no_history_no_alerts(monkeypatch):
    monkeypatch.setattr(ontime, "_history", lambda *a: {})
    monkeypatch.setattr(ontime, "_alerts", lambda ids: [])


def pct(c):
    return ontime.assess(c, START)[0]["pct"]


def test_more_slack_means_a_higher_chance_and_none_means_a_coin_flip():
    walk = [leg("walk", 30)]
    assert pct(cand(walk, slack_min=0)) == 50
    assert pct(cand(walk, slack_min=2)) > 50
    assert pct(cand(walk, slack_min=10)) > 95
    assert pct(cand(walk, slack_min=-3)) < 50                       # already late on paper
    assert pct(cand(walk, slack_min=1)) <= pct(cand(walk, slack_min=5)) <= pct(cand(walk, slack_min=20))


def test_live_road_conditions_have_a_smaller_spread_than_typical_ones_so_the_same_slack_is_safer():
    typical, live = cand([leg("road", 30)], slack_min=5), cand([leg("live_road", 30)], slack_min=5)
    assert pct(live) > pct(typical)
    ot_live, _ = ontime.assess(live, START)
    ot_typ, _ = ontime.assess(typical, START)
    assert ot_live["sigma_s"] < ot_typ["sigma_s"] and ot_live["estimated"] is True        # a default spread is always labelled ESTIMATED


def test_road_legs_share_congestion_so_their_spreads_add_up_while_walks_are_independent():
    one, two = ontime.assess(cand([leg("road", 20)]), START)[0], ontime.assess(cand([leg("road", 20), leg("road", 20)]), START)[0]
    assert two["sigma_s"] == 2 * one["sigma_s"]                                          # perfectly correlated: linear
    w1, w2 = ontime.assess(cand([leg("walk", 20)]), START)[0], ontime.assess(cand([leg("walk", 20), leg("walk", 20)]), START)[0]
    assert abs(w2["sigma_s"] - w1["sigma_s"] * 2 ** 0.5) <= 1                            # independent: square root of the sum of squares


def test_transit_replays_the_stored_delay_history_day_by_day(monkeypatch):
    days = {f"2026-09-{d:02d}": 0.0 for d in range(1, 15)}
    days.update({f"2026-09-{d:02d}": 600.0 for d in range(15, 21)})                    # 6 of 20 days the route ran 10 minutes late
    monkeypatch.setattr(ontime, "_history", lambda rid, dow, hour: days)
    c = cand([leg("rail", 25)], slack_min=4)
    ot, factors = ontime.assess(c, START)
    assert ot["history_days"] == 20 and ot["confidence"] in ("high", "medium")
    assert 60 <= ot["pct"] <= 75                                                         # about the 14 on-time days out of 20
    f = next(x for x in factors if x["id"] == "ttc-2")
    assert f["source"].startswith("Tiger Data") and f["as_of"] == "2026-09-20" and "20 day" in f["detail"]
    more_slack = ontime.assess(cand([leg("rail", 25)], slack_min=15), START)[0]["pct"]
    assert more_slack > ot["pct"]


def test_a_ttc_route_without_history_uses_a_labelled_default_and_says_so():
    ot, factors = ontime.assess(cand([leg("rail", 25)], slack_min=5), START)
    assert ot["history_days"] == 0 and ot["confidence"] == "low"
    f = next(x for x in factors if x["id"] == "transit-2")
    assert f["estimated"] is True and "ESTIMATED" in f["source"] and f["as_of"] is None


def test_go_and_bus_legs_never_pretend_to_have_ttc_history(monkeypatch):
    seen = []
    monkeypatch.setattr(ontime, "_history", lambda rid, dow, hour: seen.append(rid) or {})
    ontime.assess(cand([leg("rail", 25, route_id="GO_09261126-LW", mode="go_train")]), START)
    assert seen == []                                                                      # GO is not looked up in TTC history


def test_every_delay_item_names_its_source_and_a_timestamp_or_says_it_is_a_default(monkeypatch):
    monkeypatch.setattr(ontime, "_history", lambda rid, dow, hour: {"2026-09-30": 60.0, "2026-10-01": 120.0})
    monkeypatch.setattr(ontime, "_alerts", lambda ids: [{"alert_id": "A1", "route_id": "2", "effect": "DETOUR", "header": "Line 2: delays at Bloor", "last_seen": 1790000000}])
    tomtom._values["queen-spadina"] = {"ratio": 0.5, "current": 10, "free": 20, "at": time.time()}
    c = cand([leg("live_road", 15), leg("rail", 20), leg("road", 10)], slack_min=10)
    ot, factors = ontime.assess(c, START)
    assert factors and len(factors) <= 8
    for f in factors:
        assert f["source"] and f["label"] and f["detail"]
        assert f["as_of"] or f["estimated"], f                                           # a timestamp, or an honest "estimated"
    by = {f["id"]: f for f in factors}
    assert by["traffic-live"]["source"] == tomtom.LABEL_LIVE and by["traffic-live"]["as_of"] and by["traffic-live"]["estimated"] is False
    assert by["traffic-typical"]["estimated"] is True
    assert by["alert-A1-2"]["source"].startswith("TTC service alerts") and by["alert-A1-2"]["as_of"].startswith("2026-")
    assert "TomTom" not in str([f["source"] for f in factors if f["id"] != "traffic-live"])


def test_without_a_deadline_the_budget_is_five_minutes_and_it_says_so():
    ot, _ = ontime.assess(cand([leg("walk", 30)]), START)
    assert ot["slack_min"] == 5.0 and "5 minute" in ot["slack_basis"]
    ot, _ = ontime.assess(cand([leg("walk", 30)], slack_min=7), START)
    assert ot["slack_min"] == 7.0 and "deadline" in ot["slack_basis"]


def test_it_is_deterministic():
    c = cand([leg("road", 20), leg("walk", 10)], slack_min=3)
    assert ontime.assess(c, START)[0] == ontime.assess(c, START)[0]


def test_a_failure_estimating_never_breaks_the_route(monkeypatch):
    def boom(*a):
        raise RuntimeError("database away")

    monkeypatch.setattr(ontime, "assess", boom)
    c = cand([leg("walk", 10)])
    ontime.annotate([c, {"available": False}])
    assert "on_time" not in c


def test_the_reliable_bot_ranks_by_this_probability_for_every_kind_of_route():
    def c(i, minutes, p, cost=1.0):
        return {"id": i, "available": True, "duration_min": minutes, "cost": cost, "co2_kg": 1.0, "on_time": {"pct": p}}

    drive_safe, transit_flaky, walk_unknown = c("drive", 30, 97), c("transit", 20, 62), {"id": "walk", "available": True, "duration_min": 60, "cost": 0, "co2_kg": 0}
    assert optimize.top("reliable", [transit_flaky, walk_unknown, drive_safe], {})["id"] == "drive"
    assert optimize.on_time_pct(walk_unknown) is None
    assert [x["id"] for x in optimize.rank("reliable", [transit_flaky, walk_unknown, drive_safe], {})] == ["drive", "transit", "walk"]
    tie_a, tie_b = c("a", 40, 90), c("b", 30, 90)
    assert optimize.top("reliable", [tie_a, tie_b], {})["id"] == "b"                    # ties go to the faster route
    legacy = {"id": "old", "available": True, "duration_min": 10, "cost": 1, "co2_kg": 1, "reliability": {"reliability_pct": 80}}
    assert optimize.on_time_pct(legacy) == 80.0                                         # the older history-only figure still works


@pytest.mark.asyncio
async def test_every_route_the_planner_returns_has_an_on_time_estimate_and_delay_factors(monkeypatch):
    monkeypatch.setenv("REPLAY", "1")
    plan = await planner.plan((43.6453, -79.3806), (43.6465, -79.4637), planner.Constraints(modes=["walk", "drive", "rideshare", "transit"], has_car=True, depart_at="08:30", arrive_by="09:30"))
    done = [c for c in plan["candidates"] if c.get("available")]
    assert done
    for c in done:
        assert 0 <= c["on_time"]["pct"] <= 100 and c["on_time"]["estimated"] is True and c["delay_factors"]
        assert datetime.fromisoformat(c["on_time"]["as_of"])
    drive = next(c for c in done if c["id"] == "drive")
    assert "traffic-typical" in {f["id"] for f in drive["delay_factors"]}              # TomTom is off in tests: typical, ESTIMATED
