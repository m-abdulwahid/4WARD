"""Lightweight TTC rail/streetcar router built from the compact GTFS-derived line graph.

Not a full multimodal planner: subway/LRT/streetcar stop sequences + median inter-stop times
are real GTFS-derived; headways and the generic bus fallback are ESTIMATES (labelled in output).
"""
import heapq
import json
import math
from functools import lru_cache
from pathlib import Path

from .config import SPEEDS_KMH, traffic_multiplier
from .geo import haversine_km
from . import osm

NET_FILE = Path(__file__).resolve().parent.parent / "data" / "transit_network.json"
WALK_KMH = SPEEDS_KMH["walk"]
MAX_ACCESS_KM = 1.6
TRANSFER_M = 220
DWELL_S = 25


@lru_cache(maxsize=1)
def _net():
    lines = json.loads(NET_FILE.read_text())["lines"]
    nodes = []  # (line_idx, stop_idx)
    for li, ln in enumerate(lines):
        for si in range(len(ln["stops"])):
            nodes.append((li, si))
    index = {n: i for i, n in enumerate(nodes)}
    # transfer neighbours: grid bucket by ~0.003 deg
    grid: dict = {}
    for i, (li, si) in enumerate(nodes):
        s = lines[li]["stops"][si]
        grid.setdefault((round(s["lat"] / 0.003), round(s["lon"] / 0.003)), []).append(i)
    transfers: dict[int, list] = {i: [] for i in range(len(nodes))}
    for i, (li, si) in enumerate(nodes):
        s = lines[li]["stops"][si]
        gx, gy = round(s["lat"] / 0.003), round(s["lon"] / 0.003)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((gx + dx, gy + dy), []):
                    lj, sj = nodes[j]
                    if lines[lj]["route_id"] == lines[li]["route_id"]:
                        continue
                    t = lines[lj]["stops"][sj]
                    d = haversine_km((s["lat"], s["lon"]), (t["lat"], t["lon"])) * 1000
                    if d <= TRANSFER_M:
                        walk_s = d * 1.3 / (WALK_KMH / 3.6)
                        transfers[i].append((j, walk_s + lines[lj]["headway_min"] * 30))
    return lines, nodes, index, transfers


def _stop_ll(lines, node):
    s = lines[node[0]]["stops"][node[1]]
    return (s["lat"], s["lon"])


def _rail_route(a, b, walk_weight: float = 1.0):
    lines, nodes, index, transfers = _net()
    # access candidates
    acc = []
    for i, n in enumerate(nodes):
        d = haversine_km(a, _stop_ll(lines, n))
        if d <= MAX_ACCESS_KM:
            acc.append((i, d))
    if not acc:
        return None
    dist = {}
    prev: dict = {}
    pq = []
    for i, d in acc:
        t = d * 1.3 / (WALK_KMH / 3600) * walk_weight + lines[nodes[i][0]]["headway_min"] * 30
        if t < dist.get(i, 1e18):
            dist[i] = t
            prev[i] = ("access", None)
            heapq.heappush(pq, (t, i))
    best = (1e18, None)
    while pq:
        t, i = heapq.heappop(pq)
        if t > dist.get(i, 1e18):
            continue
        li, si = nodes[i]
        ln = lines[li]
        egress_d = haversine_km(_stop_ll(lines, nodes[i]), b)
        if egress_d <= MAX_ACCESS_KM:
            total = t + egress_d * 1.3 / (WALK_KMH / 3600) * walk_weight
            if total < best[0]:
                best = (total, i)
        if si + 1 < len(ln["stops"]):
            j = index[(li, si + 1)]
            nt = t + ln["sec_per_stop"] + DWELL_S
            if nt < dist.get(j, 1e18):
                dist[j] = nt
                prev[j] = ("ride", i)
                heapq.heappush(pq, (nt, j))
        for j, cost in transfers[i]:
            nt = t + cost
            if nt < dist.get(j, 1e18):
                dist[j] = nt
                prev[j] = ("transfer", i)
                heapq.heappush(pq, (nt, j))
    if best[1] is None:
        return None
    # reconstruct
    path = []
    cur = best[1]
    while cur is not None:
        kind, p = prev[cur]
        path.append((cur, kind))
        cur = p
    path.reverse()
    segments = []  # new segment at access/transfer; a ride extends the current one
    for node, kind in path:
        if kind == "ride" and segments:
            segments[-1]["nodes"].append(node)
        else:
            segments.append({"line": nodes[node][0], "nodes": [node]})
    return {"path": path, "segments": [s for s in segments if len(s["nodes"]) >= 2], "total_s": best[0]}


