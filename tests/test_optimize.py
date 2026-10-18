"""The optimum guarantee: after hard constraints, each bot's route beats or ties every eligible candidate on its own
metric. Property-style tests over seeded random candidate sets, plus the runtime check that flags a violation."""
import random

import pytest

from bots import optimize
from bots.profiles import PROFILES


def make(i: int, rnd: random.Random) -> dict:
    c = {"id": f"r{i}", "title": f"route {i}", "available": rnd.random() > 0.1, "duration_min": round(rnd.uniform(8, 120), 1),
         "cost": round(rnd.choice([0, rnd.uniform(1, 45)]), 2), "co2_kg": round(rnd.choice([0, rnd.uniform(0.1, 9)]), 3),
         "transfers": rnd.randint(0, 3), "walk_m": rnd.randint(0, 4000), "bag_friendliness": round(rnd.random(), 2)}
    if rnd.random() < 0.7:
        c["meets_deadline"] = rnd.random() > 0.3
    if rnd.random() < 0.5:
        c["reliability"] = {"reliability_pct": rnd.randint(0, 100), "history_days": rnd.randint(1, 30)}
    if rnd.random() < 0.1:
        c["violates"] = ["max_walk"]
    return c


def random_case(seed: int) -> tuple[list[dict], dict]:
    rnd = random.Random(seed)
    return [make(i, rnd) for i in range(rnd.randint(1, 12))], {"bags": rnd.choice([0, 0, 2])}


@pytest.mark.parametrize("seed", range(300))
def test_every_bot_beats_or_ties_every_eligible_candidate(seed):
    cands, constraints = random_case(seed)
    pool, _ = optimize.eligible(cands, constraints)
    chosen = optimize.picks(cands, constraints)
    assert optimize.check_invariants(chosen, cands, constraints) == []
    if not pool:
        assert all(v is None for v in chosen.values())
        return
    assert chosen["fastest"]["duration_min"] <= min(c["duration_min"] for c in pool)
    assert chosen["cheap"]["cost"] <= min(c["cost"] for c in pool)
    assert chosen["green"]["co2_kg"] <= min(c["co2_kg"] for c in pool)
    known = [optimize.on_time_pct(c) for c in pool if optimize.on_time_pct(c) is not None]
    if known:
        assert optimize.on_time_pct(chosen["reliable"]) >= max(known)


@pytest.mark.parametrize("seed", range(50))
def test_ranking_is_deterministic_and_independent_of_input_order(seed):
    cands, constraints = random_case(seed)
    shuffled = list(cands)
    random.Random(seed + 1).shuffle(shuffled)
    for p in PROFILES:
        a, b = optimize.top(p, cands, constraints), optimize.top(p, shuffled, constraints)
        assert (a and a["id"]) == (b and b["id"])


def test_ties_break_by_the_next_objectives_not_input_order():
    a = {"id": "a", "available": True, "duration_min": 30, "cost": 5, "co2_kg": 1}
    b = {"id": "b", "available": True, "duration_min": 30, "cost": 3, "co2_kg": 1}
    assert optimize.top("fastest", [a, b], {})["id"] == "b"   # same time -> cheaper
    assert optimize.top("fastest", [b, a], {})["id"] == "b"


def test_reliable_ties_break_by_faster_then_cheaper():
    base = {"available": True, "duration_min": 40, "cost": 10, "co2_kg": 1}
    a = {**base, "id": "a", "reliability": {"reliability_pct": 90, "history_days": 10}}
    b = {**base, "id": "b", "duration_min": 25, "reliability": {"reliability_pct": 90, "history_days": 10}}  # faster, same pct
    c = {**base, "id": "c", "duration_min": 25, "cost": 4, "reliability": {"reliability_pct": 90, "history_days": 10}}  # faster and cheaper
    assert optimize.top("reliable", [a, b], {})["id"] == "b"       # tie on pct -> faster wins
    assert optimize.top("reliable", [a, b, c], {})["id"] == "c"    # tie on pct and time -> cheaper wins
    assert optimize.top("reliable", [c, b, a], {})["id"] == "c"    # order-independent


def test_invariant_check_flags_a_wrong_pick():
    cands = [{"id": "slow", "available": True, "duration_min": 50, "cost": 1, "co2_kg": 0},
             {"id": "fast", "available": True, "duration_min": 20, "cost": 9, "co2_kg": 3}]
    chosen = optimize.picks(cands, {})
    assert optimize.check_invariants(chosen, cands, {}) == []
    chosen["fastest"] = cands[0]  # a bug: Efficient returned the slower route
    bad = optimize.check_invariants(chosen, cands, {})
    assert len(bad) == 1 and bad[0].startswith("Efficient") and "'fast'" in bad[0]


def test_invariant_check_flags_numbers_that_differ_from_the_shared_set():
    cands = [{"id": "x", "available": True, "duration_min": 20, "cost": 9, "co2_kg": 3}]
    chosen = optimize.picks(cands, {})
    chosen["cheap"] = {**cands[0], "cost": 1.0}
    assert any("differs from the shared set" in v for v in optimize.check_invariants(chosen, cands, {}))


def test_failed_bot_is_skipped_not_flagged():
    cands = [{"id": "x", "available": True, "duration_min": 20, "cost": 9, "co2_kg": 3}]
    assert optimize.check_invariants({"cheap": cands[0]}, cands, {}) == []


def test_tags_merge_bots_that_win_the_same_route():
    cands = [{"id": "only", "available": True, "duration_min": 20, "cost": 9, "co2_kg": 3}]
    assert optimize.tags(optimize.picks(cands, {})) == {"only": ["cheap", "fastest", "green", "reliable"]}


def test_next_best_is_a_different_route_for_the_same_goal():
    cands = [{"id": "a", "available": True, "duration_min": 20, "cost": 9, "co2_kg": 3},
             {"id": "b", "available": True, "duration_min": 30, "cost": 2, "co2_kg": 1}]
    assert optimize.top("fastest", cands, {})["id"] == "a"
    assert optimize.next_best("fastest", cands, {})["id"] == "b"
    assert optimize.next_best("fastest", cands[:1], {}) is None


def test_practical_cap_removes_absurd_options_for_every_bot():
    walk = {"id": "walk", "available": True, "duration_min": 700, "cost": 0, "co2_kg": 0}
    rail = {"id": "rail", "available": True, "duration_min": 60, "cost": 9, "co2_kg": 2}
    assert optimize.top("cheap", [walk, rail], {})["id"] == "rail"
    assert optimize.top("green", [walk, rail], {})["id"] == "rail"


def test_relaxations_are_reported_not_silent():
    late = {"id": "late", "available": True, "duration_min": 20, "cost": 1, "co2_kg": 0, "meets_deadline": False}
    pool, relaxed = optimize.eligible([late], {})
    assert [c["id"] for c in pool] == ["late"] and relaxed == ["No route makes your deadline"]
