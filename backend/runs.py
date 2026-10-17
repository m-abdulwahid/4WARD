"""Run registry: start a 4-bot run, fan events out to WebSocket subscribers, keep results."""
import asyncio
import time
import uuid

from bots.orchestrate import run_all

from .db import store

RUNS: dict[str, dict] = {}


def _new(prompt: str) -> dict:
    rid = uuid.uuid4().hex[:10]
    run = {"id": rid, "prompt": prompt, "status": "running", "started": time.time(), "events": [], "frames": {},
           "subs": set(), "result": None}
    RUNS[rid] = run
    return run


async def _fanout(run: dict, ev: dict) -> None:
    if ev["type"] == "frame":
        run["frames"][ev["bot"]] = ev  # keep only the newest frame per bot
    else:
        run["events"].append(ev)
    for q in list(run["subs"]):
        if q.full():
            try:
                q.get_nowait()  # drop oldest instead of blocking the bots
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(ev)


async def _execute(run: dict, options: dict) -> None:
    try:
        out = await run_all(run["prompt"], emit=lambda ev: _fanout(run, ev), **options)
        run["result"] = out
        run["status"] = "error" if out.get("error") else "done"
        try:
            await asyncio.to_thread(store.insert_run, run["id"], run["prompt"], {k: v for k, v in out.items() if k != "frames"})
        except Exception:
            pass  # persistence must never break a run
        await _fanout(run, {"type": "run_done", "status": run["status"], "result": _public(out)})
    except Exception as e:  # never leave a run hanging
        run["status"] = "error"
        run["result"] = {"error": f"{type(e).__name__}: {e}", "results": []}
        await _fanout(run, {"type": "run_done", "status": "error", "result": run["result"]})


def _public(out: dict) -> dict:
    return out


def start(prompt: str, **options) -> dict:
    run = _new(prompt)
    run["task"] = asyncio.create_task(_execute(run, options))
    return run


def snapshot(run: dict) -> dict:
    return {"id": run["id"], "prompt": run["prompt"], "status": run["status"], "events": run["events"],
            "frames": list(run["frames"].values()), "result": run["result"]}
