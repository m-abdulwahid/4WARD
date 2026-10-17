import pytest

from backend import fares


@pytest.mark.parametrize("boardings,minutes,presto,week,expected", [
    (1, 30, True, 0, 3.30),
    (3, 90, True, 0, 3.30),         # transfers inside the 2 h window share one fare
    (2, 60, False, 0, 3.30),        # cash/token fare
    (2, 130, True, 0, 6.60),        # window expired: second fare
    (1, 30, True, 12, 0.0),         # Presto weekly cap reached
    (1, 30, True, 11, 3.30),
    (1, 30, False, 20, 3.30),       # cash riders get no capping
    (0, 0, True, 0, 0.0),           # no transit leg, no fare
])
def test_transit_fare(boardings, minutes, presto, week, expected):
    assert fares.transit_trip_fare(boardings, minutes, presto, week) == expected


def test_driving_cost_gas_and_ev():
    assert fares.driving_cost(20) == 4.97          # 20 km * (9L/100km * $1.65 + $0.10 wear)
    assert fares.driving_cost(20, ev=True) == 2.8  # 20 km * ($0.04 + $0.10)


def test_parking_downtown_vs_outside():
    assert fares.parking_cost(43.6453, -79.3806) == 7.00          # Union, default 2 h at $3.50
    assert fares.parking_cost(43.7735, -79.5019, stay_hours=3) == 6.00  # York U, 3 h at $2.00
    assert fares.parking_find_probability(43.6453, -79.3806, 9) < fares.parking_find_probability(43.7735, -79.5019, 9)


def test_bikeshare_and_emissions():
    assert fares.bikeshare_cost(20) == 5.0
    assert fares.bikeshare_cost(20, ebike=True) == 9.0
    assert fares.emissions_kg("car", 10) == 1.92
    assert fares.emissions_kg("walk", 10) == 0
