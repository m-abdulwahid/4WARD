"""Feed ingestion into Tiger Data / SQLite.

  python -m backend.ingest --once            poll GBFS + TTC GTFS-RT one time and store it
  python -m backend.ingest --loop 30         poll every 30 s until stopped
  python -m backend.ingest --record          save one raw snapshot to data/replay/ (for offline replay mode)
  python -m backend.ingest --replay          store the recorded snapshot (source='replay') instead of hitting the network
  python -m backend.ingest --seed-sim 48     backfill 48 h of SIMULATED history (source='simulated') so charts are
                                             populated at demo time; the UI labels it as simulated
"""
import argparse
import asyncio
import json
import math
import random
import time
from collections import defaultdict
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
from .db import store  # noqa: E402

REPLAY_DIR = ROOT / "data" / "replay"
GBFS = "https://tor.publicbikesystem.net/ube/gbfs/v1/en/station_status"
TTC_TRIPS = "https://gtfsrt.ttc.ca/trips/update?format=binary"
TTC_ALERTS = "https://gtfsrt.ttc.ca/alerts/all?format=binary"


def summarize_bikes(status: dict) -> dict:
    st = status["data"]["stations"]
    ebikes = sum((s.get("num_bikes_available_types") or {}).get("ebike", 0) for s in st)
    return {"stations": len(st), "bikes": sum(s.get("num_bikes_available", 0) for s in st), "ebikes": ebikes,
            "docks": sum(s.get("num_docks_available", 0) for s in st)}


def summarize_ttc(trips_bin: bytes, alerts_bin: bytes) -> list[dict]:
    from google.transit import gtfs_realtime_pb2 as pb

    f = pb.FeedMessage()
    f.ParseFromString(trips_bin)
    per: dict = defaultdict(lambda: {"active": 0, "cancelled": 0, "delays": []})
    for e in f.entity:
        if not e.HasField("trip_update"):
            continue
        tu = e.trip_update
        r = tu.trip.route_id or "unknown"
        if tu.trip.schedule_relationship in (pb.TripDescriptor.CANCELED, pb.TripDescriptor.DELETED):
            per[r]["cancelled"] += 1
            continue
        per[r]["active"] += 1
        for stu in tu.stop_time_update[:1]:
            if stu.HasField("arrival") and stu.arrival.HasField("delay"):
                per[r]["delays"].append(stu.arrival.delay)
    al = pb.FeedMessage()
    al.ParseFromString(alerts_bin)
    alerts: dict = defaultdict(int)
    for e in al.entity:
        for ie in {ie.route_id for ie in e.alert.informed_entity if ie.route_id}:
            alerts[ie] += 1
    routes = set(per) | set(alerts)
    return [{"route_id": r, "active_trips": per[r]["active"], "cancelled_trips": per[r]["cancelled"],
             "avg_delay_s": (sum(per[r]["delays"]) / len(per[r]["delays"])) if per[r]["delays"] else None,
             "alerts": alerts.get(r, 0)} for r in sorted(routes)]


def parse_alerts(alerts_bin: bytes) -> list[dict]:
    """One row per (route, alert): cause/effect/header/description, for backend/reliability.py's
    'what could delay you' list. summarize_ttc() above only counts alerts per route; this keeps the text."""
    from google.transit import gtfs_realtime_pb2 as pb

    f = pb.FeedMessage()
    f.ParseFromString(alerts_bin)
    out = []
    for e in f.entity:
        if not e.HasField("alert"):
            continue
        a = e.alert
        cause = pb.Alert.Cause.Name(a.cause) if a.cause else None
        effect = pb.Alert.Effect.Name(a.effect) if a.effect else None
        header = a.header_text.translation[0].text if a.header_text.translation else None
        desc = a.description_text.translation[0].text if a.description_text.translation else None
        for route_id in sorted({ie.route_id for ie in a.informed_entity if ie.route_id}):
            out.append({"route_id": route_id, "cause": cause, "effect": effect, "header_text": header, "description_text": desc})
    return out


async def fetch_raw() -> dict:
    async with httpx.AsyncClient(timeout=20) as cl:
        g, t, a = await asyncio.gather(cl.get(GBFS), cl.get(TTC_TRIPS), cl.get(TTC_ALERTS))
    return {"ts": time.time(), "gbfs": g.json(), "trips_b64": __import__("base64").b64encode(t.content).decode(),
            "alerts_b64": __import__("base64").b64encode(a.content).decode()}


def store_raw(raw: dict, source: str) -> tuple[int, int]:
    import base64

    alerts_bin = base64.b64decode(raw["alerts_b64"])
    ttc = summarize_ttc(base64.b64decode(raw["trips_b64"]), alerts_bin)
    b = summarize_bikes(raw["gbfs"])
    ts = time.time() if source == "live" else raw["ts"] + (time.time() - raw["ts"])  # replay stamps "now"
    store.insert_ttc(ts, ttc, source)
    store.insert_bike(ts, b["stations"], b["bikes"], b["ebikes"], b["docks"], source)
    store.insert_ttc_alerts(ts, parse_alerts(alerts_bin))
    return len(ttc), b["bikes"]


def seed_simulated(hours: int) -> None:
    """Backfill plausible history: bikes dip in the morning/evening rush, TTC delays peak at rush hour."""
    rnd = random.Random(42)
    store.delete_simulated()
    now = int(time.time())
    routes = [("1", 38), ("2", 34), ("501", 40), ("504", 36), ("505", 22), ("510", 20), ("29", 28), ("36", 30)]
    n = 0
    for step in range(hours * 6, 0, -1):  # every 10 minutes
        ts = now - step * 600
        hour = time.localtime(ts).tm_hour
        rush = math.exp(-((hour - 8.5) ** 2) / 3) + math.exp(-((hour - 17.5) ** 2) / 3)
        rows = [{"route_id": r, "active_trips": max(2, int(base * (0.45 + 0.4 * rush) + rnd.randint(-2, 2))),
                 "cancelled_trips": int(rnd.random() < 0.08 * (0.5 + rush)), "avg_delay_s": max(0, 60 + 140 * rush + rnd.gauss(0, 25)),
                 "alerts": 1 if rnd.random() < 0.15 else 0} for r, base in routes]
        store.insert_ttc(ts, rows, "simulated")
        bikes = int(8300 - 2800 * rush + rnd.gauss(0, 90))
        store.insert_bike(ts, 1072, bikes, int(bikes * 0.12), 11750 + int(8138 - bikes), "simulated")
        n += 1
    store.refresh_aggregates()
    print(f"seeded {n} simulated samples over {hours} h ({store.backend})")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", type=int, metavar="SECONDS")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--seed-sim", type=int, metavar="HOURS")
    a = ap.parse_args()
    if a.seed_sim:
        seed_simulated(a.seed_sim)
    if a.record:
        REPLAY_DIR.mkdir(parents=True, exist_ok=True)
        raw = await fetch_raw()
        (REPLAY_DIR / "snapshot.json").write_text(json.dumps(raw))
        print("recorded", REPLAY_DIR / "snapshot.json")
    if a.replay:
        raw = json.loads((REPLAY_DIR / "snapshot.json").read_text())
        print("replayed:", store_raw(raw, "replay"))
    if a.once or a.loop:
        while True:
            try:
                print(time.strftime("%H:%M:%S"), "stored (routes, bikes):", store_raw(await fetch_raw(), "live"), f"[{store.backend}]")
            except Exception as e:
                print("poll failed:", type(e).__name__, str(e)[:120])
            if not a.loop:
                break
            await asyncio.sleep(a.loop)


if __name__ == "__main__":
    asyncio.run(main())
