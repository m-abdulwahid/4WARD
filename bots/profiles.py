"""The four bot personalities: what each optimizes, which modes it explores, and how it ranks routes.

Route numbers always come from the backend; this module only decides what to try and how to rank.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    key: str
    name: str
    color: str
    objective: str


PROFILES: dict[str, Profile] = {
    "cheap": Profile("cheap", "Cheapest", "#16a34a",
                     "Minimize total out-of-pocket cost: fares (Presto vs cash), parking, gas, bike share fees. Free options "
                     "are great but must still be practical (not absurdly slow) and respect bags/deadline."),
    "fastest": Profile("fastest", "Fastest", "#dc2626",
                       "Minimize door-to-door time. Consider every mode the user can actually use, including driving and "
                       "park-and-ride if they have a car, and weigh traffic at the departure hour."),
    "green": Profile("green", "Greenest", "#0d9488",
                     "Minimize CO2e emissions: prefer walking, biking, e-bikes and transit over driving, but keep the trip "
                     "practical (not several times slower than alternatives) and bag-friendly."),
    "best": Profile("best", "Best overall", "#7c3aed",
                    "Best balanced trip: weigh time, cost, emissions, transfers, walking distance and how easy it is with "
                    "bags. A route that misses the deadline is never the best."),
}


def modes_for(profile_key: str, c: dict) -> list[str]:
    """Modes this profile turns ON in the planner, given the user's constraints dict."""
    has_car, has_bike, has_ebike = c.get("has_car"), c.get("has_bike"), c.get("has_ebike")
    base = ["transit"]
    if profile_key == "cheap":
        modes = ["walk", "bikeshare", "transit"] + (["bike"] if has_bike else []) + (["ebike"] if has_ebike else [])
    elif profile_key == "fastest":
        modes = ["transit", "bikeshare", "walk"] + (["drive", "park_ride"] if has_car else ["rideshare"]) + (["ebike"] if has_ebike else []) + (["bike"] if has_bike else [])
    elif profile_key == "green":
        modes = ["walk", "bikeshare", "transit"] + (["bike"] if has_bike else []) + (["ebike"] if has_ebike else [])
    else:  # best
        modes = ["walk", "bikeshare", "transit"] + (["drive", "park_ride"] if has_car else ["rideshare"]) + (["bike"] if has_bike else []) + (["ebike"] if has_ebike else [])
    seen = []
    for m in modes + base:
        if m not in seen:
            seen.append(m)
    return seen
