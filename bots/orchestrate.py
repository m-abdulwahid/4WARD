"""Parse a plain-language prompt with Gemini (or rules), compute ONE shared candidate set for the trip, then run the
four bots in parallel. Each bot operates the dashboard, and its route is its own objective's optimum over the shared set."""
import asyncio
import os
import time
from datetime import datetime
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

import httpx
from playwright.async_api import async_playwright

from backend import geo, planner, trip

from . import critic, optimize
from .nlp import parse_prompt
from .profiles import ALL_MODES, PROFILES
from .runner import run_bot
from .steel_client import steel_session

Emit = Callable[[dict], Awaitable[None] | None]
TORONTO = ZoneInfo("America/Toronto")
# The only constraint fields the planner page can express; the shared plan uses exactly these so the engine's
# candidate set and the page every bot operates are the same computation.
UI_CONSTRAINTS = ("bags", "no_presto", "has_car", "ev", "has_bike", "has_ebike", "avoid_stairs", "depart_at", "arrive_by")


async def _emit(cb: Emit | None, ev: dict) -> None:
    if cb:
        r = cb(ev)
        if asyncio.iscoroutine(r):
            await r


async def _warm_up(base_url: str) -> None:
    """Hit /planner once so a cold Next dev server compiles it before four browsers pile on."""
    try:
        async with httpx.AsyncClient(timeout=60) as cl:
            await cl.get(f"{base_url}/planner")
    except Exception:
        pass  # the bots will report a clear error if the dashboard is really down


async def _run_local(keys, user, base_url, emit, max_steps, timeout_s, stream_frames, shared) -> list[dict]:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            return list(await asyncio.gather(*[
                run_bot(await browser.new_context(viewport={"width": 1000, "height": 640}), k, user, base_url,
                        emit, max_steps=max_steps, timeout_s=timeout_s, stream_frames=stream_frames, shared=shared)
                for k in keys]))
        finally:
            await browser.close()


async def _run_one_steel(key, user, base_url, emit, max_steps, timeout_s, stream_frames, shared) -> dict:
    # Steel's own session cap must outlast the bot's timeout_s, or Steel kills the session mid-run.
    async with steel_session(api_timeout_ms=int((timeout_s + 60) * 1000)) as bot:
        await _emit(emit, {"type": "steel_session", "bot": key, "session_id": bot.id, "live_view_url": bot.live_view_url})
        return await run_bot(bot.context, key, user, base_url, emit, max_steps=max_steps, timeout_s=timeout_s,
                             stream_frames=stream_frames, shared=shared)


async def _run_steel(keys, user, base_url, emit, max_steps, timeout_s, stream_frames, shared) -> list[dict]:
    return list(await asyncio.gather(*[
        _run_one_steel(k, user, base_url, emit, max_steps, timeout_s, stream_frames, shared) for k in keys]))


async def _locate(user: dict) -> tuple[dict | None, str | None]:
    """Geocode both ends and apply the same GTA-only check the engine applies to every /route call, up front, so
    an unresolvable trip never spins up four browsers just to fail."""
    places: dict = {"stops": []}
    wanted = [("origin", user["origin"]), ("destination", user["destination"])] + \
             [(f"stop {i + 1}", s["name"]) for i, s in enumerate(user.get("stops") or [])]
    for label, query in wanted:
        g = await geo.geocode(query)
        if not g:
            return None, f"Could not find {label} ({query!r}) in the GTA -- 4WARD only covers the Greater Toronto Area."
        if not geo.in_gta(g["lat"], g["lon"]):
            return None, f"{label.capitalize()} ({query!r}) is outside the GTA -- 4WARD only covers the Greater Toronto Area."
        if label.startswith("stop"):
            places["stops"].append({**user["stops"][len(places["stops"])], "lat": g["lat"], "lon": g["lon"]})
        else:
            places[label] = g
    return places, None