async def rail_option(a, b, hour: int, walk_weight: float = 1.0) -> dict | None:
    r = _rail_route(a, b, walk_weight)
    if not r or not r["segments"]:
        return None
    lines, nodes, _, _ = _net()
    legs = []
    first = _stop_ll(lines, nodes[r["segments"][0]["nodes"][0]])
    last = _stop_ll(lines, nodes[r["segments"][-1]["nodes"][-1]])
    w1 = await osm.leg("walk", a, first)
    legs.append({"mode": "walk", "label": "Walk to station", "from": list(a), "to": list(first),
                 "distance_km": w1["distance_km"], "duration_s": w1["duration_s"], "geometry": w1["geometry"],
                 "provenance": w1["provenance"]})
    prev_end = None
    for seg in r["segments"]:
        ln = lines[seg["line"]]
        pts = [list(_stop_ll(lines, nodes[n])) for n in seg["nodes"]]
        if prev_end is not None and haversine_km(tuple(prev_end), tuple(pts[0])) > 0.02:
            legs.append({"mode": "walk", "label": "Transfer", "from": prev_end, "to": pts[0],
                         "distance_km": round(haversine_km(tuple(prev_end), tuple(pts[0])) * 1.3, 3),
                         "duration_s": haversine_km(tuple(prev_end), tuple(pts[0])) * 1.3 / (WALK_KMH / 3600),
                         "geometry": [prev_end, pts[0]], "provenance": "estimated"})
        dist_km = sum(haversine_km(tuple(p), tuple(q)) for p, q in zip(pts, pts[1:])) * 1.1
        legs.append({
            "mode": ln["type"], "label": f"{ln['name']} ({ln['stops'][nodes[seg['nodes'][0]][1]]['name']} → "
                     f"{ln['stops'][nodes[seg['nodes'][-1]][1]]['name']})",
            "from": pts[0], "to": pts[-1], "distance_km": round(dist_km, 3),
            "duration_s": (len(seg["nodes"]) - 1) * (ln["sec_per_stop"] + DWELL_S) + ln["headway_min"] * 30,
            "geometry": pts, "provenance": "gtfs-static (headway estimated)", "color": ln["color"],
            "route_id": ln["route_id"]})
        prev_end = pts[-1]
    w2 = await osm.leg("walk", tuple(prev_end), b)
    legs.append({"mode": "walk", "label": "Walk to destination", "from": prev_end, "to": list(b),
                 "distance_km": w2["distance_km"], "duration_s": w2["duration_s"], "geometry": w2["geometry"],
                 "provenance": w2["provenance"]})
    return {"kind": "transit_rail", "legs": legs, "boardings": len(r["segments"]),
            "signature": (r["segments"][0]["nodes"][0], r["segments"][-1]["nodes"][-1])}


async def bus_option(a, b, hour: int) -> dict:
    """Generic bus ride between two points: ESTIMATED (no stop-level routing)."""
    d = haversine_km(a, b) * 1.35
    ride_s = d / SPEEDS_KMH["bus"] * 3600 * (traffic_multiplier(hour) / 1.2)
    walk_km = 0.35
    legs = [
        {"mode": "walk", "label": "Walk to bus stop", "from": list(a), "to": list(a), "distance_km": walk_km,
         "duration_s": walk_km / WALK_KMH * 3600, "geometry": [list(a)], "provenance": "estimated"},
        {"mode": "bus", "label": "TTC bus (estimated, includes ~7 min wait)", "from": list(a), "to": list(b),
         "distance_km": round(d, 3), "duration_s": ride_s + 7 * 60, "geometry": [list(a), list(b)],
         "provenance": "estimated"},
        {"mode": "walk", "label": "Walk to destination", "from": list(b), "to": list(b), "distance_km": walk_km,
         "duration_s": walk_km / WALK_KMH * 3600, "geometry": [list(b)], "provenance": "estimated"},
    ]
    return {"kind": "transit_bus", "legs": legs, "boardings": 1}
