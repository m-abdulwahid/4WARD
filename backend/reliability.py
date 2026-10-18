"""On-time scoring: 'how likely is this route to make the deadline', the Reliable bot's one job.

Two kinds of leg, combined deterministically (no Monte Carlo, nothing seeded):
  - Transit legs with a stored route_id (schedule.py timing="rail"): replayed against real TTC delay
    history from Tiger Data (db/schema.sql's ttc_route_snapshot), one pass/fail per historical date.
  - Road and generic-bus legs (timing="road"/"bus_est"): TomTom is never stored, so there is no history
    to replay. Modelled instead with a documented default distribution (config.ONTIME_CV) via a closed-
    form Normal CDF -- a probability, not a sampled guess.
A route with only road legs (e.g. a pure drive) still gets a percentage, from the Normal model alone;
a route with only transit legs is scored exactly as before Phase 6 (this file's behaviour for that case
is unchanged). Thin on early data (the collector just started), honest about it via history_days and
confidence. For transfers, a historical day's delay that eats past the scheduled transfer buffer adds
that day's real typical wait for the next connecting departure (backend/timetable.py), not a flat guess.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from statistics import NormalDist

from . import timetable
from .config import ONTIME_CV, TRANSFER_BUFFER_S
from .db import store

DEFAULT_SLACK_S = 5 * 60  # no arrive_by given: how often this route beats a flat 5-minute budget


def _leg_time(depart: datetime, legs: list[dict], idx: int) -> datetime:
    """When this leg boards, from the cumulative duration of the legs before it."""
    offset_s = sum(l["duration_s"] for l in legs[:idx])
    return depart + timedelta(seconds=offset_s)


def _leg_dow_hour(depart: datetime, legs: list[dict], idx: int) -> tuple[int, int]:
    """Day-of-week (0=Sunday, matching SQLite %w / Postgres extract(dow)) and hour this leg boards."""
    t = _leg_time(depart, legs, idx)
    return (t.weekday() + 1) % 7, t.hour


def _slack_s(route_duration_s: float, constraints: dict, depart: datetime) -> float:
    arrive_by = constraints.get("arrive_by")
    if not arrive_by:
        return DEFAULT_SLACK_S
    h, m = arrive_by.split(":")[:2]
    deadline = depart.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
    return (deadline - depart).total_seconds() - route_duration_s


def _apply_transfer_misses(per_leg_daily: list[dict[str, float]], transit_idxs: list[int], legs: list[dict], depart: datetime) -> list[dict[str, float]]:
    """If a historical day's accumulated delay through earlier legs would have eaten past the buffer
    already built into the nominal schedule, that connection would have been missed: add that day's
    real typical wait for the following departure (not a flat guess) onto this leg's delay for the date."""
    if len(per_leg_daily) < 2:
        return per_leg_daily
    out = [dict(per_leg_daily[0])]
    for pos in range(1, len(per_leg_daily)):
        i = transit_idxs[pos]
        line_key = legs[i].get("line_key")
        boarding_t = _leg_time(depart, legs, i)
        adjusted: dict[str, float] = {}
        for date, delay in per_leg_daily[pos].items():
            if date not in out[pos - 1]:
                continue  # needs an observation for every earlier leg on the same date too
            delay_so_far = sum(out[j].get(date, 0.0) for j in range(pos))
            extra = 0.0
            if delay_so_far > TRANSFER_BUFFER_S and line_key:
                extra = timetable.wait_estimate_s(line_key, boarding_t) or 0.0
            adjusted[date] = delay + extra
        out.append(adjusted)
    return out


def confidence_label(history_days: int) -> str:
    """Matches CLAUDE.md's confidence-ramp language: statistical confidence in the score ramps with
    history_days, capped at 14 (after that, more days don't buy more confidence in this simple model)."""
    capped = min(history_days, 14)
    if capped == 0:
        return "default (no matching history; the percentage comes from the road-timing model only)"
    if capped < 5:
        return "low"
    if capped < 10:
        return "medium"
    return "high"


