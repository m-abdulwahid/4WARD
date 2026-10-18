from datetime import datetime

from backend import reliability


def test_leg_dow_hour_matches_db_convention():
    # Monday 2026-01-05, 08:00 depart; a leg starting 90 min in boards at 09:30 Monday.
    depart = datetime(2026, 1, 5, 8, 0)  # a Monday
    legs = [{"duration_s": 90 * 60}, {"duration_s": 600}]
    dow, hour = reliability._leg_dow_hour(depart, legs, 1)
    assert hour == 9
    assert dow == 1  # DB convention: Sunday=0, Monday=1 ... matches Postgres extract(dow)/SQLite %w

    sunday = datetime(2026, 1, 4, 8, 0)  # the day before, a Sunday
    dow0, _ = reliability._leg_dow_hour(sunday, [{"duration_s": 0}], 0)
    assert dow0 == 0


def test_slack_s_uses_arrive_by_when_given():
    depart = datetime(2026, 1, 5, 8, 0)
    s = reliability._slack_s(route_duration_s=20 * 60, constraints={"arrive_by": "08:30"}, depart=depart)
    assert s == 10 * 60  # 30 min window minus a 20 min trip leaves 10 min slack


def test_slack_s_defaults_without_a_deadline():
    depart = datetime(2026, 1, 5, 8, 0)
    assert reliability._slack_s(20 * 60, {}, depart) == reliability.DEFAULT_SLACK_S


def test_combine_counts_only_dates_common_to_every_leg():
    # leg A has day1/day2, leg B only has day1 -> only day1 is replayable
    per_leg = [{"2026-01-05": 60.0, "2026-01-06": 600.0}, {"2026-01-05": 30.0}]
    out = reliability.combine(per_leg, slack_s=120)
    assert out == {"reliability_pct": 100, "history_days": 1}  # 60+30=90 <= 120


def test_combine_fails_days_that_exceed_slack():
    per_leg = [{"2026-01-05": 200.0, "2026-01-06": 10.0}, {"2026-01-05": 10.0, "2026-01-06": 10.0}]
    out = reliability.combine(per_leg, slack_s=50)
    # day1: 210 > 50 (fail), day2: 20 <= 50 (pass) -> 1/2 = 50%
    assert out == {"reliability_pct": 50, "history_days": 2}


def test_combine_returns_none_without_overlap_or_data():
    assert reliability.combine([], slack_s=100) is None
    assert reliability.combine([{"2026-01-05": 10.0}, {"2026-01-06": 10.0}], slack_s=100) is None


def test_score_route_skips_routes_with_no_transit_legs(monkeypatch):
    route = {"legs": [{"mode": "walk", "duration_s": 600}]}
    assert reliability.score_route(route, {}, datetime(2026, 1, 5, 8, 0)) is None


def test_score_route_uses_store_history(monkeypatch):
    route = {"legs": [{"mode": "walk", "duration_s": 60}, {"mode": "subway", "duration_s": 600, "route_id": "1"}]}
    monkeypatch.setattr(reliability.store, "ttc_daily_at_dow_hour", lambda *a, **k: {"2026-01-05": 30.0, "2026-01-06": 30.0})
    out = reliability.score_route(route, {}, datetime(2026, 1, 5, 8, 0))
    assert out["reliability_pct"] == 100
    assert out["history_days"] == 2
    assert out["basis"].startswith("5 min")
    assert out["confidence"] == "low"  # 2 days, capped-at-14 ramp
    assert out["delay_factors"] == []  # no traffic_source/provenance on these bare test legs


# -- Phase 6: road-leg variability (no stored TomTom history, so a closed-form Normal model) -----------------


def test_combine_with_road_sigma_matches_old_behaviour_when_sigma_is_zero():
    per_leg = [{"2026-01-05": 200.0, "2026-01-06": 10.0}, {"2026-01-05": 10.0, "2026-01-06": 10.0}]
    assert reliability.combine(per_leg, slack_s=50, road_sigma_s=0.0) == reliability.combine(per_leg, slack_s=50)


