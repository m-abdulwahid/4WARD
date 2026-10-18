"""Pareto-frontier ranking: the non-dominated set of candidates across time, cost, emissions,
transfers, and walking distance.

This doesn't replace each bot's single weighted pick (bots/scoring.py's per-objective scalarization)
-- it's the more rigorous multi-objective answer sitting alongside it: which routes aren't strictly
beaten by any other candidate on every metric at once. Pure, deterministic, no LLM.
"""

METRICS = ("duration_min", "cost", "co2_kg", "transfers", "walk_m")


def dominates(a: dict, b: dict) -> bool:
    """True if a is at least as good as b on every metric and strictly better on at least one."""
    le_all = all(a[m] <= b[m] for m in METRICS)
    lt_any = any(a[m] < b[m] for m in METRICS)
    return le_all and lt_any


def mark(candidates: list[dict]) -> None:
    """Mutates candidates in place: sets candidate['pareto_optimal'] for every available candidate
    that has all five METRICS. Candidates missing a metric (unavailable/incomplete) are left alone."""
    pool = [c for c in candidates if c.get("available") and all(c.get(m) is not None for m in METRICS)]
    for c in pool:
        c["pareto_optimal"] = not any(dominates(o, c) for o in pool if o is not c)
