"""All cost / emissions / speed assumptions in one place. Every value is shown in the UI.

None of these are live data: they are explicit assumptions, flagged `verify` where the real
number should be confirmed before the demo (fares and prices change).
"""

ASSUMPTIONS = {
    "ttc_fare_presto": {"value": 3.30, "unit": "CAD", "note": "TTC adult single fare with Presto (verify)"},
    "ttc_fare_cash": {"value": 3.30, "unit": "CAD", "note": "TTC adult cash/token single fare (verify)"},
    "ttc_transfer_window_min": {"value": 120, "unit": "min", "note": "Free transfers within 2 h of tapping"},
    "ttc_weekly_cap_rides": {"value": 12, "unit": "rides", "note": "Presto fare capping: rides after the 12th in a Mon-Sun week are free (verify)"},
    "fuel_price": {"value": 1.65, "unit": "CAD/L", "note": "Regular gas, Toronto average (estimated)"},
    "car_l_per_100km": {"value": 9.0, "unit": "L/100km", "note": "Typical city driving"},
    "ev_cost_per_km": {"value": 0.04, "unit": "CAD/km", "note": "Home-charging EV (estimated)"},
    "car_wear_per_km": {"value": 0.10, "unit": "CAD/km", "note": "Tires/maintenance share (estimated)"},
    "parking_downtown_per_hr": {"value": 3.50, "unit": "CAD/h", "note": "Green P downtown estimate (no public live rate feed)"},
    "parking_other_per_hr": {"value": 2.00, "unit": "CAD/h", "note": "Green P outside the core (estimated)"},
    "default_stay_hours": {"value": 2.0, "unit": "h", "note": "Parking duration when user gives none"},
    "bikeshare_unlock": {"value": 1.00, "unit": "CAD", "note": "Bike Share Toronto single-trip unlock (verify)"},
    "bikeshare_per_min": {"value": 0.20, "unit": "CAD/min", "note": "Casual per-minute rate (verify)"},
    "bikeshare_ebike_per_min": {"value": 0.20, "unit": "CAD/min", "note": "E-bike surcharge per minute (verify)"},
    "ttc_lot_parking_day": {"value": 6.00, "unit": "CAD", "note": "TTC commuter lot daily parking (estimated, verify)"},
    "own_ebike_cost_per_km": {"value": 0.02, "unit": "CAD/km", "note": "Electricity + battery wear (estimated)"},
}

# grams CO2e per passenger-km (estimated, lifecycle-ish public figures)
EMISSIONS_G_PER_KM = {
    "walk": 0, "bike": 0, "bikeshare": 0, "ebike": 8, "bikeshare_ebike": 8,
    "subway": 30, "lrt_streetcar": 40, "bus": 90, "car": 192, "ev": 50,
}

# average speeds km/h (estimated); foot/bike/car legs come from OSM routing durations when online
SPEEDS_KMH = {"walk": 4.8, "bike": 15.0, "ebike": 22.0, "car": 28.0, "bus": 17.0}

# time-of-day traffic multiplier on car/bus travel times (estimated typical Toronto profile)
def traffic_multiplier(hour: int) -> float:
    if 7 <= hour < 10:
        return 1.55
    if 16 <= hour < 19:
        return 1.7
    if 10 <= hour < 16:
        return 1.2
    if 19 <= hour < 22:
        return 1.1
    return 1.0

DOWNTOWN_BBOX = (43.630, 43.675, -79.420, -79.360)  # lat_min, lat_max, lon_min, lon_max

TORONTO_BBOX = (43.58, 43.86, -79.64, -79.12)
