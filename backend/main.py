"""4WARD FastAPI backend: health, geocode, assumptions, route planning. Bot orchestration lives in /run (see orchestrator)."""
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from . import bikeshare, geo, planner  # noqa: E402
from .config import ASSUMPTIONS, EMISSIONS_G_PER_KM, SPEEDS_KMH  # noqa: E402

app = FastAPI(title="4WARD API")
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
