"""Objective-specific ranking of route candidates (deterministic, unit-tested)."""


def _usable(cands: list[dict]) -> list[dict]:
    return [c for c in cands if c.get("available") and c.get("duration_min") is not None and not c.get("violates")]


def _practical(cands: list[dict], bags: int) -> list[dict]:
    """Drop absurd options (e.g. a 5 hour walk) and bag-hostile ones, unless that would leave nothing."""
    if not cands:
        return cands
    fastest = min(c["duration_min"] for c in cands)
    out = [c for c in cands if c["duration_min"] <= max(90, 2.5 * fastest)]
    if bags:
        bag_ok = [c for c in out if c.get("bag_friendliness", 1) >= 0.3]
        out = bag_ok or out
    return out or cands


def _deadline_ok(cands: list[dict]) -> list[dict]:
    ok = [c for c in cands if c.get("meets_deadline", True)]
    return ok or cands


def _reliability_penalty(c: dict) -> float:
    """0 (reliable, or no Tiger Data signal yet) .. 1 (basically never makes it on time).

    Confidence ramps up with history_days (how many matching past days Tiger Data actually had),
    so a thin history barely moves the ranking and a route with two weeks of matching history behind
    it moves it fully -- this is the literal mechanism by which collecting more delay data makes
    routing better over time, not a separate training step."""
    rel = c.get("reliability")
    if not rel:
        return 0.0
    confidence = min(1.0, rel["history_days"] / 14)
    return (1 - rel["reliability_pct"] / 100) * confidence


def rank(profile_key: str, cands: list[dict], constraints: dict) -> list[dict]:
    """Best first. Each returned candidate is annotated with `score` (lower is better) and `why`."""
    pool = _deadline_ok(_practical(_usable(cands), constraints.get("bags", 0)))
    if not pool:
        return []
    tmax = max(c["duration_min"] for c in pool) or 1
    cmax = max(c["cost"] for c in pool) or 1
    gmax = max(c["co2_kg"] for c in pool) or 1
    bags = constraints.get("bags", 0)

    def score(c: dict) -> float:
        t, cost, co2 = c["duration_min"] / tmax, c["cost"] / cmax, c["co2_kg"] / gmax
        rel = _reliability_penalty(c)
        # Weighted differently per objective: a "fastest" pick that's unreliable defeats its own point,
        # so it's penalized hardest; "cheap" cares about it least.
        if profile_key == "cheap":
            return round(cost * 10 + t + rel * 0.5, 4)
        if profile_key == "fastest":
            return round(t * 10 + cost * 0.1 + rel * 3.0, 4)
        if profile_key == "green":
            return round(co2 * 10 + t * 0.5 + cost * 0.05 + rel * 0.5, 4)
        walk = min(1.0, c["walk_m"] / 3000)
        bag_pen = (1 - c.get("bag_friendliness", 1)) * (1.0 if bags else 0.05)
        return round(0.35 * t + 0.2 * cost + 0.15 * co2 + 0.05 * c["transfers"] + 0.1 * walk + bag_pen + rel * 0.25, 4)

    ranked = sorted(pool, key=score)
    for c in ranked:
        c["score"] = score(c)
    return ranked


def best(profile_key: str, cands: list[dict], constraints: dict) -> dict | None:
    r = rank(profile_key, cands, constraints)
    return r[0] if r else None
