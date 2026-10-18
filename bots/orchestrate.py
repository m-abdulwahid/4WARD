"""Parse a plain-language prompt with Gemini (or rules), then run the four bots in parallel."""
import asyncio
import os
import time
from typing import Awaitable, Callable

import httpx
from playwright.async_api import async_playwright

from backend import geo

from . import critic
from .nlp import parse_prompt
from .profiles import PROFILES
from .runner import run_bot
from .steel_client import steel_session

Emit = Callable[[dict], Awaitable[None] | None]


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


async def _run_one_local(key, user, base_url, emit, max_steps, timeout_s, stream_frames, avoid_ids=None) -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            ctx = await browser.new_context(viewport={"width": 1000, "height": 640})
            return await run_bot(ctx, key, user, base_url, emit, max_steps=max_steps, timeout_s=timeout_s,
                                 stream_frames=stream_frames, avoid_ids=avoid_ids)
        finally:
            await browser.close()


async def _run_local(keys, user, base_url, emit, max_steps, timeout_s, stream_frames) -> list[dict]:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            return list(await asyncio.gather(*[
                run_bot(await browser.new_context(viewport={"width": 1000, "height": 640}), k, user, base_url,
                        emit, max_steps=max_steps, timeout_s=timeout_s, stream_frames=stream_frames)
                for k in keys]))
        finally:
            await browser.close()


async def _run_one_steel(key, user, base_url, emit, max_steps, timeout_s, stream_frames, avoid_ids=None) -> dict:
    # Steel's own session cap must outlast the bot's timeout_s, or Steel kills the session mid-run.
    async with steel_session(api_timeout_ms=int((timeout_s + 60) * 1000)) as bot:
        await _emit(emit, {"type": "steel_session", "bot": key, "session_id": bot.id, "live_view_url": bot.live_view_url})
        return await run_bot(bot.context, key, user, base_url, emit, max_steps=max_steps, timeout_s=timeout_s,
                             stream_frames=stream_frames, avoid_ids=avoid_ids)


async def _run_steel(keys, user, base_url, emit, max_steps, timeout_s, stream_frames) -> list[dict]:
    return list(await asyncio.gather(*[
        _run_one_steel(k, user, base_url, emit, max_steps, timeout_s, stream_frames) for k in keys]))


async def _resolve_diversity(keys, results, user, base_url, emit, max_steps, timeout_s, stream_frames, backend) -> list[dict]:
    """If two bots land on the same route (by id), rerun the lower-priority one (later in `keys`)
    telling it to avoid every route id already claimed."""
    claimed: dict[str, str] = {}
    reruns: list[tuple[int, str]] = []
    for i, key in enumerate(keys):
        rid = ((results[i] or {}).get("route") or {}).get("id")
        if not rid:
            continue
        if rid in claimed:
            reruns.append((i, key))
        else:
            claimed[rid] = key
    for i, key in reruns:
        dup_of = claimed[((results[i] or {}).get("route") or {}).get("id")]
        avoid = set(claimed)
        await _emit(emit, {"type": "diversity_rerun", "bot": key, "duplicate_of": dup_of, "avoid_ids": sorted(avoid)})
        run_one = _run_one_steel if backend == "steel" else _run_one_local
        new_result = await run_one(key, user, base_url, emit, max_steps, timeout_s, stream_frames, avoid_ids=avoid)
        results[i] = new_result
        new_rid = (new_result.get("route") or {}).get("id")
        if new_rid:
            claimed[new_rid] = key
    return results


async def _out_of_scope(user: dict) -> str | None:
    """Same GTA-only check the engine applies to every /route call, run up front so an unresolvable
    trip never spins up four cloud browsers (or lets a bot loop forever guessing spellings) just to fail."""
    misses = [label for label, q in (("origin", user["origin"]), ("destination", user["destination"]))
              if not await geo.geocode(q)]
    if not misses:
        return None
    bad = " and ".join(f"{label} ({user[label]!r})" for label in misses)
    return f"Could not find {bad} in the GTA -- 4WARD only covers the Greater Toronto Area."


async def run_all(prompt: str, emit: Emit | None = None, base_url: str | None = None, keys: list[str] | None = None,
                  stream_frames: bool = True, max_steps: int = 24, timeout_s: float = 120) -> dict:
    base_url = base_url or os.environ.get("DASHBOARD_URL", "http://localhost:3000")
    t0 = time.time()
    user = await parse_prompt(prompt)
    await _emit(emit, {"type": "parsed", **user})
    if not user["origin"] or not user["destination"]:
        return {"error": "Could not find an origin and destination in the prompt.", "parsed": user, "results": []}
    scope_error = await _out_of_scope(user)
    if scope_error:
        return {"error": scope_error, "parsed": user, "results": []}
    keys = keys or list(PROFILES)
    await _warm_up(base_url)
    backend = os.environ.get("BROWSER_BACKEND", "local").lower()
    if backend == "local":
        results = await _run_local(keys, user, base_url, emit, max_steps, timeout_s, stream_frames)
    elif backend == "steel":
        results = await _run_steel(keys, user, base_url, emit, max_steps, timeout_s, stream_frames)
    else:
        raise NotImplementedError(f"BROWSER_BACKEND={backend!r} is not supported; use local or steel")
    results = await _resolve_diversity(keys, results, user, base_url, emit, max_steps, timeout_s, stream_frames, backend)
    critic.apply(results, user["constraints"])
    for r in results:
        if r.get("critic_violations"):
            await _emit(emit, {"type": "critic", "bot": r["bot"], "violations": r["critic_violations"]})
    return {"parsed": user, "results": results, "elapsed_s": round(time.time() - t0, 1)}
