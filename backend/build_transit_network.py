"""One-off preprocessing: compress TTC GTFS static into a small rail/streetcar line graph.

Reads data/gtfs/{routes,trips,stop_times,stops}.txt and writes data/transit_network.json:
  lines: [{route_id, name, type, color, stops:[{id,name,lat,lon}], mins_per_stop, headway_min}]
Only the first trip per (route, direction) is used as the stop sequence, and the median
inter-stop time from that trip gives the in-vehicle speed. Run: python -m backend.build_transit_network
"""
import csv
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GTFS = ROOT / "data" / "gtfs"
OUT = ROOT / "data" / "transit_network.json"
RAIL_TYPES = {"0", "1"}  # tram/streetcar/LRT and subway


def hms(s: str) -> int:
    h, m, sec = s.split(":")
    return int(h) * 3600 + int(m) * 60 + int(sec)


def main() -> None:
    routes = {}
    with open(GTFS / "routes.txt", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["route_type"] in RAIL_TYPES and not r["route_id"].startswith("3"):  # skip overnight 3xx duplicates
                routes[r["route_id"]] = r

    trip_key = {}  # trip_id -> (route, dir)
    with open(GTFS / "trips.txt", encoding="utf-8-sig") as f:
        for t in csv.DictReader(f):
            if t["route_id"] in routes:
                trip_key[t["trip_id"]] = (t["route_id"], t["direction_id"])

    # pass 1: count stops per trip, keep the LONGEST trip per (route, direction) (first trips are often short-turns)
    counts: dict = {}
    with open(GTFS / "stop_times.txt", encoding="utf-8-sig") as f:
        for st in csv.DictReader(f):
            if st["trip_id"] in trip_key:
                counts[st["trip_id"]] = counts.get(st["trip_id"], 0) + 1
    longest: dict = {}
    for tid, n in counts.items():
        k = trip_key[tid]
        if k not in longest or n > counts[longest[k]]:
            longest[k] = tid
    wanted = {v: k for k, v in longest.items()}

    seqs = {tid: [] for tid in wanted}
    with open(GTFS / "stop_times.txt", encoding="utf-8-sig") as f:
        for st in csv.DictReader(f):
            if st["trip_id"] in seqs:
                seqs[st["trip_id"]].append((int(st["stop_sequence"]), st["stop_id"], hms(st["arrival_time"])))

    stops = {}
    with open(GTFS / "stops.txt", encoding="utf-8-sig") as f:
        for s in csv.DictReader(f):
            stops[s["stop_id"]] = s

    lines = []
    for tid, (rid, direction) in wanted.items():
        seq = sorted(seqs[tid])
        if len(seq) < 2:
            continue
        gaps = [b[2] - a[2] for a, b in zip(seq, seq[1:]) if b[2] > a[2]]
        r = routes[rid]
        lines.append({
            "route_id": rid,
            "direction": direction,
            "name": f"{r['route_short_name']} {r['route_long_name']}",
            "type": "subway" if r["route_type"] == "1" else "lrt_streetcar",
            "color": "#" + (r["route_color"] or "888888"),
            "sec_per_stop": statistics.median(gaps) if gaps else 120,
            "headway_min": 5 if r["route_type"] == "1" else 8,
            "stops": [
                {"id": sid, "name": stops[sid]["stop_name"],
                 "lat": float(stops[sid]["stop_lat"]), "lon": float(stops[sid]["stop_lon"])}
                for _, sid, _ in seq if sid in stops
            ],
        })
    OUT.write_text(json.dumps({"source": "TTC GTFS static (Toronto Open Data)", "lines": lines}))
    print(f"wrote {OUT} with {len(lines)} directional lines")


if __name__ == "__main__":
    main()
