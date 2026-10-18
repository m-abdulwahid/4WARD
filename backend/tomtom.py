"""TomTom live traffic for the downtown corridors: the LATEST values, in memory only, for a few minutes. Never stored.

Why nothing is stored: TomTom's terms on storing API results are not confirmed to allow it on the free plan (see
CLAUDE.md). So this module never writes a TomTom value, or anything derived from one, to a database, a file or a log. The
values live in a dict for TOMTOM_TTL_S seconds and are gone after that (and on restart). `TOMTOM_STORE` in backend/config.py
exists so storage can be switched on later if TomTom's terms or support allow it; no storage code exists today.

Cost control: a poll is 10 requests (one per corridor segment). It runs only when a plan needs current conditions
(demand-driven, at most one poll per TTL), never in the background, and stops for the day when the request budget
(80% of the free allowance, backend/config.py) is used up. A rejected key, a rate limit or an outage never fails a route: the
caller falls back to the typical-hour profile and the UI says ESTIMATED.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from . import config
from .geo import haversine_km

TORONTO = ZoneInfo("America/Toronto")
URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"


class _NoTomTomUrls(logging.Filter):
    """httpx logs every request URL at INFO, and TomTom's URL carries the API key in its query string. Drop those records so the
    key (and what was asked for) can never reach a log file or console."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "api.tomtom.com" not in record.getMessage()


for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).addFilter(_NoTomTomUrls())
LABEL_LIVE = "TomTom live (not stored)"
RADIUS_KM = 1.2  # a road sample within this distance of a segment point takes that segment's conditions

# Ten downtown corridors (our own choice of points, not TomTom data). TomTom snaps each point to the nearest road segment.
SEGMENTS = [
    {"id": "gardiner-spadina", "name": "Gardiner Expressway near Spadina", "lat": 43.63823, "lon": -79.39536},
    {"id": "dvp-bloor", "name": "Don Valley Parkway near Bloor", "lat": 43.6738, "lon": -79.3625},
    {"id": "lakeshore-jarvis", "name": "Lake Shore Blvd E at Jarvis", "lat": 43.6452, "lon": -79.3700},
    {"id": "yonge-bloor", "name": "Yonge St at Bloor", "lat": 43.6709, "lon": -79.3866},
    {"id": "university-queen", "name": "University Ave at Queen", "lat": 43.6508, "lon": -79.3865},
    {"id": "queen-spadina", "name": "Queen St W at Spadina", "lat": 43.6486, "lon": -79.3954},
    {"id": "bloor-spadina", "name": "Bloor St W at Spadina", "lat": 43.6677, "lon": -79.4040},
    {"id": "spadina-college", "name": "Spadina Ave at College", "lat": 43.6580, "lon": -79.3999},
    {"id": "front-jarvis", "name": "Front St E at Jarvis", "lat": 43.6498, "lon": -79.3708},
    {"id": "dundas-yonge", "name": "Dundas St at Yonge", "lat": 43.6561, "lon": -79.3802},
]
_BY_ID = {s["id"]: s for s in SEGMENTS}

_transport: httpx.AsyncBaseTransport | None = None  # tests inject a mock transport here
_lock = asyncio.Lock()
_values: dict[str, dict] = {}  # segment id -> {"ratio", "current", "free", "at" (epoch)}; memory only
_state = {"status": "idle", "detail": "", "backoff_until": 0.0, "day": "", "used_today": 0, "last_attempt": 0.0, "last_ok": 0.0}


def _key() -> str:
    import os

    return os.environ.get("TOMTOM_API_KEY", "").strip()


def store_requested() -> bool:
    """True if TOMTOM_STORE was switched on. There is deliberately no storage implementation: this only lets the UI and
    /conditions say that storing was requested but is not built, so a flag flip can never silently start storing."""
    return config.TOMTOM_STORE


def _today() -> str:
    return datetime.now(TORONTO).strftime("%Y-%m-%d")


def _budget_left() -> int:
    if _state["day"] != _today():
        _state.update(day=_today(), used_today=0)
    return config.TOMTOM_DAILY_REQUEST_BUDGET - _state["used_today"]


def latest(now: float | None = None) -> dict[str, dict]:
    """Segments whose values are still fresh (younger than the TTL). Expired ones are dropped from memory here."""
    now = time.time() if now is None else now
    for sid in [s for s, v in _values.items() if now - v["at"] > config.TOMTOM_TTL_S]:
        _values.pop(sid, None)
    return dict(_values)


def _parse(body: dict) -> tuple[float, float] | None:
    d = body.get("flowSegmentData") or {}
    cur, free = d.get("currentSpeed"), d.get("freeFlowSpeed")
    if not isinstance(cur, (int, float)) or not isinstance(free, (int, float)) or free <= 0 or d.get("roadClosure") is True:
        return None  # a closed or unusable segment says nothing about how fast traffic moves
    return float(cur), float(free)


