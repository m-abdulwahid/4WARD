"""Build data/timetable.json: real SCHEDULED departures for the lines in data/transit_network.json.

  python -m backend.build_timetable

Needs each agency's GTFS static folder (data/gtfs for TTC, data/gtfs_go for GO). Lines from agencies whose GTFS is not
present are simply left out; the router then falls back to an ESTIMATED headway for them and says so.

How it stays small: a line keeps ONE representative stop sequence (the one in transit_network.json) and the
representative trip's run times (offsets). Every trip of that route and direction is reduced to its "virtual start": the time
it would leave the first stop of the representative sequence, so departure at stop k = virtual_start + offset[k]. A trip that
serves only part of the line (short turn) keeps the first and last stop index it serves. Trips are grouped by service_id,
and the calendar (weekday flags plus added/removed dates) says which services run on which day.
"""
import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from .build_transit_network import SOURCES, hms

ROOT = Path(__file__).resolve().parent.parent
NET = ROOT / "data" / "transit_network.json"
OUT = ROOT / "data" / "timetable.json"


def _read(path: Path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f)


def build_source(src: dict, net_lines: list[dict]) -> tuple[dict, dict] | None:
    d = src["dir"]
    if not (d / "routes.txt").exists():
        return None
    prefix = src["prefix"]
    mine = [l for l in net_lines if l["route_id"].startswith(prefix) and (prefix or not l["route_id"].startswith(("GO_", "YRT_", "DRT_", "MIWAY_")))]
    wanted = {(l["route_id"][len(prefix):], l["direction"]): l for l in mine}
    trips: dict[str, tuple] = {}
    for t in _read(d / "trips.txt"):
        k = (t["route_id"], t["direction_id"])
        if k in wanted:
            trips[t["trip_id"]] = (k, t["service_id"])
    seqs: dict[str, list] = defaultdict(list)
    for st in _read(d / "stop_times.txt"):
        tid = st["trip_id"]
        if tid in trips:
            arr = hms(st["arrival_time"]) if st["arrival_time"] else hms(st["departure_time"])
            dep = hms(st["departure_time"]) if st["departure_time"] else arr
            seqs[tid].append((int(st["stop_sequence"]), st["stop_id"], arr, dep))
    by_line: dict[tuple, list[str]] = defaultdict(list)
    for tid, (k, _) in trips.items():
        by_line[k].append(tid)

    lines_out: dict[str, dict] = {}
    for k, line in wanted.items():
        rep_ids = [s["id"][len(prefix):] for s in line["stops"]]
        known = set(rep_ids)
        # the network drops stops that are missing from stops.txt, so compare each trip's stops restricted to the known ones
        rep = next((tid for tid in by_line[k] if [s[1] for s in sorted(seqs[tid]) if s[1] in known] == rep_ids), None)
        if rep is None:
            continue
        rs = [s for s in sorted(seqs[rep]) if s[1] in known]
        t0 = rs[0][3]
        dep_off = [s[3] - t0 for s in rs]
        arr_off = [s[2] - t0 for s in rs]
        idx = {}
        for i, sid in enumerate(rep_ids):
            idx.setdefault(sid, i)
        last = len(rep_ids) - 1
        svc: dict[str, dict] = {}
        for tid in by_line[k]:
            s = sorted(seqs[tid])
            hits = [(idx[x[1]], x) for x in s if x[1] in idx]
            if len(hits) < 2:
                continue
            f_idx, f = hits[0]
            l_idx = hits[-1][0]
            if l_idx <= f_idx:
                continue
            start = f[3] - dep_off[f_idx]
            e = svc.setdefault(trips[tid][1], {"s": [], "p": []})
            if f_idx == 0 and l_idx == last:
                e["s"].append(start)
            else:
                e["p"].append([start, f_idx, l_idx])
        for e in svc.values():
            e["s"].sort()
            e["p"].sort()
        lines_out[f"{line['route_id']}|{line['direction']}"] = {"dep": dep_off, "arr": arr_off, "trips": svc}

    cal = {"service": {}, "added": defaultdict(list), "removed": defaultdict(list)}
    if (d / "calendar.txt").exists():
        for r in _read(d / "calendar.txt"):
            cal["service"][r["service_id"]] = {"days": "".join(r[x] for x in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")),
                                               "start": r["start_date"], "end": r["end_date"]}
    if (d / "calendar_dates.txt").exists():
        for r in _read(d / "calendar_dates.txt"):
            cal["added" if r["exception_type"] == "1" else "removed"][r["date"]].append(r["service_id"])
    cal["added"], cal["removed"] = dict(cal["added"]), dict(cal["removed"])
    valid = ["", ""]
    if (d / "feed_info.txt").exists():
        for r in _read(d / "feed_info.txt"):
            valid = [r.get("feed_start_date", ""), r.get("feed_end_date", "")]
    return {"label": src["label"], "prefix": prefix, "valid": valid, "calendar": cal}, lines_out


def main() -> None:
    net = json.loads(NET.read_text())["lines"]
    sources, lines = [], {}
    for src in SOURCES:
        built = build_source(src, net)
        if not built:
            print(f"skip {src['label']}: no GTFS at {src['dir']}")
            continue
        meta, ls = built
        sources.append(meta)
        lines.update(ls)
        n_trips = sum(len(e["s"]) + len(e["p"]) for v in ls.values() for e in v["trips"].values())
        print(f"{src['label']}: {len(ls)} lines, {n_trips} trips, valid {meta['valid']}")
    OUT.write_text(json.dumps({"built": date.today().isoformat(), "sources": sources, "lines": lines}, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
