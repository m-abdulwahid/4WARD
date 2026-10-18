"""MVP constraint 1 (CLAUDE.md): "Bags: avoid stairs, long walks, and transfers with no elevator."
Direct unit tests of planner._finish's bag_friendliness heuristic, isolating each factor."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend import planner

TORONTO = ZoneInfo("America/Toronto")
HERE = (43.6453, -79.3806)
DEPART = datetime(2026, 1, 15, 8, 30, tzinfo=TORONTO)


def _g(distance_km=0.0):
    return {"distance_km": distance_km, "duration_s": 600, "geometry": [list(HERE)], "provenance": "estimated"}


def _subway_leg():
    return planner._leg("subway", "TTC subway", HERE, HERE, _g(), cost=3.3, co2=0.5, dur=600)


def _walk_leg(distance_km):
    return planner._leg("walk", "Walk", HERE, HERE, _g(distance_km), dur=int(distance_km * 1000 / 1.3))


def _finish(legs, bags, avoid_stairs, transfers=0):
    c = planner.Constraints(bags=bags, avoid_stairs=avoid_stairs)
    return planner._finish("t", "title", legs, c, DEPART, DEPART.hour, transfers, [])


def test_no_bags_means_full_bag_friendliness_regardless_of_the_route():
    cand = _finish([_subway_leg()], bags=0, avoid_stairs=True)
    assert cand["bag_friendliness"] == 1.0


def test_avoiding_stairs_penalizes_a_subway_leg_when_carrying_bags():
    without = _finish([_subway_leg()], bags=2, avoid_stairs=False)
    with_ = _finish([_subway_leg()], bags=2, avoid_stairs=True)
    assert without["bag_friendliness"] == 1.0
    assert with_["bag_friendliness"] == pytest.approx(0.9)


def test_avoid_stairs_has_no_effect_without_a_subway_leg():
    cand = _finish([_walk_leg(0.1)], bags=2, avoid_stairs=True)
    without = _finish([_walk_leg(0.1)], bags=2, avoid_stairs=False)
    assert cand["bag_friendliness"] == without["bag_friendliness"]


def test_long_walks_and_transfers_both_hurt_bag_friendliness_with_bags():
    short_no_transfer = _finish([_walk_leg(0.1)], bags=2, avoid_stairs=False, transfers=0)
    long_with_transfer = _finish([_walk_leg(2.0)], bags=2, avoid_stairs=False, transfers=2)
    assert long_with_transfer["bag_friendliness"] < short_no_transfer["bag_friendliness"]


def test_bag_friendliness_never_goes_negative_or_above_one():
    cand = _finish([_walk_leg(10.0)], bags=5, avoid_stairs=True, transfers=5)
    assert 0.0 <= cand["bag_friendliness"] <= 1.0
