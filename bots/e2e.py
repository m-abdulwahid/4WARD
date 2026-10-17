"""Run several different prompts end to end and report time + failures. Needs backend :8000 and dashboard :3000 up.

  python -m bots.e2e            all prompts
  python -m bots.e2e --fast     first two only
"""
import asyncio
import sys
import time

from dotenv import load_dotenv

from .orchestrate import run_all

PROMPTS = [
    "Get me from York University to Union Station by 5pm. I have 2 big bags, no Presto card, but I have my car.",
    "I'm at Kipling Station and need to reach the CN Tower by 6pm with a suitcase. No Presto card.",
    "From the Beaches to Pearson Airport, leaving at 7am, I have 2 suitcases and an e-bike.",
    "Get from Scarborough Town Centre to the University of Toronto, I have an electric car and want to arrive by 9am.",
    "Take me from Kensington Market to the Distillery District, I'm walking-friendly and have no bags.",
]


async def main() -> int:
    prompts = PROMPTS[:2] if "--fast" in sys.argv else PROMPTS
    failures = 0
    for p in prompts:
        t = time.time()
        out = await run_all(p, stream_frames=False)
        dt = time.time() - t
        print(f"\n> {p}\n  parsed: {out.get('parsed', {}).get('origin')} -> {out.get('parsed', {}).get('destination')}  ({dt:.1f}s)")
        if out.get("error"):
            print("  ERROR:", out["error"])
            failures += 1
            continue
        for r in out["results"]:
            rt = r["route"]
            if r["status"] != "DONE":
                failures += 1
            print(f"  {r['name']:<13} {r['status']:<6} " + (f"{rt['title']:<22} {rt['duration_min']:>6.0f} min ${rt['cost']:>6.2f} {rt['co2_kg']:>5.2f} kg" if rt else f"error={r['error']}"))
    print(f"\n{failures} bot failure(s) across {len(prompts)} prompts")
    return failures


if __name__ == "__main__":
    load_dotenv()
    sys.exit(1 if asyncio.run(main()) else 0)
