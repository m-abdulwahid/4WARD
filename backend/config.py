"""All cost / emissions / speed assumptions in one place. Every value is shown in the UI.

None of these are live data: they are explicit assumptions, flagged `verify` where the real
number should be confirmed before the demo (fares and prices change).
"""
import os


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
    "rideshare_base": {"value": 3.50, "unit": "CAD", "note": "Rideshare base + booking fee (ESTIMATE, no surge modeled)"},
    "rideshare_per_km": {"value": 1.45, "unit": "CAD/km", "note": "Rideshare distance rate (estimated)"},
    "rideshare_per_min": {"value": 0.40, "unit": "CAD/min", "note": "Rideshare time rate (estimated)"},
    "ttc_lot_parking_day": {"value": 6.00, "unit": "CAD", "note": "TTC commuter lot daily parking (estimated, verify)"},
    "own_ebike_cost_per_km": {"value": 0.02, "unit": "CAD/km", "note": "Electricity + battery wear (estimated)"},
    "go_fare_base": {"value": 3.90, "unit": "CAD", "note": "GO Transit PRESTO base fare (ESTIMATE -- real GO fares are distance-banded and change, verify)"},
    "go_fare_per_km": {"value": 0.22, "unit": "CAD/km", "note": "GO Transit PRESTO approx. rate beyond the base fare (ESTIMATE, verify)"},
    "go_lot_parking_day": {"value": 0.00, "unit": "CAD", "note": "Most GO lots are currently free for PRESTO riders (verify before a demo, this changes)"},
}

# grams CO2e per passenger-km (estimated, lifecycle-ish public figures)
EMISSIONS_G_PER_KM = {
    "walk": 0, "bike": 0, "bikeshare": 0, "ebike": 8, "bikeshare_ebike": 8,
    "subway": 30, "lrt_streetcar": 40, "bus": 90, "car": 192, "ev": 50,
    "go_train": 40,  # diesel commuter rail: worse than electric subway, far better than driving alone
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

# Door-to-door time includes the minutes a car trip spends finding a spot and walking from it. ESTIMATES
# (no public live parking data); downtown is slower than elsewhere. Used by planner.py for drive and park & ride.
PARKING_SEARCH_MIN = {"downtown": 8.0, "other": 3.0}
PARKING_WALK_MIN = {"downtown": 4.0, "other": 2.0}
PARK_RIDE_LOT_TO_PLATFORM_MIN = 4.0

# TomTom live traffic (backend/tomtom.py). TomTom's terms on storing results are NOT confirmed to allow it on the free plan, so
# nothing from TomTom is ever written to a database, file or log: the latest values live in memory for TOMTOM_TTL_S and are
# then gone. TOMTOM_STORE exists so storage can be switched on later if TomTom's terms or support allow it; today it is
# false and no storage code path exists (turning it on only makes the app say so, see tomtom.store_requested()).
TOMTOM_STORE = os.environ.get("TOMTOM_STORE", "false").strip().lower() in ("1", "true", "yes")
TOMTOM_TTL_S = 300
TOMTOM_FREE_REQUESTS_PER_MONTH = 20000  # Traffic Flow Segment Data free tier on docs.tomtom.com/pricing ("Free 20K monthly")
TOMTOM_OLDER_FREE_PER_DAY = 2500        # what an older TomTom pricing page said; both are respected, the smaller daily share wins
TOMTOM_BUDGET_FRACTION = 0.8            # never use more than this share of the free allowance
TOMTOM_DAILY_REQUEST_BUDGET = int(TOMTOM_BUDGET_FRACTION * min(TOMTOM_FREE_REQUESTS_PER_MONTH / 30, TOMTOM_OLDER_FREE_PER_DAY))
# How much slower than usual a trip time can turn out (documented DEFAULTS, ESTIMATED: not measured). Used by backend/ontime.py.
ONTIME_CV = {"road_live": 0.15, "road_typical": 0.25, "walk": 0.08, "bike": 0.12, "transit_no_history_s": 90.0}

# Time accuracy (backend/schedule.py). All values are settings you can change; they are shown as ESTIMATES in the UI.
BOARDING_BUFFER_S = 60     # arrive at a stop this long before the vehicle you plan to catch (first boarding)
TRANSFER_BUFFER_S = 60     # same margin for every boarding after a transfer, on top of the transfer walk
REALTIME_HORIZON_MIN = 30  # a departure within this long from now uses OBSERVED conditions; later ones use typical ones
REALTIME_MAX_AGE_S = 300   # an observed delay older than this is not used
LEAVE_BY_SEARCH_MIN = 300  # how far back from the deadline to look for the latest workable departure

# "Meet someone at X at 6": arriving early waits; arriving up to this many minutes late is still on time (ASSUMPTION, verify).
MEET_WINDOW_GRACE_MIN = 5

# Shared eligibility rule for ranking (bots/optimize.py): a route slower than max(MIN_CAP, FACTOR x the fastest
# route) is not a practical option for ANY bot (stops "free" 12 hour walks winning on cost).
PRACTICAL_MIN_CAP_MIN = 90
PRACTICAL_FACTOR = 2.5

DOWNTOWN_BBOX =(43.630, 43.675, -79.420, -79.360)  # lat_min, lat_max, lon_min, lon_max

# Toronto + Durham, York, Peel, Halton regions (the conventional GTA, not GO's full reach into
# Hamilton/Niagara/Waterloo/Simcoe -- chosen to comfortably include Oshawa, Milton, Newmarket and
# Georgetown while excluding Hamilton-area and Barrie-area GO stations just south/north of those).
GTA_BBOX = (43.30, 44.10, -80.05, -78.70)
