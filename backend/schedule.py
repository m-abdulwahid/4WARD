"""Time accuracy: turn a candidate's legs into a real clock schedule, forwards from a departure or backwards from a deadline.

A leg carries a `timing` tag saying what its duration depends on:
  (none)    fixed duration: walking, cycling, a Bike Share ride, parking search and walk
  road      base_s x the road factor at the moment the leg starts (+ fixed_extra_s, e.g. a rideshare pickup wait)
  bus_est   ESTIMATED generic bus: base_s x factor/1.2 plus an estimated wait
  rail      a scheduled TTC/GO vehicle: wait for the next real departure, then the scheduled ride

retime() walks the legs in order and fills in every leg's start, end, duration and wait, so by construction
  total duration == last arrival - first departure == sum of the legs' durations (waits included).
latest_departure() finds the latest departure that still arrives by a deadline ("leave by 7:42").
Everything is Toronto local time.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import conditions, realtime, timetable
from .config import BOARDING_BUFFER_S, LEAVE_BY_SEARCH_MIN, REALTIME_HORIZON_MIN, TRANSFER_BUFFER_S

TORONTO = ZoneInfo("America/Toronto")


def _hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def retime(legs: list[dict], depart: datetime, now: datetime | None = None) -> tuple[list[dict], datetime]:
    """Copy of `legs` timed from `depart`, and the arrival time. Never mutates the input legs."""
    now = now or datetime.now(TORONTO)
    out: list[dict] = []
    t = depart
    boarded = False
    for leg in legs:
        l = dict(leg)
        timing = leg.get("timing")
        wait = float(leg.get("wait_s", 0) or 0) if timing is None else 0.0
        if timing == "road":
            f, prov = conditions.road_factor(t, now, leg)
            wait = float(leg.get("fixed_extra_s", 0))
            dur = leg["base_s"] * f + wait
            l.update(traffic_factor=round(f, 2), traffic_source=prov)
        elif timing == "bus_est":
            f, prov = conditions.road_factor(t, now, leg)
            wait = float(leg["est_wait_s"])
            dur = leg["base_s"] * (f / 1.2) + wait
            l.update(traffic_factor=round(f, 2), traffic_source=prov)
        elif timing == "rail":
            buffer = TRANSFER_BUFFER_S if boarded else BOARDING_BUFFER_S
            boarded = True
            dep = timetable.next_departure(leg["line_key"], leg["from_idx"], t + timedelta(seconds=buffer)) if timetable.available() else None
            ride = timetable.ride_seconds(leg["line_key"], leg["from_idx"], leg["to_idx"]) if dep is not None else None
            if dep is not None and ride is not None:
                source = "scheduled departure (GTFS)"
                delay = None
                if abs((dep - now).total_seconds()) <= REALTIME_HORIZON_MIN * 60:
                    delay = realtime.route_delay_s(leg["route_id"])
                if delay:
                    dep = dep + timedelta(seconds=delay)
                    source = f"scheduled departure (GTFS) + observed delay {round(delay)} s (TTC realtime)"
                wait = max(0.0, (dep - t).total_seconds())
                if leg.get("mode") == "lrt_streetcar" and leg.get("corridors"):
                    # a streetcar shares the road: stretch or shrink its scheduled run time by live vs typical conditions
                    c = conditions.leg_conditions(leg, dep, now)
                    if c["source"] == conditions.LIVE:
                        adj = max(0.9, min(2.5, c["factor"] / c["typical"]))
                        ride = ride * adj
                        source += f" + road conditions x{adj:.2f} ({conditions.LIVE})"
                        l.update(traffic_factor=round(c["factor"], 2), traffic_source=conditions.LIVE)
                dur = wait + ride
                l.update(provenance=source, departs_at=_hm(dep), scheduled=True)
            else:  # no timetable for this line, or no service that day: ESTIMATED headway
                wait = float(leg["est_wait_s"])
                dur = wait + float(leg["est_ride_s"])
                l.update(provenance="gtfs-static (headway ESTIMATED: no timetable for this line or day)", scheduled=False)
        else:
            dur = float(leg["duration_s"])
        l.update(duration_s=dur, wait_s=wait, depart_at=_hm(t), arrive_at=_hm(t + timedelta(seconds=dur)),
                 start_iso=t.isoformat(timespec="seconds"), end_iso=(t + timedelta(seconds=dur)).isoformat(timespec="seconds"))
        out.append(l)
        t = t + timedelta(seconds=dur)
    return out, t


def latest_boundary(feasible, lo: datetime, hi: datetime) -> datetime | None:
    """The latest whole-minute time in [lo, hi] for which feasible(time) holds, by bisection, or None when even `lo` fails.
    Arrival is a non-decreasing staircase of the departure time (later departure never arrives earlier, apart from the
    edge where the traffic profile eases), so the feasible departures form an interval ending at the answer."""
    lo, hi = lo.replace(second=0, microsecond=0), hi.replace(second=0, microsecond=0)
    if not feasible(lo):
        return None
    if feasible(hi):
        return hi
    while hi - lo > timedelta(minutes=1):
        mid = (lo + (hi - lo) / 2).replace(second=0, microsecond=0)
        if mid <= lo:
            break
        if feasible(mid):
            lo = mid
        else:
            hi = mid
    return lo


def latest_departure(legs: list[dict], deadline: datetime, now: datetime | None = None,
                     window_min: int = LEAVE_BY_SEARCH_MIN) -> datetime | None:
    """The latest departure (whole minutes) whose arrival is at or before `deadline`, or None if even leaving
    `window_min` earlier is too late. Waits make arrival a staircase in the departure time."""
    now = now or datetime.now(TORONTO)
    return latest_boundary(lambda d: retime(legs, d, now)[1] <= deadline, deadline - timedelta(minutes=window_min), deadline)