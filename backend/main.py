"""4WARD FastAPI backend: health, geocode, assumptions, route planning. Bot orchestration lives in /run (see orchestrator)."""
import os
from pathlib import Path

from dotenv import load_dotenv
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from . import bikeshare, geo, ingest, planner, runs  # noqa: E402
from .db import store  # noqa: E402
from .config import ASSUMPTIONS, EMISSIONS_G_PER_KM, SPEEDS_KMH  # noqa: E402

@asynccontextmanager
async def lifespan(_: FastAPI):
    """Optionally poll the live feeds in the background so the Tiger Data charts accrue real history."""
    task = None
    secs = int(os.environ.get("INGEST_EVERY_S", "60"))
    if secs > 0 and os.environ.get("REPLAY") != "1":
        task = asyncio.create_task(_poll_feeds(secs))
    yield
    if task:
        task.cancel()


async def _poll_feeds(secs: int) -> None:
    while True:
        try:
            raw = await ingest.fetch_raw()
            await asyncio.to_thread(ingest.store_raw, raw, "live")
        except Exception:
            pass  # offline or feed hiccup: keep going, charts fall back to stored/simulated history
        await asyncio.sleep(secs)


app = FastAPI(title="4WARD API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class Place(BaseModel):
    text: str | None = None
    lat: float | None = None
    lon: float | None = None


class RouteRequest(BaseModel):
    origin: Place
    destination: Place
    constraints: planner.Constraints = planner.Constraints()


async def resolve(p: Place) -> dict:
    if p.lat is not None and p.lon is not None:
        return {"name": p.text or f"{p.lat:.4f},{p.lon:.4f}", "lat": p.lat, "lon": p.lon}
    if p.text:
        g = await geo.geocode(p.text)
        if g:
            return g
    raise HTTPException(422, f"Could not locate place: {p.text!r}")


@app.get("/health")
async def health():
    return {"status": "ok", "replay": os.environ.get("REPLAY") == "1",
            "gemini_configured": bool(os.environ.get("GEMINI_API_KEY"))}


@app.get("/assumptions")
async def assumptions():
    return {"assumptions": ASSUMPTIONS, "emissions_g_per_km": EMISSIONS_G_PER_KM, "speeds_kmh": SPEEDS_KMH}


@app.get("/suggest")
async def suggest(q: str):
    return {"results": geo.suggest(q)}


@app.get("/geocode")
async def geocode(q: str):
    g = await geo.geocode(q)
    if not g:
        raise HTTPException(404, "not found")
    return g


@app.get("/bikeshare")
async def bikeshare_stations():
    s = await bikeshare.stations()
    return {"provenance": s["provenance"], "fetched_at": s.get("fetched_at"), "stations": s["stations"]}


@app.post("/route")
async def route(req: RouteRequest):
    o, d = await resolve(req.origin), await resolve(req.destination)
    for n, p in (("origin", o), ("destination", d)):
        if not geo.in_toronto(p["lat"], p["lon"]):
            raise HTTPException(422, f"{n} is outside the Toronto area")
    result = await planner.plan((o["lat"], o["lon"]), (d["lat"], d["lon"]), req.constraints)
    result["origin_place"], result["destination_place"] = o, d
    return result


class RunRequest(BaseModel):
    prompt: str


@app.post("/run")
async def start_run(req: RunRequest):
    if not req.prompt.strip():
        raise HTTPException(422, "prompt is empty")
    run = runs.start(req.prompt.strip())
    return {"id": run["id"]}


@app.get("/run/{rid}")
async def get_run(rid: str):
    run = runs.RUNS.get(rid)
    if not run:
        raise HTTPException(404, "unknown run")
    return runs.snapshot(run)


@app.websocket("/ws/run/{rid}")
async def ws_run(ws: WebSocket, rid: str):
    run = runs.RUNS.get(rid)
    await ws.accept()
    if not run:
        await ws.close(code=4404)
        return
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    run["subs"].add(q)
    try:
        for ev in run["events"] + list(run["frames"].values()):  # replay what the client missed
            await ws.send_json(ev)
        if run["status"] != "running":
            await ws.send_json({"type": "run_done", "status": run["status"], "result": run["result"]})
        while True:
            await ws.send_json(await q.get())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        run["subs"].discard(q)


@app.get("/insights/ttc")
async def insights_ttc(hours: int = 24, top: int = 6):
    rows = await asyncio.to_thread(store.ttc_hourly, hours, top)
    return {"backend": store.backend, "sources": (await asyncio.to_thread(store.sources, hours)).get("ttc_route_snapshot", {}), "rows": rows}


@app.get("/insights/bikes")
async def insights_bikes(hours: int = 24):
    rows = await asyncio.to_thread(store.bikes_hourly, hours)
    return {"backend": store.backend, "sources": (await asyncio.to_thread(store.sources, hours)).get("bike_system_snapshot", {}), "rows": rows}
