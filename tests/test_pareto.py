from backend import pareto


def cand(id, t, cost, co2, transfers=0, walk=300, available=True):
    return {"id": id, "available": available, "duration_min": t, "cost": cost, "co2_kg": co2,
            "transfers": transfers, "walk_m": walk}


def test_strictly_worse_candidate_is_dominated():
    a = cand("fast_cheap_clean", 20, 3.0, 0.5)
    b = cand("worse_everywhere", 30, 5.0, 1.0)  # loses on every metric
    pareto.mark([a, b])
    assert a["pareto_optimal"] is True
    assert b["pareto_optimal"] is False


def test_tradeoffs_are_all_pareto_optimal():
    fast = cand("fast", 10, 20.0, 5.0)     # best time, worst cost/co2
    cheap = cand("cheap", 60, 3.0, 0.5)    # best cost/co2, worst time
    pareto.mark([fast, cheap])
    assert fast["pareto_optimal"] is True
    assert cheap["pareto_optimal"] is True


def test_ties_do_not_dominate():
    a = cand("a", 20, 3.0, 0.5)
    b = cand("b", 20, 3.0, 0.5)  # identical on every metric
    pareto.mark([a, b])
    assert a["pareto_optimal"] is True
    assert b["pareto_optimal"] is True


def test_unavailable_candidates_are_skipped_and_untouched():
    a = cand("a", 20, 3.0, 0.5)
    b = cand("b", 999, 999, 999, available=False)
    pareto.mark([a, b])
    assert a["pareto_optimal"] is True
    assert "pareto_optimal" not in b


def test_dominates_requires_strictly_better_on_at_least_one():
    a = cand("a", 20, 3.0, 0.5)
    b = cand("b", 20, 3.0, 0.5)
    assert pareto.dominates(a, b) is False  # equal everywhere: neither dominates