def test_combine_with_road_sigma_softens_a_hard_fail_into_a_probability():
    # day1 (210 > 50) would hard-fail with sigma=0; a wide road sigma gives it real odds instead of zero.
    per_leg = [{"2026-01-05": 200.0}, {"2026-01-05": 10.0}]
    hard = reliability.combine(per_leg, slack_s=50, road_sigma_s=0.0)
    soft = reliability.combine(per_leg, slack_s=50, road_sigma_s=300.0)
    assert hard["reliability_pct"] == 0
    assert 0 < soft["reliability_pct"] < 100


def test_combine_pure_road_route_gets_one_shot_against_the_full_slack():
    # no transit history at all, but a road sigma: NormalDist(0, sigma).cdf(slack) directly, one "day".
    out = reliability.combine([], slack_s=0.0, road_sigma_s=600.0)
    assert out == {"reliability_pct": 50, "history_days": 0}  # slack==mean: exactly 50/50 under a Normal


def test_combine_with_no_history_and_no_road_legs_is_none():
    assert reliability.combine([], slack_s=100, road_sigma_s=0.0) is None


def test_score_route_scores_a_pure_drive_route_with_no_transit_legs(monkeypatch):
    # Phase 6: every route gets a percentage now, including ones the old transit-only version skipped.
    route = {"legs": [{"mode": "car", "duration_s": 600, "timing": "road"}]}
    out = reliability.score_route(route, {}, datetime(2026, 1, 5, 8, 0))
    assert out is not None
    assert 0 <= out["reliability_pct"] <= 100
    assert out["history_days"] == 0
    assert out["confidence"].startswith("default")


def test_confidence_label_ramps_with_history_days_and_caps_at_14():
    assert reliability.confidence_label(0).startswith("default")
    assert reliability.confidence_label(2) == "low"
    assert reliability.confidence_label(7) == "medium"
    assert reliability.confidence_label(14) == "high"
    assert reliability.confidence_label(90) == "high"  # capped, not higher than 14 days' worth


def test_transfer_miss_adds_the_real_next_departure_wait(monkeypatch):
    # leg 0 is so delayed on 01-05 that it eats the transfer buffer before leg 1 boards; the following
    # day is fine. The missed day should absorb leg 1's own delay plus a real wait_estimate_s lookup.
    legs = [
        {"mode": "subway", "duration_s": 600, "route_id": "1", "line_key": "1|N"},
        {"mode": "subway", "duration_s": 600, "route_id": "2", "line_key": "2|E"},
    ]
    route = {"legs": legs}
    daily = iter([
        {"2026-01-05": reliability.TRANSFER_BUFFER_S + 100.0, "2026-01-06": 10.0},  # leg 0
        {"2026-01-05": 5.0, "2026-01-06": 5.0},  # leg 1
    ])
    monkeypatch.setattr(reliability.store, "ttc_daily_at_dow_hour", lambda *a, **k: next(daily))
    monkeypatch.setattr(reliability.timetable, "wait_estimate_s", lambda line_key, at, **k: 240.0)
    out = reliability.score_route(route, {"arrive_by": "08:25"}, datetime(2026, 1, 5, 8, 0))
    # 2026-01-05: 20 min route + (buffer+100)s + 5s + 240s wait, vs a 5 min slack window -> fails
    # 2026-01-06: 20 min route + 10s + 5s, comfortably inside the window -> passes
    assert out["reliability_pct"] == 50
    assert out["history_days"] == 2


def test_delay_factors_reflects_live_signals_on_the_legs():
    legs = [
        {"label": "Drive", "duration_s": 600, "timing": "road", "traffic_source": "TomTom live (not stored)", "traffic_factor": 1.4},
        {"label": "Subway", "duration_s": 600, "route_id": "1", "provenance": "scheduled departure (GTFS) + observed delay 90 s (TTC realtime)"},
        {"label": "Walk", "duration_s": 60},
    ]
    out = reliability._delay_factors(legs)
    assert out[0]["source"] == "TomTom (live, not stored)" and out[0]["default"] is False
    assert out[1]["source"] == "TTC realtime (GTFS-RT)" and "90 s" in out[1]["text"]
    assert len(out) == 2  # the plain walk leg has no live signal and contributes nothing
