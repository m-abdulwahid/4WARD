"""One bot = one Playwright browser context driving the /planner dashboard, one action per loop step."""
import asyncio
import base64
import time
from typing import Awaitable, Callable

from playwright.async_api import BrowserContext, Page

from . import optimize
from .brain import FallbackBrain, validate
from .critic import flags_for
from .profiles import PROFILES

Emit = Callable[[dict], Awaitable[None] | None]

OBSERVE_JS = """() => {
  const controls = Array.from(document.querySelectorAll('[data-testid]')).map(e => ({
    testid: e.dataset.testid, tag: e.tagName.toLowerCase(),
    text: ((e.tagName === 'INPUT') ? '' : (e.innerText || '')).trim().slice(0, 60),
    pressed: e.getAttribute('aria-pressed'), value: (e.tagName === 'INPUT') ? e.value : undefined,
    disabled: e.disabled === true ? true : undefined,
  })).filter(c => c.tag === 'button' || c.tag === 'input');
  const w = window.__4ward || {};
  return {controls, state: w.state || 'idle', error: w.error || '', selected: w.selected || null,
          confirmed: (w.confirmed && w.confirmed.id) || null, candidates: w.candidates || []};
}"""


async def _emit(cb: Emit | None, ev: dict) -> None:
    if cb:
        r = cb(ev)
        if asyncio.iscoroutine(r):
            await r


async def _frames(page: Page, key: str, cb: Emit | None, stop: asyncio.Event, fps: float) -> None:
    while not stop.is_set():
        try:
            img = await page.screenshot(type="jpeg", quality=45)
            await _emit(cb, {"type": "frame", "bot": key, "data": base64.b64encode(img).decode()})
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=1 / fps)
        except asyncio.TimeoutError:
            pass


async def run_bot(ctx: BrowserContext, key: str, user: dict, base_url: str, emit: Emit | None = None,
                  max_steps: int = 24, timeout_s: float = 120, stream_frames: bool = True,
                  shared: list[dict] | None = None) -> dict:
    """user = {"origin": str, "destination": str, "constraints": {...}}. Returns the bot's result dict.

    shared is the trip's ONE candidate set (computed once by the orchestrator). The bot operates the UI, but its
    route is always optimize.top() over that set, so no UI quirk can change which route an objective gets.

    ctx is a browser context the bot owns exclusively (a fresh local context, or a Steel session's
    context) and closes when done.
    """
    prof = PROFILES[key]
    t0 = time.time()
    steps: list[dict] = []
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    stop = asyncio.Event()
    frame_task = None
    result = {"bot": key, "name": prof.name, "status": "FAILED", "route": None, "alternatives": [], "next_best": None,
              "steps": steps, "brain": "rule", "error": None}
    try:
        await _emit(emit, {"type": "status", "bot": key, "status": "SCANNING"})
        await page.goto(f"{base_url}/planner?bot={key}", wait_until="domcontentloaded", timeout=60000)
        await page.get_by_test_id("compute-route").wait_for(timeout=60000)
        await page.wait_for_function("window.__4ward !== undefined", timeout=60000)  # React hydrated
        if stream_frames:
            frame_task = asyncio.create_task(_frames(page, key, emit, stop, 2.5))
        brain = FallbackBrain(key, user, shared=shared)
        shared_ids = {c["id"] for c in shared or [] if c.get("available")}
        result["brain"] = brain.name
        history: list[dict] = []
        for n in range(1, max_steps + 1):
            if time.time() - t0 > timeout_s:
                raise TimeoutError(f"bot exceeded {timeout_s}s")
            obs = await page.evaluate(OBSERVE_JS)
            action = await brain.next_action(obs, history)
            rec = {"n": n, "t": round(time.time() - t0, 1), **{k: v for k, v in action.items() if v is not None}}
            if action["action"] == "finish":
                rid = action.get("route_id")
                rec["outcome"] = "ok" if rid else "no route"
                steps.append(rec)
                await _emit(emit, {"type": "step", "bot": key, **rec})
                if rid:
                    pool = shared or obs["candidates"]  # the shared set is the single source of truth
                    chosen = next((c for c in pool if c["id"] == rid), None)
                    # This bot's own top 3 and its best DIFFERENT route ("next best for this goal"), from the
                    # same ranking that picked its winner -- a comparison, not a second search.
                    ranked = optimize.rank(key, pool, user["constraints"])
                    top3 = ranked[:3]
                    for alt in top3:
                        alt["critic_violations"] = flags_for(alt, user["constraints"])
                    nxt = next((c for c in ranked if chosen and c["id"] != chosen["id"]), None)
                    # Did the page this bot operated show the same numbers as the engine's shared set? (live data
                    # such as Bike Share can move between the two computations; the shared set stays the truth.)
                    page_by_id = {c["id"]: c for c in obs["candidates"]}
                    result["ui_mismatch"] = [c["id"] for c in (shared or []) if c.get("available") and (
                        c["id"] not in page_by_id or abs((page_by_id[c["id"]].get("duration_min") or 0) - c["duration_min"]) > 0.15
                        or abs((page_by_id[c["id"]].get("cost") or 0) - c["cost"]) > 0.01)]
                    result.update(status="DONE", route=chosen, alternatives=top3, next_best=nxt)
                else:
                    result["error"] = action.get("reason") or "no route"
                break
            try:
                validate(action, obs, shared_ids)
                loc = page.get_by_test_id(action["testid"]) if action["action"] != "wait" else None
                if action["action"] == "type":
                    await loc.fill(action["text"], timeout=5000)
                elif action["action"] == "click":
                    await loc.click(timeout=5000)
                else:
                    # Waiting on a slow route computation (trips with stops can take a while): block until it ends,
                    # bounded, rather than spending one of the step cap's steps every half second.
                    try:
                        await page.wait_for_function("!window.__4ward || window.__4ward.state !== 'loading'", timeout=30000)
                    except Exception:
                        pass
                if action["action"] != "wait":
                    await page.wait_for_function("!window.__4ward || window.__4ward.state !== 'loading'", timeout=45000)
                    await page.wait_for_timeout(120)
                rec["outcome"] = "ok"
                if action.get("testid") == "compute-route":
                    await _emit(emit, {"type": "status", "bot": key, "status": "ROUTING"})
            except Exception as e:
                rec["outcome"] = f"error: {str(e)[:160]}"
            history.append({k: rec.get(k) for k in ("action", "testid", "text", "outcome")})
            steps.append(rec)
            await _emit(emit, {"type": "step", "bot": key, **rec})
        else:
            result["error"] = f"hit step cap ({max_steps})"
        result["fallbacks"] = brain.fallbacks
        result["gemini_calls"] = brain.gemini_calls
        result["brain_error"] = brain.last_error
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        result["elapsed_s"] = round(time.time() - t0, 1)
        if stream_frames:
            try:
                img = await page.screenshot(type="jpeg", quality=45)
                await _emit(emit, {"type": "frame", "bot": key, "data": base64.b64encode(img).decode()})
            except Exception:
                pass
        stop.set()
        if frame_task:
            try:
                await asyncio.wait_for(frame_task, timeout=3)
            except Exception:
                pass
        await ctx.close()
        await _emit(emit, {"type": "status", "bot": key, "status": result["status"]})
        await _emit(emit, {"type": "result", **result})
    return result