def combine(per_leg_daily: list[dict[str, float]], slack_s: float, road_sigma_s: float = 0.0) -> dict | None:
    """Pure math, kept separate from the DB fetch so it's unit-testable without a database.
    road_sigma_s = 0: identical to the pre-Phase-6 behaviour (hard pass/fail per historical date).
    road_sigma_s > 0: each historical date's chance of also absorbing the road legs' variability and
    still beating slack_s, via a closed-form Normal(0, road_sigma_s) CDF, averaged across the dates."""
    if not per_leg_daily:
        if road_sigma_s <= 0:
            return None  # nothing to go on: no transit history and no road legs either
        pct = round(100 * NormalDist(0, road_sigma_s).cdf(slack_s))  # pure-road route, one shot, no dates
        return {"reliability_pct": max(0, min(100, pct)), "history_days": 0}
    common_dates = set(per_leg_daily[0])
    for d in per_leg_daily[1:]:
        common_dates &= set(d)
    if not common_dates:
        return None
    if road_sigma_s <= 0:
        passes = sum(1 for date in common_dates if sum(d[date] for d in per_leg_daily) <= slack_s)
        pct = round(100 * passes / len(common_dates))
    else:
        nd = NormalDist(0, road_sigma_s)
        probs = [nd.cdf(slack_s - sum(d[date] for d in per_leg_daily)) for date in common_dates]
        pct = round(100 * sum(probs) / len(probs))
    return {"reliability_pct": max(0, min(100, pct)), "history_days": len(common_dates)}


def _delay_factors(legs: list[dict]) -> list[dict]:
    """'What could delay you': one item per leg with a real live signal (TomTom's current ratio, an
    observed TTC delay folded into a scheduled departure, or an active TTC service alert), each with
    its source and a timestamp; legs with no live signal get one item labelled as a default, not
    omitted. Alerts come from the official GTFS-RT feed (backend/ingest.py's parse_alerts), stored in
    Tiger Data's ttc_alerts table."""
    now = time.time()
    out = []
    for l in legs:
        label = l.get("label", "This leg")
        src = l.get("traffic_source") or ""
        prov = l.get("provenance") or ""
        if src == "TomTom live (not stored)":
            factor = l.get("traffic_factor", 1.0)
            if factor > 1.05:
                out.append({"text": f"{label}: roads are running {factor}x slower than free-flow right now",
                            "source": "TomTom (live, not stored)", "timestamp": now, "default": False})
        elif "observed delay" in prov:
            out.append({"text": f"{label}: {prov}", "source": "TTC realtime (GTFS-RT)", "timestamp": now, "default": False})
        elif "ESTIMATED" in src or "ESTIMATED" in prov:
            out.append({"text": f"{label}: no live delay signal for this leg, timing is a typical-pattern estimate",
                        "source": "typical pattern", "timestamp": now, "default": True})
        if route_id := l.get("route_id"):
            try:
                alerts = store.active_alerts(route_id)
            except Exception:
                alerts = []
            for a in alerts:
                text = a.get("header_text") or f"{a.get('effect') or 'disruption'} ({a.get('cause') or 'unknown cause'})"
                out.append({"text": f"{label}: {text}", "source": "TTC service alert (GTFS-RT)", "timestamp": a["ts"], "default": False})
    return out


def score_route(route: dict, constraints: dict, depart: datetime) -> dict | None:
    legs = route.get("legs") or []
    transit_idxs = [i for i, l in enumerate(legs) if l.get("route_id")]
    road_idxs = [i for i, l in enumerate(legs) if l.get("timing") in ("road", "bus_est")]
    if not transit_idxs and not road_idxs:
        return None  # e.g. a pure walk/bike leg: nothing here has meaningful timing variability
    route_duration_s = sum(l["duration_s"] for l in legs)
    slack_s = _slack_s(route_duration_s, constraints, depart)
    per_leg_daily = []
    for i in transit_idxs:
        dow, hour = _leg_dow_hour(depart, legs, i)
        daily = store.ttc_daily_at_dow_hour(legs[i]["route_id"], dow, hour)
        if daily:
            per_leg_daily.append(daily)
    if per_leg_daily:
        per_leg_daily = _apply_transfer_misses(per_leg_daily, [i for i in transit_idxs if legs[i]["route_id"]], legs, depart)
    road_sigma_s = sum(legs[i]["duration_s"] for i in road_idxs) * ONTIME_CV
    out = combine(per_leg_daily, slack_s, road_sigma_s)
    if out:
        out["basis"] = "arrive_by deadline" if constraints.get("arrive_by") else "5 min on-time budget (no deadline given)"
        out["confidence"] = confidence_label(out["history_days"])
        out["delay_factors"] = _delay_factors(legs)
    return out


def annotate(candidates: list[dict], constraints: dict, depart: datetime) -> None:
    """Mutates candidates in place: sets candidate['reliability'] for every one with transit and/or
    road legs and at least a road-timing model to go on, otherwise leaves it unset (the UI shows
    'not enough history yet')."""
    for c in candidates:
        if not c.get("available"):
            continue
        try:
            # each candidate may leave at its own time (planning backwards from a deadline), so use its own departure
            own = datetime.fromisoformat(c["depart_iso"]) if c.get("depart_iso") else depart
            rel = score_route(c, constraints, own)
        except Exception:
            rel = None  # Tiger Data unreachable etc. must never break route computation
        if rel:
            c["reliability"] = rel
