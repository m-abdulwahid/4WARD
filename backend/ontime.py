"""On-time probability for a candidate route, and the list of things that could delay it (each with a source and a timestamp).

How it is computed (deterministic, no random sampling; every default is a documented ESTIMATE in backend/config.py ONTIME_CV):

  slack      the minutes between the planned arrival and the deadline (a plain trip with no deadline gets a 5 minute on-time
             budget: "arrives within 5 minutes of the plan").
  transit    TTC legs use the stored TTC delay history for that route at that day-of-week and hour (Tiger Data
             ttc_route_snapshot, real polls only, simulated rows excluded): each matching past day is one replay of how late
             the route ran. A TTC leg with no history, and every GO / bus leg, gets a default +/- ONTIME_CV["transit_no_history_s"].
  roads      road legs (drive, rideshare, lift, bus) vary by a default share of their time: ONTIME_CV["road_live"] where TomTom live
             conditions were used, the larger ONTIME_CV["road_typical"] where only the typical-hour profile exists. Congestion on
             one trip is shared by all its road legs, so their spreads add up (they are perfectly correlated).
  walking    walking and cycling legs vary by a small default share.

  P(on time) = average over the replayed days of Phi((slack - that day's transit delay) / sigma), where sigma combines the road
  spread with the independent default spreads. With no history it is one term. A normal spread is a simplification: delays are
  skewed in reality. Confidence says how much of this rests on measured data rather than defaults.
"""
from __future__ import annotations

import math
import time
from datetime import datetime
from statistics import median
from zoneinfo import ZoneInfo

from . import tomtom
from .config import ONTIME_CV
from .db import store

TORONTO = ZoneInfo("America/Toronto")
NO_DEADLINE_BUDGET_S = 300.0
_hist_cache: dict[tuple, tuple[float, dict]] = {}
_alert_cache: tuple[float, list[dict]] | None = None
HIST_TTL_S = 60


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _history(route_id: str, dow: int, hour: int) -> dict[str, float]:
    key = (route_id, dow, hour)
    hit = _hist_cache.get(key)
    if hit and time.time() - hit[0] < HIST_TTL_S:
        return hit[1]
    try:
        got = store.ttc_daily_at_dow_hour(route_id, dow, hour, real_only=True)
    except Exception:  # noqa: BLE001 - the database being away never fails a route
        got = {}
    _hist_cache[key] = (time.time(), got)
    return got


def _alerts(route_ids: set[str]) -> list[dict]:
    global _alert_cache
    if not route_ids:
        return []
    if _alert_cache is None or time.time() - _alert_cache[0] > HIST_TTL_S:
        try:
            _alert_cache = (time.time(), store.recent_alerts(None, 1800))
        except Exception:  # noqa: BLE001
            _alert_cache = (time.time(), [])
    return [a for a in _alert_cache[1] if a["route_id"] in route_ids]


def _dow_hour(leg: dict) -> tuple[int, int]:
    t = datetime.fromisoformat(leg["start_iso"])
    return (t.weekday() + 1) % 7, t.hour  # 0 = Sunday, matching the database


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, TORONTO).isoformat(timespec="seconds") if ts else None


