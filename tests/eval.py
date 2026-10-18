"""Phase 8 eval harness: run real saved prompts through the actual pipeline, report real numbers.

No mocking -- every prompt runs through bots.orchestrate.run_all exactly the way a user's trip does,
against whatever DASHBOARD_URL/BROWSER_BACKEND/.env the bots normally use. Fare correctness is
covered separately by tests/test_fares.py's 10 hand-checked trips (pure unit tests, not re-run here).

Usage:
    python -m tests.eval                    # full PROMPTS suite
    python -m tests.eval --n 5               # first 5 prompts only (cheaper/faster smoke run)
    python -m tests.eval --keys cheap,reliable  # fewer bots per prompt
    python -m tests.eval --md data/eval_results.md
"""
import argparse
import asyncio
import statistics
import time

from dotenv import load_dotenv

from bots.orchestrate import run_all
from bots.profiles import PROFILES

PROMPTS = [
    "York University to Union Station, 2 bags, no presto, arrive by 5pm",
    "UTSC to King and Spadina by 8:30am, no Presto card",
    "High Park to CN Tower, I have my car but don't want to drive downtown",
    "Kensington Market to Distillery District",
    "Kipling Station to CN Tower by 6pm with a suitcase, no Presto",
    "Union Station to Pearson Airport, 3 bags, I have my car",
    "Finch Station to Scarborough Town Centre, no presto card",
    "Vaughan Metropolitan Centre to Union Station by 9am",
    "High Park to Distillery District, I have my e-bike",
    "Eglinton Station to York University with a suitcase, avoid stairs",
    "Union Station to St Clair West, arrive by 6pm, no Presto",
    "Kipling Station to Union Station, I have my car but hate downtown traffic",
    "Dundas West Station to Union Station, 2 bags",
    "Union Station to UTSC by 8:30am with a suitcase",
    "CN Tower to York University, no presto, 1 bag",
    "Spadina Station to Distillery District",
    "Union Station to Finch Station, I have my bike",
    "Kensington Market to Pearson Airport, 2 bags, I have my car",
    "Union Station to High Park by 7pm",
    "Vaughan Metropolitan Centre to Scarborough Town Centre, no presto, 1 bag",
]


def _fmt_table(rows: list[dict]) -> str:
    cols = ["prompt", "success", "wall_s", "seq_equiv_s", "speedup_x", "avg_steps", "violations", "engine_check_failures"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    return "\n".join(lines)


async def _run_one(prompt: str, keys: list[str]) -> dict:
    events: list[dict] = []

    def emit(ev: dict) -> None:
        events.append(ev)

    t0 = time.time()
    out = await run_all(prompt, emit, keys=keys, stream_frames=False)
    wall_s = round(time.time() - t0, 1)

    results = out.get("results") or []
    n_done = sum(1 for r in results if r.get("status") == "DONE")
    seq_equiv = round(sum(r.get("elapsed_s", 0) for r in results), 1)
    violations = sum(len(r.get("critic_violations") or []) for r in results)
    engine_failures = len(out.get("invariant_violations") or [])
    avg_steps = round(statistics.mean(len(r.get("steps") or []) for r in results), 1) if results else 0.0

    return {
        "prompt": prompt[:40] + ("…" if len(prompt) > 40 else ""),
        "success": f"{n_done}/{len(keys)}" if not out.get("error") else "ERROR",
        "wall_s": wall_s,
        "seq_equiv_s": seq_equiv,
        "speedup_x": round(seq_equiv / wall_s, 2) if wall_s else "-",
        "avg_steps": avg_steps,
        "violations": violations,
        "engine_check_failures": engine_failures,
        "_n_done": n_done,
        "_n_keys": len(keys),
        "_error": out.get("error"),
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=len(PROMPTS), help="how many prompts to run (default: all)")
    ap.add_argument("--keys", default=",".join(PROFILES), help="comma-separated bot keys to run per prompt")
    ap.add_argument("--md", default="data/eval_results.md", help="where to write the markdown table")
    a = ap.parse_args()

    keys = [k.strip() for k in a.keys.split(",") if k.strip()]
    prompts = PROMPTS[: a.n]

    rows = []
    for i, p in enumerate(prompts, 1):
        print(f"[{i}/{len(prompts)}] {p}")
        rows.append(await _run_one(p, keys))

    total_runs = len(rows)
    full_success = sum(1 for r in rows if r["_n_done"] == r["_n_keys"])
    partial_success = sum(1 for r in rows if 0 < r["_n_done"] < r["_n_keys"])
    hard_fail = sum(1 for r in rows if r["_error"])
    speedups = [r["speedup_x"] for r in rows if isinstance(r["speedup_x"], (int, float))]

    summary = (
        f"\n## Summary ({total_runs} prompts, keys={','.join(keys)})\n\n"
        f"- All {len(keys)} bots finished: {full_success}/{total_runs} ({round(100*full_success/total_runs)}%)\n"
        f"- Partial (some bots finished): {partial_success}/{total_runs}\n"
        f"- Hard failures (prompt couldn't even parse/run): {hard_fail}/{total_runs}\n"
        f"- Parallel speedup vs running the same {len(keys)} bots one after another: "
        f"{round(statistics.mean(speedups), 2) if speedups else '-'}x average\n"
        f"- Total constraint violations caught by the critic pass: {sum(r['violations'] for r in rows)}\n"
        f"- Total engine optimum-check failures (each bot must beat or tie every eligible route on its own metric): {sum(r['engine_check_failures'] for r in rows)}\n"
    )

    table = _fmt_table(rows)
    out_md = f"# 4WARD eval results\n{summary}\n{table}\n"
    print("\n" + out_md)

    with open(a.md, "w") as f:
        f.write(out_md)
    print(f"\nwrote {a.md}")


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
