"""Download the GTA's controlled-access highways from OpenStreetMap (Overpass) into data/highways.json.

  python -m backend.build_highways

Controlled access = highway=motorway/motorway_link (DVP, Gardiner, 400-series, QEW, 407, ...) plus trunk roads that
are explicitly motorroads or closed to foot/bicycle. Also saves every motorway_junction (exit). The routing code
checks walk/bike legs against this file offline, so replay mode and demos never depend on Overpass being up.
"""
import json
import sys
from pathlib import Path

import httpx

from .config import GTA_BBOX

OUT = Path(__file__).resolve().parent.parent / "data" / "highways.json"
MIRRORS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
           "https://overpass.private.coffee/api/interpreter"]


def query() -> str:
    s, n, w, e = GTA_BBOX[0], GTA_BBOX[1], GTA_BBOX[2], GTA_BBOX[3]
    bb = f"{s},{w},{n},{e}"
    return f"""[out:json][timeout:240];
(
  way["highway"~"^(motorway|motorway_link)$"]({bb});
  way["highway"~"^(trunk|trunk_link)$"]["motorroad"="yes"]({bb});
  way["highway"~"^(trunk|trunk_link)$"]["foot"="no"]["bicycle"="no"]({bb});
  node["highway"="motorway_junction"]({bb});
);
out geom tags;"""


def build() -> dict:
    last = ""
    for url in MIRRORS:
        try:
            r = httpx.post(url, data={"data": query()}, timeout=300, headers={"User-Agent": "4ward-hackathon/0.1"})
            r.raise_for_status()
            raw = r.json()
            break
        except Exception as e:  # try the next mirror
            last = f"{url}: {type(e).__name__}: {str(e)[:100]}"
            print("mirror failed:", last, file=sys.stderr)
    else:
        raise SystemExit(f"all Overpass mirrors failed ({last})")
    ways, junctions = [], []
    for el in raw["elements"]:
        if el["type"] == "way" and el.get("geometry"):
            t = el.get("tags", {})
            ways.append({"id": el["id"], "name": t.get("name") or t.get("ref") or "", "ref": t.get("ref", ""), "kind": t.get("highway"),
                         "pts": [[round(p["lat"], 5), round(p["lon"], 5)] for p in el["geometry"]]})
        elif el["type"] == "node":
            t = el.get("tags", {})
            junctions.append({"lat": round(el["lat"], 5), "lon": round(el["lon"], 5), "ref": t.get("ref", ""), "name": t.get("name", "")})
    return {"source": "OpenStreetMap via Overpass (ODbL)", "bbox": list(GTA_BBOX), "ways": ways, "junctions": junctions}


if __name__ == "__main__":
    data = build()
    OUT.write_text(json.dumps(data, separators=(",", ":")))
    print(f"saved {len(data['ways'])} ways and {len(data['junctions'])} junctions ({OUT.stat().st_size / 1e6:.1f} MB)")
