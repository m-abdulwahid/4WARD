"""Bike Share Toronto GBFS: live station availability with a local snapshot for replay/offline."""
import json
import os
import time
from pathlib import Path

import httpx

from .geo import haversine_km

BASE = "https://tor.publicbikesystem.net/ube/gbfs/v1/en"
SNAP = Path(__file__).resolve().parent.parent / "data" / "gbfs_snapshot.json"
_mem: dict = {"t": 0.0, "stations": [], "provenance": "none"}


async def stations(max_age_s: int = 30) -> dict:
    """Returns {stations:[{id,name,lat,lon,bikes,ebikes,docks}], provenance, fetched_at}."""
    if time.time() - _mem["t"] < max_age_s and _mem["stations"]:
        return _mem
    if os.environ.get("REPLAY") != "1":
        try:
            async with httpx.AsyncClient(timeout=8) as cl:
                info = (await cl.get(f"{BASE}/station_information")).json()["data"]["stations"]
                stat = (await cl.get(f"{BASE}/station_status")).json()["data"]["stations"]
            by = {s["station_id"]: s for s in stat}
            out = []
            for s in info:
                st = by.get(s["station_id"])
                if not st or not st.get("is_renting", 1):
                    continue
                ebikes = (st.get("num_bikes_available_types") or {}).get("ebike", 0)
                out.append({"id": s["station_id"], "name": s["name"], "lat": s["lat"], "lon": s["lon"],
                            "bikes": st.get("num_bikes_available", 0), "ebikes": ebikes,
                            "docks": st.get("num_docks_available", 0)})
            _mem.update(t=time.time(), stations=out, provenance="live", fetched_at=time.time())
            try:
                SNAP.write_text(json.dumps({"fetched_at": time.time(), "stations": out}))
            except Exception:
                pass
            return _mem
        except Exception:
            pass
    try:
        snap = json.loads(SNAP.read_text())
        _mem.update(t=time.time(), stations=snap["stations"], provenance="snapshot", fetched_at=snap["fetched_at"])
    except Exception:
        _mem.update(t=time.time(), stations=[], provenance="unavailable", fetched_at=None)
    return _mem


def nearest(stations_: list[dict], lat: float, lon: float, need: str, max_km: float = 0.9) -> dict | None:
    """need: 'bikes' | 'ebikes' | 'docks'."""
    best, bd = None, 1e9
    for s in stations_:
        if s[need] <= 0:
            continue
        d = haversine_km((lat, lon), (s["lat"], s["lon"]))
        if d < bd and d <= max_km:
            best, bd = s, d
    return best