async def _one(client: httpx.AsyncClient, seg: dict, key: str) -> tuple[str, int, tuple[float, float] | None]:
    try:
        r = await client.get(URL, params={"key": key, "point": f"{seg['lat']},{seg['lon']}", "unit": "KMPH"})
    except Exception:  # noqa: BLE001 - network trouble of any kind: the caller falls back
        return seg["id"], 0, None
    if r.status_code != 200:
        return seg["id"], r.status_code, None
    try:
        return seg["id"], 200, _parse(r.json())
    except Exception:  # noqa: BLE001
        return seg["id"], 200, None


async def refresh(force: bool = False) -> None:
    """Poll the ten segments if the cached values are stale. Never raises, never logs values, never stores anything."""
    key = _key()
    if not key:
        _state.update(status="no_key", detail="TOMTOM_API_KEY is not set")
        return
    now = time.time()
    if not force and latest(now) and now - _state["last_ok"] < config.TOMTOM_TTL_S:
        return
    if now < _state["backoff_until"]:
        return
    async with _lock:
        now = time.time()
        if not force and latest(now) and now - _state["last_ok"] < config.TOMTOM_TTL_S:
            return
        if _budget_left() < len(SEGMENTS):
            _state.update(status="budget_exhausted", detail="today's request budget (80% of the free allowance) is used up")
            return
        _state.update(last_attempt=now, used_today=_state["used_today"] + len(SEGMENTS))
        async with httpx.AsyncClient(timeout=8, transport=_transport) as client:
            results = await asyncio.gather(*[_one(client, s, key) for s in SEGMENTS])
        codes = {c for _, c, _ in results}
        got = [(sid, v) for sid, c, v in results if c == 200 and v]
        if 401 in codes or 403 in codes:
            _state.update(status="key_rejected", detail="TomTom rejected the key", backoff_until=now + 1800)
        elif 429 in codes:
            _state.update(status="rate_limited", detail="TomTom rate limit reached", backoff_until=now + 900)
        elif not got:
            ok_codes = codes == {200}
            _state.update(status="no_data" if ok_codes else "unreachable", detail="no usable segments" if ok_codes else "TomTom could not be reached",
                          backoff_until=now + 120)
        for sid, (cur, free) in got:
            _values[sid] = {"ratio": cur / free, "current": cur, "free": free, "at": now}
        if got:
            _state.update(status="live", detail="", last_ok=now)


def live_factor(segment_id: str, now: float | None = None) -> tuple[float, float] | None:
    """(slowdown factor vs free flow, age in seconds) for a segment, or None when there is no fresh value."""
    v = latest(now).get(segment_id)
    if not v:
        return None
    now = time.time() if now is None else now
    return max(1.0, min(4.0, 1.0 / max(v["ratio"], 0.25))), now - v["at"]


def profile_for(geometry: list[list[float]], spacing_m: float = 200.0) -> dict[str, int]:
    """Metres of a leg's geometry that fall within RADIUS_KM of each corridor segment (nearest one wins). Static geography
    only; it holds no TomTom data, so it can live on the leg. Empty when the leg stays away from downtown."""
    out: dict[str, int] = {}
    if len(geometry) < 2:
        return out
    for a, b in zip(geometry, geometry[1:]):
        d = haversine_km(tuple(a), tuple(b)) * 1000
        n = max(1, int(d / spacing_m))
        seg_m = d / n
        for k in range(n):
            t = (k + 0.5) / n
            p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            best, best_d = None, RADIUS_KM
            for s in SEGMENTS:
                dd = haversine_km(p, (s["lat"], s["lon"]))
                if dd < best_d:
                    best, best_d = s["id"], dd
            if best:
                out[best] = out.get(best, 0) + round(seg_m)
    return out


def status() -> dict:
    """What the UI shows. No TomTom values here, only whether live conditions are in use."""
    fresh = latest()
    live = bool(fresh)
    age = round(time.time() - min(v["at"] for v in fresh.values())) if live else None
    state = "live" if live else (_state["status"] if _state["status"] not in ("live", "idle") else ("idle" if _key() else "no_key"))
    label = LABEL_LIVE if live else "Typical speeds (ESTIMATED): TomTom live data is not in use"
    return {"state": state, "label": label, "live": live, "segments_fresh": len(fresh), "segments_total": len(SEGMENTS),
            "age_s": age, "ttl_s": config.TOMTOM_TTL_S, "detail": _state["detail"], "requests_today": _state["used_today"],
            "daily_request_budget": config.TOMTOM_DAILY_REQUEST_BUDGET, "stored": False, "store_requested": store_requested(),
            "as_of": datetime.fromtimestamp(max(v["at"] for v in fresh.values()), TORONTO).isoformat(timespec="seconds") if live else None}


def reset() -> None:
    """Forget everything (tests, and the 'clear' escape hatch)."""
    _values.clear()
    _state.update(status="idle", detail="", backoff_until=0.0, day="", used_today=0, last_attempt=0.0, last_ok=0.0)
