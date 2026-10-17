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
        if profile_key == "cheap":
            return round(cost * 10 + t, 4)
        if profile_key == "fastest":
            return round(t * 10 + cost * 0.1, 4)
        if profile_key == "green":
            return round(co2 * 10 + t * 0.5 + cost * 0.05, 4)
        walk = min(1.0, c["walk_m"] / 3000)
        bag_pen = (1 - c.get("bag_friendliness", 1)) * (1.0 if bags else 0.05)
        return round(0.35 * t + 0.2 * cost + 0.15 * co2 + 0.05 * c["transfers"] + 0.1 * walk + bag_pen, 4)

    ranked = sorted(pool, key=score)
    for c in ranked:
        c["score"] = score(c)
    return ranked


def best(profile_key: str, cands: list[dict], constraints: dict) -> dict | None:
    r = rank(profile_key, cands, constraints)
    return r[0] if r else None
