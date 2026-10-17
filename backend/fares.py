"""Deterministic fare, driving-cost and emissions math. No LLM involved."""
from .config import ASSUMPTIONS as A, EMISSIONS_G_PER_KM, DOWNTOWN_BBOX


def ttc_fare(rides_this_week: int = 0, presto: bool = True, transfers_within_window: bool = True) -> float:
    """Cost of ONE trip (door to door) on TTC, which may include several vehicles.

    Within the transfer window the whole trip is a single fare. Presto riders past the weekly
    cap ride free; cash riders never get capping.
    """
    base = A["ttc_fare_presto"]["value"] if presto else A["ttc_fare_cash"]["value"]
    if presto and rides_this_week >= A["ttc_weekly_cap_rides"]["value"]:
        return 0.0
    return round(base, 2)


def transit_trip_fare(vehicle_boardings: int, trip_minutes: float, presto: bool = True, rides_this_week: int = 0) -> float:
    """Fare for a transit itinerary. Boardings within the transfer window share one fare."""
    if vehicle_boardings <= 0:
        return 0.0
    window = A["ttc_transfer_window_min"]["value"]
    fares = 1 + int(max(0.0, trip_minutes) // window)  # a new fare after each window expiry
    return round(ttc_fare(rides_this_week, presto) * fares, 2)


def driving_cost(distance_km: float, ev: bool = False) -> float:
    if ev:
        per_km = A["ev_cost_per_km"]["value"]
    else:
        per_km = A["car_l_per_100km"]["value"] / 100.0 * A["fuel_price"]["value"]
    return round(distance_km * (per_km + A["car_wear_per_km"]["value"]), 2)


def in_downtown(lat: float, lon: float) -> bool:
    la0, la1, lo0, lo1 = DOWNTOWN_BBOX
    return la0 <= lat <= la1 and lo0 <= lon <= lo1


def parking_cost(lat: float, lon: float, stay_hours: float | None = None) -> float:
    hours = stay_hours if stay_hours is not None else A["default_stay_hours"]["value"]
    rate = A["parking_downtown_per_hr"]["value"] if in_downtown(lat, lon) else A["parking_other_per_hr"]["value"]
    return round(rate * max(1.0, hours), 2)


def parking_find_probability(lat: float, lon: float, hour: int) -> float:
    """Labeled ESTIMATE of finding a spot (no public live availability feed)."""
    p = 0.55 if in_downtown(lat, lon) else 0.85
    if 8 <= hour < 19:
        p -= 0.2
    return round(max(0.05, min(0.99, p)), 2)


def bikeshare_cost(minutes: float, ebike: bool = False) -> float:
    per_min = A["bikeshare_per_min"]["value"] + (A["bikeshare_ebike_per_min"]["value"] if ebike else 0.0)
    return round(A["bikeshare_unlock"]["value"] + per_min * max(0.0, minutes), 2)


def emissions_kg(mode: str, distance_km: float) -> float:
    return round(EMISSIONS_G_PER_KM.get(mode, 0) * distance_km / 1000.0, 3)


def rideshare_cost(distance_km: float, minutes: float) -> float:
    return round(A["rideshare_base"]["value"] + A["rideshare_per_km"]["value"] * distance_km
                 + A["rideshare_per_min"]["value"] * minutes, 2)
