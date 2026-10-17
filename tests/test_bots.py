import pytest

from bots import nlp, scoring
from bots.brain import FallbackBrain, RuleBrain, validate
from bots.profiles import modes_for


def cand(id, t, cost, co2, transfers=0, walk=300, bag=1.0, ok=True, deadline=True):
    return {"id": id, "available": True, "duration_min": t, "cost": cost, "co2_kg": co2, "transfers": transfers,
            "walk_m": walk, "bag_friendliness": bag, "meets_deadline": deadline, "title": id}


CANDS = [cand("walk", 200, 0, 0, walk=15000, bag=0.2), cand("rail", 46, 3.3, 0.6), cand("drive", 33, 12.4, 4.1),
         cand("bikeshare", 50, 8, 0, bag=0.2), cand("late_fast", 20, 40, 5, deadline=False)]


def test_each_objective_picks_a_different_winner():
    c = {"bags": 0}
    assert scoring.best("cheap", CANDS, c)["id"] == "rail"      # walk is free but impractical (200 min)
    assert scoring.best("fastest", CANDS, c)["id"] == "drive"   # late_fast misses the deadline
    assert scoring.best("green", CANDS, c)["id"] == "bikeshare"


def test_bags_exclude_bag_hostile_routes():
    assert scoring.best("green", CANDS, {"bags": 2})["id"] == "rail"


def test_unavailable_and_no_routes():
    assert scoring.best("cheap", [{"id": "x", "available": False}], {}) is None


def test_parse_rules():
    r = nlp.parse_rules("Get me from York University to Union Station by 5pm. I have 2 big bags, no Presto card, but I have my car.")
    assert (r["origin"], r["destination"]) == ("York University", "Union Station")
    c = r["constraints"]
    assert c["bags"] == 2 and c["no_presto"] and c["has_car"] and c["arrive_by"] == "17:00"
    r = nlp.parse_rules("I need to get to the CN Tower from Kipling Station, leaving at 7:30am with a suitcase and my e-bike")
    assert (r["origin"], r["destination"]) == ("Kipling Station", "CN Tower")
    assert r["constraints"]["depart_at"] == "07:30" and r["constraints"]["bags"] == 1 and r["constraints"]["has_ebike"]


def test_modes_follow_constraints():
    assert "drive" not in modes_for("fastest", {"has_car": False})
    assert "rideshare" in modes_for("fastest", {"has_car": False})
    assert "drive" in modes_for("fastest", {"has_car": True})
    assert "drive" not in modes_for("cheap", {"has_car": True})


PAGE = {"controls": [{"testid": "origin-input", "value": ""}, {"testid": "compute-route"}], "state": "idle", "error": "",
        "selected": None, "confirmed": None, "candidates": []}


def test_validate_rejects_bad_actions():
    with pytest.raises(ValueError):
        validate({"action": "click", "testid": "nope"}, PAGE)
    with pytest.raises(ValueError):
        validate({"action": "type", "testid": "origin-input"}, PAGE)
    with pytest.raises(ValueError):
        validate({"action": "finish", "route_id": "ghost"}, PAGE)
    assert validate({"action": "click", "testid": "compute-route"}, PAGE).action == "click"


def test_rule_brain_types_origin_first():
    user = {"origin": "Union Station", "destination": "CN Tower", "constraints": {}}
    a = RuleBrain("cheap", user).next_action(PAGE, [])
    assert a["action"] == "type" and a["testid"] == "origin-input" and a["text"] == "Union Station"


@pytest.mark.asyncio
async def test_fallback_to_rule_brain_when_gemini_breaks(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    user = {"origin": "Union Station", "destination": "CN Tower", "constraints": {}}
    b = FallbackBrain("cheap", user)

    class Broken:
        async def next_action(self, page, history):
            raise RuntimeError("quota exceeded")

    b.gemini = Broken()
    a = await b.next_action(PAGE, [])
    assert a["brain"] == "rule" and b.fallbacks == 1

    class Garbage:
        async def next_action(self, page, history):
            return {"action": "click", "testid": "does-not-exist"}

    b.gemini = Garbage()
    assert (await b.next_action(PAGE, []))["brain"] == "rule"

    class Good:
        async def next_action(self, page, history):
            return {"action": "click", "testid": "compute-route", "reason": "go"}

    b.gemini = Good()
    assert (await b.next_action(PAGE, []))["brain"] == "gemini"


@pytest.mark.parametrize("text,o,d", [
    ("I'm at Kipling Station and need to reach the CN Tower by 6pm with a suitcase. No Presto card.", "Kipling Station", "CN Tower"),
    ("Kensington Market to Distillery District please", "Kensington Market", "Distillery District"),
    ("How do I do high park, ending at union station", "High Park", "Union Station"),
])
def test_parse_prompt_variants(text, o, d):
    r = nlp.parse_rules(text)
    assert (r["origin"].lower(), r["destination"].lower()) == (o.lower(), d.lower())
