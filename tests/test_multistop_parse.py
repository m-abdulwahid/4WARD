import pytest

from bots import nlp


def parsed(text):
    r = nlp.parse_rules(text)
    return r["origin"], [(s["name"], s["type"], s["dwell_min"]) for s in r["stops"]], r["destination"], r["optimize_order"]


@pytest.mark.parametrize("text,expected", [
    ("From Union Station to the CN Tower with a stop at High Park for 30 minutes",
     ("Union Station", [("High Park", "stop", 30.0)], "CN Tower", False)),
    ("Get from York University to Union Station, swing by Eaton Centre for 20 minutes and pick up a friend at Kipling Station",
     ("York University", [("Eaton Centre", "stop", 20.0), ("Kipling Station", "errand", 0.0)], "Union Station", False)),   # YOU pick a friend up: an errand stop (a ride that picks YOU up is "pickup")
    ("Union Station to Kensington Market via High Park",
     ("Union Station", [("High Park", "via", 0.0)], "Kensington Market", False)),
    ("From Kipling Station to Finch Station, then Union Station, then CN Tower",
     ("Kipling Station", [("Finch Station", "stop", 0.0), ("Union Station", "stop", 0.0)], "CN Tower", False)),
    ("Kipling Station to Finch Station to Union Station to CN Tower",
     ("Kipling Station", [("Finch Station", "stop", 0.0), ("Union Station", "stop", 0.0)], "CN Tower", False)),
    ("From Union Station to High Park, stop at Eaton Centre for an hour, any order is fine",
     ("Union Station", [("Eaton Centre", "stop", 60.0)], "High Park", True)),
    ("Get me from York University to Union Station by 5pm. I have 2 big bags, no Presto card, but I have my car.",
     ("York University", [], "Union Station", False)),
])
def test_stops_in_the_order_typed(text, expected):
    assert parsed(text) == expected


def test_constraints_survive_stop_extraction():
    r = nlp.parse_rules("From Union Station to High Park with a stop at Eaton Centre for 20 minutes, I have 2 bags and my car, arrive by 6pm")
    assert r["constraints"]["bags"] == 2 and r["constraints"]["has_car"] and r["constraints"]["arrive_by"] == "18:00"
    assert r["stops"][0]["dwell_min"] == 20


def test_a_plain_trip_has_no_stops_and_does_not_reorder():
    r = nlp.parse_rules("Kensington Market to Distillery District please")
    assert r["stops"] == [] and r["optimize_order"] is False
