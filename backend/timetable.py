"""Scheduled departures for the lines in data/transit_network.json (built by backend/build_timetable.py).

next_departure(line_key, stop_index, at) answers "when does the next vehicle on this line leave this stop?" from the real
GTFS schedule for that calendar day (weekday flags, added/removed dates, trips running past midnight), instead of an
average headway. Lines with no timetable (agencies whose GTFS is not on this machine) return None and the caller falls back
to an ESTIMATED headway and says so.
"""
from __future__ import annotations

import json
from bisect import bisect_left
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path

FILE = Path(__file__).resolve().parent.parent / "data" / "timetable.json"


@lru_cache(maxsize=1)
def _tt() -> dict | None:
    try:
        raw = json.loads(FILE.read_text())
        raw["_by_prefix"] = {s["prefix"]: s for s in raw["sources"]}
        return raw
    except Exception:
        return None


def reload() -> None:
    _tt.cache_clear()
    _merged.cache_clear()
    _wait_bucket.cache_clear()


def available() -> bool:
    return _tt() is not None


def covers(line_key: str) -> bool:
    t = _tt()
    return bool(t) and line_key in t["lines"]


def _prefix(line_key: str) -> str:
    for p in ("GO_", "YRT_", "DRT_", "MIWAY_"):
        if line_key.startswith(p):
            return p
    return ""


def services_on(prefix: str, d: date) -> set[str]:
    t = _tt()
    src = t["_by_prefix"].get(prefix) if t else None
    if not src:
        return set()
    cal, iso = src["calendar"], d.strftime("%Y%m%d")
    active = {sid for sid, c in cal["service"].items() if c["start"] <= iso <= c["end"] and c["days"][d.weekday()] == "1"}
    active |= set(cal["added"].get(iso, []))
    active -= set(cal["removed"].get(iso, []))
    return active


@lru_cache(maxsize=4096)
def _merged(line_key: str, iso: str) -> tuple[list[int], list[tuple[int, int, int]]]:
    """All trips of a line on one service day as (starts sorted, [(start, first stop index, last stop index)])."""
    t = _tt()
    line = t["lines"][line_key]
    last = len(line["dep"]) - 1
    items: list[tuple[int, int, int]] = []
    for sid in services_on(_prefix(line_key), date.fromisoformat(iso)):
        e = line["trips"].get(sid)
        if not e:
            continue
        items += [(s, 0, last) for s in e["s"]]
        items += [tuple(p) for p in e["p"]]
    items.sort()
    return [i[0] for i in items], items


def next_departure(line_key: str, stop_idx: int, at: datetime) -> datetime | None:
    """Earliest scheduled departure from stop `stop_idx` of the line at or after `at` (timezone-aware), or None when there
    is no timetable for that line or no service on those days."""
    t = _tt()
    if not t or line_key not in t["lines"]:
        return None
    off = t["lines"][line_key]["dep"][stop_idx]
    best = None
    for delta in (-1, 0, 1):  # a trip that started yesterday can still be running after midnight
        day = at.date() + timedelta(days=delta)
        mid = datetime.combine(day, time(0), tzinfo=at.tzinfo)
        starts, items = _merged(line_key, day.isoformat())
        if not items:
            continue
        thr = (at - mid).total_seconds() - off
        for j in range(bisect_left(starts, thr), len(items)):
            s, f, l = items[j]
            if f <= stop_idx < l:
                cand = mid + timedelta(seconds=s + off)
                if best is None or cand < best:
                    best = cand
                break
    return best


def ride_seconds(line_key: str, from_idx: int, to_idx: int) -> float | None:
    """Scheduled in-vehicle time between two stops of a line (arrival at `to` minus departure from `from`)."""
    t = _tt()
    if not t or line_key not in t["lines"]:
        return None
    line = t["lines"][line_key]
    return float(line["arr"][to_idx] - line["dep"][from_idx])


@lru_cache(maxsize=16384)
def _wait_bucket(line_key: str, bucket: int, tz_name: str, horizon_s: int) -> float | None:
    t = _tt()
    if not t or line_key not in t["lines"]:
        return None
    from zoneinfo import ZoneInfo

    at = datetime.fromtimestamp(bucket * 600, tz=ZoneInfo(tz_name))
    mid = len(t["lines"][line_key]["dep"]) // 2  # a stop most trips serve, so short-turn trips count too
    times: list[datetime] = []
    cur = at
    for _ in range(12):
        d = next_departure(line_key, mid, cur)
        if d is None or (d - at).total_seconds() > horizon_s:
            break
        times.append(d)
        cur = d + timedelta(seconds=1)
    if not times:
        return float("inf")
    if len(times) == 1:
        return max(0.0, (times[0] - at).total_seconds())
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
    return sum(gaps) / len(gaps) / 2  # a typical wait: half the average gap between departures


def wait_estimate_s(line_key: str, at: datetime, horizon_s: int = 3 * 3600) -> float | None:
    """How long you would typically wait for a vehicle of this line around `at`, from the real schedule (half the average gap
    between departures over the next `horizon_s`). math.inf = the timetable has no departures then (no service today or
    tonight); None = no timetable for this line, so the caller keeps its ESTIMATED headway. Used to pick transit paths
    that actually run, not to time them: schedule.retime() does the timing."""
    tzname = getattr(at.tzinfo, "key", "America/Toronto")
    return _wait_bucket(line_key, int(at.timestamp() // 600), tzname, horizon_s)