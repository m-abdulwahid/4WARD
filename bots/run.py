"""CLI: python -m bots.run --prompt "..."  (backend on :8000 and dashboard on :3000 must be running)."""
import argparse
import asyncio
import json

from dotenv import load_dotenv

from .orchestrate import run_all


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    async def emit(ev: dict) -> None:
        if a.verbose and ev["type"] == "step":
            print(f"  [{ev['bot']}] step {ev['n']}: {ev['action']} {ev.get('testid', '')} {ev.get('text', '')} -> {ev.get('outcome')}")

    out = await run_all(a.prompt, emit, a.base_url, stream_frames=False)
    if a.json:
        print(json.dumps(out, indent=1, default=str))
        return
    print("Parsed:", json.dumps(out.get("parsed"), default=str))
    for r in out["results"]:
        route = r["route"]
        line = f"{r['name']:<14} {r['status']:<6} brain={r['brain']:<6} {r['elapsed_s']:>5}s "
        if route:
            line += f"{route['title']}: {route['duration_min']:.0f} min, ${route['cost']:.2f}, {route['co2_kg']:.2f} kg CO2, {route['transfers']} transfers"
        else:
            line += f"error={r['error']}"
        print(line)
    print("total", out.get("elapsed_s"), "s")


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
