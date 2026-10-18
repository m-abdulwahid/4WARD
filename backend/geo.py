"""Geometry helpers and geocoding (built-in GTA gazetteer first, Nominatim second, cached)."""
import json
import math
from pathlib import Path

import httpx

from .config import GTA_BBOX

CACHE = Path(__file__).resolve().parent.parent / "data" / "geocode_cache.json"

# Offline-safe gazetteer of demo-friendly places: name -> (lat, lon)
GAZETTEER = {
    "union station": (43.6453, -79.3806),
    "cn tower": (43.6426, -79.3871),
    "university of toronto": (43.6629, -79.3957),
    "uoft": (43.6629, -79.3957),
    "york university": (43.7735, -79.5019),
    "toronto metropolitan university": (43.6577, -79.3788),
    "pearson airport": (43.6777, -79.6248),
    "billy bishop airport": (43.6275, -79.3962),
    "scarborough town centre": (43.7764, -79.2573),
    "eaton centre": (43.6544, -79.3807),
    "high park": (43.6465, -79.4637),
    "the beaches": (43.6710, -79.2970),
    "kensington market": (43.6547, -79.4005),
    "yorkdale mall": (43.7254, -79.4522),
    "ontario science centre": (43.7164, -79.3390),
    "toronto zoo": (43.8177, -79.1859),
    "distillery district": (43.6503, -79.3596),
    "sheppard-yonge station": (43.7615, -79.4111),
    "finch station": (43.7807, -79.4148),
    "kennedy station": (43.7323, -79.2637),
    "kipling station": (43.6372, -79.5361),
    "humber college": (43.7291, -79.6058),
    "centennial college": (43.7855, -79.2285),
    "ontario place": (43.6289, -79.4139),
    "harbourfront": (43.6389, -79.3812),
    "casa loma": (43.6780, -79.4094),
    "mount sinai hospital": (43.6573, -79.3902),
    "scotiabank arena": (43.6435, -79.3791),
    # GO Transit stations across the wider GTA (coordinates from Metrolinx's GTFS static feed)
    "oshawa": (43.870909, -78.88585),
    "oshawa go": (43.870909, -78.88585),
    "whitby go": (43.86484, -78.93818),
    "ajax go": (43.847766, -79.041372),
    "pickering go": (43.831576, -79.084594),
    "union station go": (43.645195, -79.3806),
    "milton go": (43.52364, -79.867172),
    "oakville go": (43.455594, -79.682242),
    "burlington go": (43.341265, -79.809141),
    "bramalea go": (43.7022591, -79.6887512),
    "brampton": (43.7022591, -79.6887512),
    "cooksville go": (43.582973, -79.623695),
    "mississauga": (43.582973, -79.623695),
    "markham go": (43.882691, -79.262469),
    "unionville go": (43.851689, -79.314332),
    "markham": (43.882691, -79.262469),
    "richmond hill go": (43.874307, -79.426167),
    "newmarket go": (44.060426, -79.459289),
    "aurora go": (44.000758, -79.459725),
    "rogers centre": (43.6414, -79.3894),
}


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def _load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text())
    except Exception:
        return {}


def _save_cache(c: dict) -> None:
    try:
        CACHE.write_text(json.dumps(c, indent=1))
    except Exception:
        pass


def in_gta(lat: float, lon: float) -> bool:
    a, b, c, d = GTA_BBOX
    return a <= lat <= b and c <= lon <= d


def suggest(query: str, limit: int = 6) -> list[dict]:
    """Gazetteer-only autocomplete (instant, offline)."""
    q = query.strip().lower()
    out = [{"name": k.title(), "lat": v[0], "lon": v[1], "source": "gazetteer"}
           for k, v in GAZETTEER.items() if q and q in k]
    return out[:limit]


async def geocode(query: str) -> dict | None:
    q = query.strip().lower()
    if not q:
        return None
    if q in GAZETTEER:
        lat, lon = GAZETTEER[q]
        return {"name": query.title(), "lat": lat, "lon": lon, "source": "gazetteer"}
    for k, (lat, lon) in GAZETTEER.items():
        if q in k or k in q:
            return {"name": k.title(), "lat": lat, "lon": lon, "source": "gazetteer"}
    cache = _load_cache()
    if q in cache:
        return {**cache[q], "source": "cache"}
    a, b, c, d = GTA_BBOX
    try:
        async with httpx.AsyncClient(timeout=8, headers={"User-Agent": "4ward-hackathon/0.1"}) as cl:
            r = await cl.get("https://nominatim.openstreetmap.org/search", params={
                "q": query, "format": "json", "limit": 1, "viewbox": f"{c},{b},{d},{a}", "bounded": 1})
            data = r.json()
        if data:
            res = {"name": data[0]["display_name"].split(",")[0], "lat": float(data[0]["lat"]), "lon": float(data[0]["lon"])}
            cache[q] = res
            _save_cache(cache)
            return {**res, "source": "nominatim"}
    except Exception:
        pass
    return None
