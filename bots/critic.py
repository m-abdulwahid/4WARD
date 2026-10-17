"""Critic pass: flag constraint violations in a bot's finished route.

Every flag here reads a field the routing engine already computed (meets_deadline, violates,
bag_friendliness). No new judgments are invented; this only surfaces engine output.
"""


def flags_for(route: dict | None, constraints: dict) -> list[str]:
    if not route:
        return []
    out = []
    if route.get("meets_deadline") is False:
        arrive_by = constraints.get("arrive_by")
        if arrive_by:
            out.append(f"Misses your {arrive_by} deadline (arrives {route.get('arrive')})")
        else:
            out.append("Misses the deadline")
    for v in route.get("violates") or []:
        if v == "max_walk":
            out.append(f"Walking {route.get('walk_m')} m exceeds your limit")
        else:
            out.append(f"Violates constraint: {v}")
    bags = constraints.get("bags", 0)
    bf = route.get("bag_friendliness")
    if bags and bf is not None and bf < 0.3:
        out.append(f"Hard to manage with {bags} bag(s) on this route")
    return out


def apply(results: list[dict], constraints: dict) -> None:
    """Mutates results in place: sets result['critic_violations'] and prepends flags to route['notes']."""
    for r in results:
        route = r.get("route")
        flags = flags_for(route, constraints)
        r["critic_violations"] = flags
        if flags and route is not None:
            route["notes"] = [f"⚠ {f}" for f in flags] + list(route.get("notes") or [])
