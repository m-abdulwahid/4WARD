"""Parse a plain-language prompt with Gemini (or rules), then run the four bots in parallel."""
import asyncio
import os
import time
from typing import Awaitable, Callable

import httpx
from playwright.async_api import async_playwright

from .nlp import parse_prompt
from .profiles import PROFILES
from .runner import run_bot

Emit = Callable[[dict], Awaitable[None] | None]


async def _warm_up(base_url: str) -> None:
    """Hit /planner once so a cold Next dev server compiles it before four browsers pile on."""
    try:
        async with httpx.AsyncClient(timeout=60) as cl:
            await cl.get(f"{base_url}/planner")
    except Exception:
        pass  # the bots will report a clear error if the dashboard is really down


async def run_all(prompt: str, emit: Emit | None = None, base_url: str | None = None, keys: list[str] | None = None,
                  stream_frames: bool = True, max_steps: int = 24, timeout_s: float = 120) -> dict:
    base_url = base_url or os.environ.get("DASHBOARD_URL", "http://localhost:3000")
    t0 = time.time()
    user = await parse_prompt(prompt)
    if emit:
        r = emit({"type": "parsed", **user})
        if asyncio.iscoroutine(r):
            await r
    if not user["origin"] or not user["destination"]:
        return {"error": "Could not find an origin and destination in the prompt.", "parsed": user, "results": []}
    keys = keys or list(PROFILES)
    await _warm_up(base_url)
    backend = os.environ.get("BROWSER_BACKEND", "local").lower()
    if backend != "local":
        raise NotImplementedError(f"BROWSER_BACKEND={backend!r} is not built yet (Phase 5); use BROWSER_BACKEND=local")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            results = await asyncio.gather(*[
                run_bot(browser, k, user, base_url, emit, max_steps=max_steps, timeout_s=timeout_s, stream_frames=stream_frames)
                for k in keys])
        finally:
            await browser.close()
    return {"parsed": user, "results": list(results), "elapsed_s": round(time.time() - t0, 1)}