def assess(cand: dict, now: datetime | None = None) -> tuple[dict, list[dict]]:
    """-> (on_time, delay_factors) for one candidate that has timed legs."""
    now = now or datetime.now(TORONTO)
    legs = cand.get("legs") or []
    if cand.get("deadline_iso") and cand.get("arrive_iso"):
        slack = (datetime.fromisoformat(cand["deadline_iso"]) - datetime.fromisoformat(cand["arrive_iso"])).total_seconds()
        slack_basis = f"deadline {cand['deadline_iso'][11:16]}"
    else:
        slack, slack_basis = NO_DEADLINE_BUDGET_S, "5 minute on-time budget (no deadline given)"

    sigma_road = 0.0
    sigma_other_sq = 0.0
    per_leg_daily: list[dict[str, float]] = []
    factors: list[dict] = []
    tt_status = tomtom.status()
    live_used, typical_used = False, False
    foot_modes: set[str] = set()
    default_transit: list[str] = []
    ttc_routes: list[tuple[str, dict]] = []
    route_ids: set[str] = set()

    for leg in legs:
        dur = float(leg.get("duration_s", 0))
        timing = leg.get("timing")
        if timing in ("road", "bus_est"):
            moving = max(0.0, dur - float(leg.get("wait_s", 0)))
            if "TomTom live" in str(leg.get("traffic_source", "")):
                sigma_road += ONTIME_CV["road_live"] * moving
                live_used = True
            else:
                sigma_road += ONTIME_CV["road_typical"] * moving
                typical_used = True
        elif timing == "rail":
            rid = leg.get("route_id", "")
            route_ids.add(rid)
            hist = {} if rid.startswith(("GO_", "YRT_", "DRT_", "MIWAY_")) else _history(rid, *_dow_hour(leg))
            if hist:
                per_leg_daily.append(hist)
                ttc_routes.append((rid, hist))
            else:
                sigma_other_sq += ONTIME_CV["transit_no_history_s"] ** 2
                default_transit.append(rid)
        elif leg.get("mode") in ("bike", "ebike", "bikeshare"):
            sigma_other_sq += (ONTIME_CV["bike"] * dur) ** 2
            foot_modes.add("cycling")
        elif leg.get("mode") == "walk" and dur >= 120:
            sigma_other_sq += (ONTIME_CV["walk"] * dur) ** 2
            foot_modes.add("walking")
        if leg.get("route_id"):
            route_ids.add(leg["route_id"])

    sigma = math.sqrt(sigma_road ** 2 + sigma_other_sq)
    common = set(per_leg_daily[0]) if per_leg_daily else set()
    for d in per_leg_daily[1:]:
        common &= set(d)
    days = sorted(common)
    totals = [max(0.0, sum(d[day] for d in per_leg_daily)) for day in days] if days else [0.0]
    if sigma > 0:
        pct = 100.0 * sum(_phi((slack - t) / sigma) for t in totals) / len(totals)
    else:
        pct = 100.0 * sum(1.0 for t in totals if t <= slack) / len(totals)
    n_days = len(days)
    measured_transit = bool(per_leg_daily) and n_days >= 5
    has_road = any(l.get("timing") in ("road", "bus_est") for l in legs)
    road_measured = live_used and not typical_used           # every road leg used live conditions
    transit_measured = bool(ttc_routes) and n_days >= 14 and not default_transit
    anything_measured = (live_used and has_road) or measured_transit
    if (road_measured or not has_road) and (transit_measured or not (ttc_routes or default_transit)) and anything_measured:
        confidence = "high"
    elif anything_measured:
        confidence = "medium"
    else:
        confidence = "low"            # everything rests on default assumptions
    on_time = {"pct": int(round(max(0.0, min(100.0, pct)))), "slack_min": round(slack / 60, 1), "sigma_s": round(sigma), "history_days": n_days,
               "confidence": confidence, "estimated": True, "slack_basis": slack_basis, "as_of": now.isoformat(timespec="seconds"),
               "basis": "Normal spread around the plan; TTC legs replay stored delay history, other legs use default variability (ESTIMATED)"
               + ("" if measured_transit or not ttc_routes else f"; only {n_days} matching day(s) of TTC history")}

    # What could delay you: each item names its source and when that source was last updated.
    road_legs = [l for l in legs if l.get("timing") in ("road", "bus_est")]
    if road_legs:
        if live_used:
            fresh = [l for l in road_legs if "TomTom live" in str(l.get("traffic_source", ""))]
            worst = max(float(l.get("traffic_factor", 1.0)) for l in fresh)
            factors.append({"id": "traffic-live", "label": "Downtown traffic right now",
                            "detail": f"Roads on this route are running about x{worst:.2f} slower than free flow (live, {len(fresh)} road leg(s))",
                            "source": tomtom.LABEL_LIVE, "as_of": tt_status.get("as_of"), "estimated": False, "impact": worst})
        if typical_used or not live_used:
            h = datetime.fromisoformat(road_legs[0]["start_iso"]).hour
            factors.append({"id": "traffic-typical", "label": "Typical traffic for the hour",
                            "detail": f"No live conditions for part of this route: the typical {h:02d}:00 profile is used"
                                      + ("" if tt_status.get("live") else f" (TomTom live data is not in use: {tt_status.get('state')})"),
                            "source": "Typical-hour profile (ESTIMATED)", "as_of": None, "estimated": True, "impact": 1.0})
        cv = ONTIME_CV["road_live"] if live_used and not typical_used else ONTIME_CV["road_typical"]
        factors.append({"id": "road-variability", "label": "Road time can vary",
                        "detail": f"Road legs can take about +/-{round(cv * 100)}% longer or shorter (default assumption)",
                        "source": "Default assumption (ESTIMATED), config ONTIME_CV", "as_of": None, "estimated": True, "impact": cv})
    if foot_modes:
        cv = ONTIME_CV["bike"] if "cycling" in foot_modes else ONTIME_CV["walk"]
        factors.append({"id": "foot-variability", "label": " and ".join(sorted(foot_modes)).capitalize() + " pace",
                        "detail": f"Time on foot or by bike can vary by about +/-{round(cv * 100)}% (default assumption)",
                        "source": "Default assumption (ESTIMATED), config ONTIME_CV", "as_of": None, "estimated": True, "impact": cv})
    for rid, hist in ttc_routes:
        vals = list(hist.values())
        factors.append({"id": f"ttc-{rid}", "label": f"TTC route {rid} history",
                        "detail": f"Usually {median(vals):+.0f} s versus schedule at this day and hour (median of {len(vals)} day(s))",
                        "source": "Tiger Data ttc_route_snapshot (TTC GTFS-RT polls)", "as_of": max(hist), "estimated": len(vals) < 14, "impact": abs(median(vals)) / 60})
    for rid in default_transit:
        factors.append({"id": f"transit-{rid}", "label": f"Route {rid}: no delay history",
                        "detail": f"No stored delay history for this route and hour: assumed +/-{round(ONTIME_CV['transit_no_history_s'])} s per boarding",
                        "source": "Default assumption (ESTIMATED)", "as_of": None, "estimated": True, "impact": ONTIME_CV["transit_no_history_s"] / 60})
    for a in _alerts(route_ids):
        factors.append({"id": f"alert-{a['alert_id']}-{a['route_id']}", "label": f"TTC alert on route {a['route_id']}",
                        "detail": (a.get("header") or a.get("effect") or "Service alert")[:160],
                        "source": "TTC service alerts (GTFS-RT), stored in Tiger Data", "as_of": _iso(a.get("last_seen")), "estimated": False, "impact": 5.0})
    factors.sort(key=lambda f: -float(f.get("impact", 0)))
    return on_time, factors[:8]


def annotate(cands: list[dict], now: datetime | None = None) -> None:
    """Sets cand["on_time"] and cand["delay_factors"] on every available candidate with timed legs. Never raises."""
    for c in cands:
        if not c.get("available") or not c.get("legs") or not c["legs"][0].get("start_iso"):
            continue
        try:
            c["on_time"], c["delay_factors"] = assess(c, now)
        except Exception:  # noqa: BLE001 - a missing estimate must never break route computation
            continue