def trip_points(user: dict, places: dict) -> list[dict]:
    """The ordered stops with their A, B, C... labels (as typed), for map markers and the stop list."""
    seq = [("origin", places["origin"], {})] + [("stop", s, s) for s in places["stops"]] + [("destination", places["destination"], {})]
    return [{"label": trip.LETTERS[i], "name": (user["origin"] if k == "origin" else user["destination"] if k == "destination" else p["name"]),
             "lat": p["lat"], "lon": p["lon"], "type": k if k != "stop" else p.get("type", "stop"), "dwell_min": p.get("dwell_min", 0)}
            for i, (k, p, _) in enumerate(seq)]


async def shared_candidates(user: dict, places: dict) -> list[dict]:
    """The trip's ONE candidate set: every mode, computed once, ranked by all four bots. With stops it is a set of
    whole-trip candidates (backend/trip.py) shaped exactly like single-leg ones."""
    c = {k: v for k, v in user["constraints"].items() if k in UI_CONSTRAINTS and v is not None}
    constraints = planner.Constraints(modes=list(ALL_MODES), **c)
    o, d = places["origin"], places["destination"]
    if places["stops"]:
        plan = await trip.plan_trip(o, places["stops"], d, constraints, bool(user.get("optimize_order")))
    else:
        plan = await planner.plan((o["lat"], o["lon"]), (d["lat"], d["lon"]), constraints)
    return plan["candidates"]


def _summary(c: dict) -> dict:
    return {k: c.get(k) for k in ("id", "title", "available", "reason", "duration_min", "cost", "co2_kg", "transfers",
                                  "walk_m", "meets_deadline", "bag_friendliness", "pareto_optimal")}


async def run_all(prompt: str, emit: Emit | None = None, base_url: str | None = None, keys: list[str] | None = None,
                  stream_frames: bool = True, max_steps: int = 40, timeout_s: float = 150) -> dict:
    base_url = base_url or os.environ.get("DASHBOARD_URL", "http://localhost:3000")
    t0 = time.time()
    user = await parse_prompt(prompt)
    await _emit(emit, {"type": "parsed", **user})
    if not user["origin"] or not user["destination"]:
        return {"error": "Could not find an origin and destination in the prompt.", "parsed": user, "results": []}
    places, scope_error = await _locate(user)
    if scope_error:
        return {"error": scope_error, "parsed": user, "results": []}
    await _emit(emit, {"type": "points", "points": trip_points(user, places)})
    # Pin the departure to one clock time so the engine's run and every bot's page compute identical times.
    if not user["constraints"].get("depart_at"):
        user["constraints"]["depart_at"] = datetime.now(TORONTO).strftime("%H:%M")
    try:
        shared = await shared_candidates(user, places)
    except Exception as e:  # engine failure: say so rather than starting four bots that would hit the same failure
        return {"error": f"The routing engine failed: {type(e).__name__}: {str(e)[:160]}", "parsed": user, "results": []}
    keys = keys or list(PROFILES)
    await _warm_up(base_url)
    backend = os.environ.get("BROWSER_BACKEND", "local").lower()
    if backend == "local":
        results = await _run_local(keys, user, base_url, emit, max_steps, timeout_s, stream_frames, shared)
    elif backend == "steel":
        results = await _run_steel(keys, user, base_url, emit, max_steps, timeout_s, stream_frames, shared)
    else:
        raise NotImplementedError(f"BROWSER_BACKEND={backend!r} is not supported; use local or steel")
    critic.apply(results, user["constraints"])
    for r in results:
        if r.get("critic_violations"):
            await _emit(emit, {"type": "critic", "bot": r["bot"], "violations": r["critic_violations"]})
    # Runtime check of the optimum guarantee: a bot that failed is skipped (its own error is already shown).
    chosen = {r["bot"]: r.get("route") for r in results if r.get("status") == "DONE"}
    violations = optimize.check_invariants(chosen, shared, user["constraints"])
    if violations:
        await _emit(emit, {"type": "invariant_violation", "violations": violations})
    _, relaxed = optimize.eligible(shared, user["constraints"])
    return {"parsed": user, "results": results, "elapsed_s": round(time.time() - t0, 1),
            "points": trip_points(user, places), "candidates": [_summary(c) for c in shared], "relaxed": relaxed,
            "invariant_violations": violations, "route_tags": optimize.tags(chosen)}
