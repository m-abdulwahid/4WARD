"""Each bot's optimum over ONE shared candidate set. Pure, deterministic, no LLM, no I/O.

  Economic      (cheap)    lowest total cost
  Efficient     (fastest)  lowest door-to-door time
  Environmental (green)    lowest CO2e
  Reliable      (reliable) highest on-time probability

Every bot ranks the same eligible set (see eligible()) by exactly one objective, with explicit tie-breaks, so a
bot's pick can never be beaten on its own metric by another candidate. check_invariants() verifies that
independently of rank() and is run on every trip.
"""
from __future__ import annotations

from backend.config import PRACTICAL_FACTOR, PRACTICAL_MIN_CAP_MIN

PROFILES = ("cheap", "fastest", "green", "reliable")
INF = float("inf")


def on_time_pct(c: dict) -> float | None:
    """The one on-time function every card chip and the Reliable bot use. None = no estimate yet (never a made-up 100)."""
    rel = c.get("reliability")
    return float(rel["reliability_pct"]) if rel else None


def _sort_key(profile: str, c: dict) -> tuple:
    t, cost, co2, ident = c["duration_min"], c["cost"], c["co2_kg"], c["id"]
    if profile == "cheap":
        return (cost, t, co2, ident)
    if profile == "fastest":
        return (t, cost, co2, ident)
    if profile == "green":
        return (co2, t, cost, ident)
    if profile == "reliable":
        pct = on_time_pct(c)
        return (0 if pct is not None else 1, -(pct or 0.0), t, cost, co2, ident)
    raise KeyError(profile)


def eligible(cands: list[dict], constraints: dict) -> tuple[list[dict], list[str]]:
    """The set every bot ranks: hard constraints applied identically for all four.

    Returns (pool, relaxed) where `relaxed` names any soft rule that had to be dropped because nothing passed it,
    or that narrowed every bot down to the same single option (shown to the user, never silent). Order: usable ->
    practical time cap -> bags -> deadline.
    """
    relaxed: list[str] = []
    pool = [c for c in cands if c.get("available") and c.get("duration_min") is not None and c.get("cost") is not None
            and c.get("co2_kg") is not None and not c.get("violates")]
    if not pool:
        return [], relaxed
    cap = max(PRACTICAL_MIN_CAP_MIN, PRACTICAL_FACTOR * min(c["duration_min"] for c in pool))
    pool = [c for c in pool if c["duration_min"] <= cap]
    if constraints.get("bags"):
        ok = [c for c in pool if c.get("bag_friendliness", 1) >= 0.3]
        if ok:
            pool = ok
        else:
            relaxed.append("Every route is hard to manage with your bags")
    ok = [c for c in pool if c.get("meets_deadline", True)]
    if ok:
        # Every bot ranks this same shared pool, so when the deadline leaves only one option, all four land on
        # it -- not a diversity bug, the deadline itself removed the choice. Say so instead of leaving it silent.
        if len(ok) == 1 and len(pool) > 1:
            relaxed.append(f"Only {ok[0]['title']} makes your deadline, so all four bots land on it -- "
                            "the other ways to go were too slow, not ruled out for any other reason")
        pool = ok
    else:
        relaxed.append("No route makes your deadline")
    return pool, relaxed


def rank(profile: str, cands: list[dict], constraints: dict) -> list[dict]:
    """Eligible candidates, best first for this bot's objective. Annotates each with `objective_value`."""
    pool, _ = eligible(cands, constraints)
    out = sorted((dict(c) for c in pool), key=lambda c: _sort_key(profile, c))
    for c in out:
        c["objective_value"] = {"cheap": c["cost"], "fastest": c["duration_min"], "green": c["co2_kg"],
                                "reliable": on_time_pct(c)}[profile]
    return out


def top(profile: str, cands: list[dict], constraints: dict) -> dict | None:
    r = rank(profile, cands, constraints)
    return r[0] if r else None


def next_best(profile: str, cands: list[dict], constraints: dict) -> dict | None:
    """This bot's best DIFFERENT route ("next best for this goal")."""
    r = rank(profile, cands, constraints)
    return r[1] if len(r) > 1 else None


def picks(cands: list[dict], constraints: dict) -> dict[str, dict | None]:
    return {p: top(p, cands, constraints) for p in PROFILES}


def convergence_note(chosen: dict[str, dict | None], pool: list[dict]) -> str | None:
    """All four bots can honestly agree two ways: the deadline (or another hard rule) left only one eligible
    option (eligible() already explains that in `relaxed`), or one real option is simply better on every
    measure. The second case needs its own explanation, or it reads the same as a diversity bug: four bots,
    one answer, no reason given. None when the bots didn't actually all agree, or when there was never a
    second eligible option to prefer it over."""
    ids = {c["id"] for c in chosen.values() if c}
    if len(ids) != 1 or len({c["id"] for c in pool}) < 2:
        return None
    winner = next((c for c in pool if c["id"] == next(iter(ids))), None)
    if not winner:
        return None
    return f"{winner['title']} is the better pick on every measure for this trip right now, so all four bots agree."


def tags(chosen: dict[str, dict | None]) -> dict[str, list[str]]:
    """route id -> every bot (profile key) whose pick it is, in fixed bot order."""
    out: dict[str, list[str]] = {}
    for p in PROFILES:
        c = chosen.get(p)
        if c:
            out.setdefault(c["id"], []).append(p)
    return out


def check_invariants(chosen: dict[str, dict | None], cands: list[dict], constraints: dict) -> list[str]:
    """Independent of rank(): does each bot's route beat or tie every eligible candidate on its own metric?
    Returns human-readable violations (empty = all four hold). A non-empty result is an engine bug and is shown in the UI."""
    pool, _ = eligible(cands, constraints)
    by_id = {c["id"]: c for c in pool}
    bad: list[str] = []
    metric = {
        "cheap": ("Economic", "cost", lambda c: c["cost"]),
        "fastest": ("Efficient", "time", lambda c: c["duration_min"]),
        "green": ("Environmental", "CO2e", lambda c: c["co2_kg"]),
        "reliable": ("Reliable", "on-time %", lambda c: INF if on_time_pct(c) is None else -on_time_pct(c)),
    }
    for p, (name, what, f) in metric.items():
        if p not in chosen:  # a bot that failed has no route to check; its own error is reported separately
            continue
        pick = chosen.get(p)
        if not pool:
            if pick:
                bad.append(f"{name} returned a route but no route is eligible")
            continue
        if not pick:
            bad.append(f"{name} returned no route although {len(pool)} are eligible")
            continue
        if pick["id"] not in by_id:
            bad.append(f"{name} picked '{pick['id']}', which is not in the shared eligible set")
            continue
        ref = by_id[pick["id"]]
        for k in ("duration_min", "cost", "co2_kg"):
            if abs((pick.get(k) or 0) - ref[k]) > 1e-9:
                bad.append(f"{name}'s {k} for '{pick['id']}' ({pick.get(k)}) differs from the shared set ({ref[k]})")
        best = min(pool, key=f)
        if f(ref) > f(best) + 1e-9:
            bad.append(f"{name} picked '{ref['id']}' but '{best['id']}' has a better {what}")
    return bad
