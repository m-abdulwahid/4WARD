"""The four bot personalities: what each optimizes. Ranking lives in bots/optimize.py.

Route numbers always come from the backend; this module only names the bots and the objective text Gemini sees.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    key: str
    name: str
    color: str
    objective: str


PROFILES: dict[str, Profile] = {
    "cheap": Profile("cheap", "Economic", "#16a34a",
                     "Lowest total out-of-pocket cost: fares (Presto vs cash), parking, gas, bike share fees."),
    "fastest": Profile("fastest", "Efficient", "#dc2626",
                       "Lowest door-to-door time, including walking, waits, transfers and parking."),
    "green": Profile("green", "Environmental", "#0d9488", "Lowest CO2e emissions."),
    "reliable": Profile("reliable", "Reliable", "#7c3aed",
                        "Highest probability of arriving by the deadline, using stored delay history. Ties go to the "
                        "faster route, then the cheaper one."),
}

ALL_MODES = ["walk", "bike", "ebike", "bikeshare", "drive", "rideshare", "transit", "park_ride"]


def modes_for(profile_key: str, c: dict) -> list[str]:
    """Modes the planner UI has switched ON. Identical for every bot: all four search ONE shared candidate set, so
    no objective can be missing a route another objective sees. Modes needing a car/bike the user lacks come back
    from the engine as unavailable with a reason, so turning them on is always safe."""
    return list(ALL_MODES)
