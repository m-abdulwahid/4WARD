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
