"""Reliability scoring: 'made the deadline on X% of past weekdays', from real TTC delay history.

For each transit leg that carries a route_id, pull its historical per-day delay at that leg's
day-of-week/hour from Tiger Data (db/schema.sql's ttc_route_snapshot), then replay: for every
historical date where every transit leg in the route has an observation, sum that date's delays
and check whether the route would still have landed inside its slack. No LLM, no invented numbers;
this reads the same hypertable the dashboard's Insights chart reads. Thin on early data (the
collector just started), honest about it via history_days.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .db import store

DEFAULT_SLACK_S = 5 * 60  # no arrive_by given: how often this route beats a flat 5-minute budget


def _leg_dow_hour(depart: datetime, legs: list[dict], idx: int) -> tuple[int, int]:
    """Day-of-week (0=Sunday, matching SQLite %w / Postgres extract(dow)) and hour this leg boards,
    from the cumulative duration of the legs before it."""
    offset_s = sum(l["duration_s"] for l in legs[:idx])
    t = depart + timedelta(seconds=offset_s)
    return (t.weekday() + 1) % 7, t.hour  # Python Monday=0 -> Sunday=0 convention used by the DB


def _slack_s(route_duration_s: float, constraints: dict, depart: datetime) -> float:
    arrive_by = constraints.get("arrive_by")
    if not arrive_by:
        return DEFAULT_SLACK_S
    h, m = arrive_by.split(":")[:2]
    deadline = depart.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
    return (deadline - depart).total_seconds() - route_duration_s


def combine(per_leg_daily: list[dict[str, float]], slack_s: float) -> dict | None:
    """Pure replay step, kept separate from the DB fetch so it's unit-testable without a database."""
    if not per_leg_daily:
        return None
    common_dates = set(per_leg_daily[0])
    for d in per_leg_daily[1:]:
        common_dates &= set(d)
    if not common_dates:
        return None
    passes = sum(1 for date in common_dates if sum(d[date] for d in per_leg_daily) <= slack_s)
    return {"reliability_pct": round(100 * passes / len(common_dates)), "history_days": len(common_dates)}


def score_route(route: dict, constraints: dict, depart: datetime) -> dict | None:
    legs = route.get("legs") or []
    transit_idxs = [i for i, l in enumerate(legs) if l.get("route_id")]
    if not transit_idxs:
        return None
    slack_s = _slack_s(sum(l["duration_s"] for l in legs), constraints, depart)
    per_leg_daily = []
    for i in transit_idxs:
        dow, hour = _leg_dow_hour(depart, legs, i)
        daily = store.ttc_daily_at_dow_hour(legs[i]["route_id"], dow, hour)
        if daily:
            per_leg_daily.append(daily)
    out = combine(per_leg_daily, slack_s)
    if out:
        out["basis"] = "arrive_by deadline" if constraints.get("arrive_by") else "5 min on-time budget (no deadline given)"
    return out


def annotate(candidates: list[dict], constraints: dict, depart: datetime) -> None:
    """Mutates candidates in place: sets candidate['reliability'] for every one with transit legs
    and enough matching history, otherwise leaves it unset (the UI shows 'not enough history yet')."""
    for c in candidates:
        if not c.get("available"):
            continue
        try:
            rel = score_route(c, constraints, depart)
        except Exception:
            rel = None  # Tiger Data unreachable etc. must never break route computation
        if rel:
            c["reliability"] = rel
