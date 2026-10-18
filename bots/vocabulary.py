"""The trip vocabulary: what each phrase means, and worked examples.

One source of truth for (1) the glossary and few-shot examples shown to Gemini when it parses a trip, and (2) tests that
hold the rule-based parser (bots/nlp.py) to exactly the same examples, so both parsers agree on the logistics.

A "stop" here is one of: stop, via, pickup, dropoff, errand, park, alight. Types describe how you ARRIVE or LEAVE:
  pickup   a ride begins here (someone else picks YOU up)
  dropoff  a ride ends here (someone else drops YOU off); you are on foot afterwards, their vehicle does not continue
  park     you arrive driving your own car and it stays here
  alight   you get off a bus/train/subway/streetcar here
  via      pass through without stopping (no dwell, same mode on both sides)
  stop     a waypoint you stay at for a while (swing by, stop at)
  errand   you do something for someone else here (pick up a friend, drop off a parcel); a stop with dwell
"""

GLOSSARY = [
    ("start from X / starting at X", "X is the origin."),
    ("end at X / finish at X", "X is the destination."),
    ("I'm starting in my car / I'm in my car", "traveler_state is driving at the start and the car is with you: has_car and start_in_car are true; the first leg is driven."),
    ("dropped off at X / drop me off at X", "Someone else drives you to X. You are on foot at X afterwards, their vehicle does not continue with you, you pay no parking. type dropoff."),
    ("picked up at X / pick me up at X", "A ride (someone else's vehicle) begins at X. type pickup."),
    ("park at X / park my car at X", "You arrive in your own car and it stays parked at X. has_car is true. type park."),
    ("stop at X / swing by X (for N minutes)", "A waypoint at X with a stay of N minutes (0 if not said). type stop."),
    ("via X / through X", "Pass through X with no stay and no change of mode. type via."),
    ("meet someone at X at 6pm", "A fixed time: you must be at X at 18:00. Arriving early means waiting. type stop with a time window."),
    ("get off the bus/train at X", "You alight from transit at X. type alight."),
    ("get off at X (no vehicle named)", "AMBIGUOUS between the bus and the car. Do not guess: ask which."),
    ("meet ... at 6 (no am/pm)", "AMBIGUOUS between 6:00 AM and 6:00 PM. Do not guess: ask which."),
    ("I will need my car later / afterwards", "The car must end the trip with you, or the way back to it must be planned: car_needed_after is true; ask whether to plan the return to the car."),
]

# (prompt, expected). `expected` lists only what the example demonstrates; tests compare exactly those fields.
FEW_SHOTS: list[tuple[str, dict]] = [
    ("Start from Union Station and get to High Park.",
     {"origin": "Union Station", "destination": "High Park", "stops": []}),
    ("I'm starting in my car at Kipling Station and need to reach the CN Tower by 6pm.",
     {"origin": "Kipling Station", "destination": "CN Tower", "constraints": {"start_in_car": True, "has_car": True, "arrive_by": "18:00"}}),
    ("From Union Station to Pearson Airport. I'll be dropped off at Pearson Airport.",
     {"origin": "Union Station", "destination": "Pearson Airport", "destination_type": "dropoff", "stops": []}),
    ("Get me to High Park from Kipling Station. A friend picks me up at Kipling Station.",
     {"origin": "Kipling Station", "destination": "High Park", "origin_type": "pickup", "stops": []}),
    ("From Union Station to High Park, park at Kipling Station on the way.",
     {"origin": "Union Station", "destination": "High Park", "stops": [{"name": "Kipling Station", "type": "park", "dwell_min": 0.0}],
      "constraints": {"has_car": True}}),
    ("From Union Station to High Park via Kensington Market.",
     {"origin": "Union Station", "destination": "High Park", "stops": [{"name": "Kensington Market", "type": "via", "dwell_min": 0.0}]}),
    ("From Union Station to the CN Tower, swing by the Eaton Centre for 20 minutes.",
     {"origin": "Union Station", "destination": "CN Tower", "stops": [{"name": "Eaton Centre", "type": "stop", "dwell_min": 20.0}]}),
    ("Start from Kipling Station, meet a friend at Union Station at 6pm, and end at High Park.",
     {"origin": "Kipling Station", "destination": "High Park",
      "stops": [{"name": "Union Station", "type": "stop", "dwell_min": 0.0, "window": ["18:00", "18:05"]}], "clarifications": []}),
    ("From Union Station to High Park, meet a friend at the Eaton Centre at 6.",
     {"clarifications": ["meet-time"], "stops": [{"name": "Eaton Centre", "type": "stop", "dwell_min": 0.0}]}),
    ("From Union Station to High Park, get off at Bloor.",
     {"clarifications": ["get-off"], "stops": []}),
    ("From Union Station to High Park, get off the subway at Bloor.",
     {"stops": [{"name": "Bloor", "type": "alight", "dwell_min": 0.0, "vehicle": "subway"}], "clarifications": []}),
    ("From Oshawa to King and Bay with my car. I will need my car later.",
     {"origin": "Oshawa", "destination": "King and Bay", "constraints": {"has_car": True, "car_needed_after": True}, "clarifications": ["car-return"]}),
    ("From Oshawa to King and Bay with my car. I will need my car later. Plan the return to my car.",
     {"constraints": {"has_car": True, "car_needed_after": True, "plan_car_return": True}, "clarifications": []}),
    ("I'm in my car on the Don Valley Parkway and need to get to Union Station.",
     {"origin": "Don Valley Parkway", "destination": "Union Station", "constraints": {"start_in_car": True, "has_car": True}}),
]


def gemini_vocabulary() -> str:
    """Text appended to Gemini's parsing instructions."""
    import json

    g = "\n".join(f'- "{p}": {m}' for p, m in GLOSSARY)
    shots = "\n".join(f"Trip: {p}\nJSON: {json.dumps(e)}" for p, e in FEW_SHOTS)
    return f"Vocabulary (follow it exactly):\n{g}\n\nExamples (they show only the fields they demonstrate):\n{shots}"
