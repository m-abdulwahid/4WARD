"""City of Toronto road restrictions and closures (open data "Road Restrictions", a live feed), stored in Tiger Data.

Source: https://open.toronto.ca/dataset/road-restrictions/  (feed: https://secure.toronto.ca/opendata/cart/road_restrictions/v3?format=json)
Catalogue facts, checked 2026-10-18: refresh rate "Real-time"; the catalogue entry says "License not specified", and the portal's
standard licence is the Open Government Licence - Toronto (use, copy, redistribute and adapt, commercial use allowed, attribution
required: "Contains information licensed under the Open Government Licence - Toronto"). It is an official open-data API, so
no scraping is involved. Because you asked to be told before it is used, the collector is OFF by default: set
ROAD_RESTRICTIONS_ENABLED=true to switch it on. While it is off nothing is fetched and the road_restrictions table stays empty.
"""
from __future__ import annotations

import json
import os
import re
import time

import httpx

from .config import GTA_BBOX
from .db import store

URL = "https://secure.toronto.ca/opendata/cart/road_restrictions/v3"
ATTRIBUTION = "Contains information licensed under the Open Government Licence - Toronto"
POLL_EVERY_S = 3600


def enabled() -> bool:
    return os.environ.get("ROAD_RESTRICTIONS_ENABLED", "false").strip().lower() in ("1", "true", "yes")


def _ms(v) -> int | None:
    try:
        return int(int(v) / 1000) if v not in (None, "", "0") else None
    except (TypeError, ValueError):
        return None


def parse(text: str) -> list[dict]:
    """The feed's JSON (it contains a few invalid backslash escapes, so those are repaired first) as rows for
    Store.upsert_restrictions. Rows without a usable position, or outside the GTA, are dropped."""
    fixed = re.sub(r'\\(?![\\"/bfnrtu])', r"\\\\", text)
    out = []
    for r in json.loads(fixed, strict=False).get("Closure", []):
        try:
            lat, lon = float(r["latitude"]), float(r["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        a, b, c, d = GTA_BBOX
        if not (a <= lat <= b and c <= lon <= d) or r.get("expired") in (1, "1", True):
            continue
        out.append({"id": str(r["id"]), "road": r.get("road"), "name": r.get("name"), "lat": lat, "lon": lon, "restriction_type": r.get("type"),
                    "sub_type": r.get("subType"), "max_impact": r.get("maxImpact"), "curr_impact": r.get("currImpact"),
                    "planned": r.get("planned") in (1, "1", True), "start_ts": _ms(r.get("startTime")), "end_ts": _ms(r.get("endTime")),
                    "description": (r.get("description") or "")[:600] or None, "last_updated": _ms(r.get("lastUpdated"))})
    return out


async def poll_road_restrictions() -> int:
    """One poll. Returns how many restrictions were stored, 0 when the collector is switched off or the feed is unreachable."""
    if not enabled():
        return 0
    try:
        async with httpx.AsyncClient(timeout=60, headers={"User-Agent": "4ward-hackathon/0.1"}) as cl:
            r = await cl.get(URL, params={"format": "json"})
        r.raise_for_status()
        rows = parse(r.text)
    except Exception:  # noqa: BLE001 - an outage of the city's feed never affects routing
        return 0
    import asyncio

    await asyncio.to_thread(store.upsert_restrictions, rows, time.time())
    return len(rows)
