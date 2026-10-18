"""One-off preprocessing: compress TTC + GO Transit GTFS static into one compact rail line graph.

Reads each source's GTFS static files and writes data/transit_network.json:
  lines: [{route_id, name, type, color, stops:[{id,name,lat,lon}], sec_per_stop, headway_min}]
Only the LONGEST trip per (route, direction) is used as the stop sequence, and the median
inter-stop time from that trip gives the in-vehicle speed. TTC ids are kept exactly as TTC's
GTFS-RT feed uses them (reliability.py joins Tiger Data delay history on route_id, so they must
not change); GO ids are prefixed to keep the two sources from ever colliding.
Run: python -m backend.build_transit_network
"""
import csv
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "transit_network.json"

SOURCES = [
    {
        "dir": ROOT / "data" / "gtfs", "prefix": "", "label": "TTC GTFS static (Toronto Open Data)",
        "rail_types": {"0": "lrt_streetcar", "1": "subway"}, "headway_min": {"0": 8, "1": 5},
        "skip_prefix": "3",  # TTC's 3xx route ids are overnight duplicates of the daytime route
    },
    {
        "dir": ROOT / "data" / "gtfs_go", "prefix": "GO_", "label": "GO Transit GTFS static (Metrolinx Open Data)",
        "rail_types": {"2": "go_train"}, "headway_min": {"2": 30},  # GO frequency varies a lot by line/time; flat estimate
        "skip_prefix": None,
    },
    # Local GTA municipal bus agencies, same compact line-graph treatment as TTC/GO: real stop sequences from
    # each agency's own published GTFS static feed, not the generic haversine bus_option() estimate. Headway is
    # a flat typical-service estimate (none of these publish a usable realtime headway feed); fares still use
    # the Presto-style formula in fares.py, labeled as an estimate (see planner._apply_transit_fare).
    {
        "dir": ROOT / "data" / "gtfs_yrt", "prefix": "YRT_", "label": "York Region Transit GTFS static (yrt.ca)",
        "rail_types": {"3": "bus"}, "headway_min": {"3": 20}, "skip_prefix": None,
    },
    {
        "dir": ROOT / "data" / "gtfs_durham", "prefix": "DRT_", "label": "Durham Region Transit GTFS static (Durham Region Open Data)",
        "rail_types": {"3": "bus"}, "headway_min": {"3": 25}, "skip_prefix": None,
    },
    {
        "dir": ROOT / "data" / "gtfs_miway", "prefix": "MIWAY_", "label": "MiWay GTFS static (Mississauga)",
        "rail_types": {"3": "bus"}, "headway_min": {"3": 15}, "skip_prefix": None,
    },
]


def hms(s: str) -> int:
    h, m, sec = s.split(":")
    return int(h) * 3600 + int(m) * 60 + int(sec)


def build_source(src: dict) -> list[dict]:
    d = src["dir"]
    routes = {}
    with open(d / "routes.txt", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["route_type"] in src["rail_types"] and not (src["skip_prefix"] and r["route_id"].startswith(src["skip_prefix"])):
                routes[r["route_id"]] = r

    trip_key = {}
    with open(d / "trips.txt", encoding="utf-8-sig") as f:
        for t in csv.DictReader(f):
            if t["route_id"] in routes:
                trip_key[t["trip_id"]] = (t["route_id"], t["direction_id"])

    # pass 1: count stops per trip, keep the LONGEST trip per (route, direction) (first trips are often short-turns)
    counts: dict = {}
    with open(d / "stop_times.txt", encoding="utf-8-sig") as f:
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
    with open(d / "stop_times.txt", encoding="utf-8-sig") as f:
        for st in csv.DictReader(f):
            if st["trip_id"] in seqs:
                seqs[st["trip_id"]].append((int(st["stop_sequence"]), st["stop_id"], hms(st["arrival_time"])))

    stops = {}
    with open(d / "stops.txt", encoding="utf-8-sig") as f:
        for s in csv.DictReader(f):
            stops[s["stop_id"]] = s

    lines = []
    for tid, (rid, direction) in wanted.items():
        seq = sorted(seqs[tid])
        if len(seq) < 2:
            continue
        gaps = [b[2] - a[2] for a, b in zip(seq, seq[1:]) if b[2] > a[2]]
        r = routes[rid]
        kind = src["rail_types"][r["route_type"]]
        lines.append({
            "route_id": f"{src['prefix']}{rid}",
            "direction": direction,
            "name": f"{r['route_short_name']} {r['route_long_name']}".strip(),
            "type": kind,
            "color": "#" + (r["route_color"] or "888888"),
            "sec_per_stop": statistics.median(gaps) if gaps else 120,
            "headway_min": src["headway_min"][r["route_type"]],
            "stops": [
                {"id": f"{src['prefix']}{sid}", "name": stops[sid]["stop_name"],
                 "lat": float(stops[sid]["stop_lat"]), "lon": float(stops[sid]["stop_lon"])}
                for _, sid, _ in seq if sid in stops
            ],
        })
    return lines


def main() -> None:
    all_lines = []
    labels = []
    old = json.loads(OUT.read_text())["lines"] if OUT.exists() else []
    for src in SOURCES:
        if not (src["dir"] / "routes.txt").exists():
            # raw GTFS not on this machine: keep that agency's lines from the existing network instead of dropping them
            keep = [l for l in old if (l["route_id"].startswith(src["prefix"]) if src["prefix"] else not l["route_id"].startswith(("GO_", "YRT_", "DRT_", "MIWAY_")))]
            all_lines.extend(keep)
            print(f"{src['label']}: no GTFS at {src['dir']}; kept {len(keep)} existing lines")
            continue
        lines = build_source(src)
        all_lines.extend(lines)
        labels.append(src["label"])
        print(f"{src['label']}: {len(lines)} directional lines")
    OUT.write_text(json.dumps({"source": " + ".join(labels), "lines": all_lines}))
    print(f"wrote {OUT} with {len(all_lines)} total directional lines")


if __name__ == "__main__":
    main()
