import pytest

from bots import critic, nlp, scoring
from bots.brain import FallbackBrain, RuleBrain, _knob_variant, _shift_hhmm, validate
from bots.profiles import modes_for


def cand(id, t, cost, co2, transfers=0, walk=300, bag=1.0, ok=True, deadline=True):
    return {"id": id, "available": True, "duration_min": t, "cost": cost, "co2_kg": co2, "transfers": transfers,
            "walk_m": walk, "bag_friendliness": bag, "meets_deadline": deadline, "title": id}


CANDS = [cand("walk", 200, 0, 0, walk=15000, bag=0.2), cand("rail", 46, 3.3, 0.6), cand("drive", 33, 12.4, 4.1),
         cand("bikeshare", 50, 8, 0, bag=0.2), cand("late_fast", 20, 40, 5, deadline=False)]


def test_each_objective_picks_a_different_winner():
    c = {"bags": 0}
    assert scoring.top("cheap", CANDS, c)["id"] == "rail"      # walk is free but impractical (200 min)
    assert scoring.top("fastest", CANDS, c)["id"] == "drive"   # late_fast misses the deadline
    assert scoring.top("green", CANDS, c)["id"] == "bikeshare"


def test_bags_exclude_bag_hostile_routes():
    assert scoring.top("green", CANDS, {"bags": 2})["id"] == "rail"


def test_unavailable_and_no_routes():
    assert scoring.top("cheap", [{"id": "x", "available": False}], {}) is None


def test_reliability_breaks_a_near_tie_toward_the_more_reliable_option():
    """Two near-identical-time options for 'fastest': the one Tiger Data shows as flaky loses,
    but only once there's enough history behind that signal to trust it."""
    fast_unreliable = {**cand("fast_flaky", 20, 5, 1), "reliability": {"reliability_pct": 40, "history_days": 14}}
    fast_reliable = {**cand("fast_solid", 21, 5, 1), "reliability": {"reliability_pct": 95, "history_days": 14}}
    assert scoring.top("fastest", [fast_unreliable, fast_reliable], {"bags": 0})["id"] == "fast_solid"


def test_reliability_barely_matters_with_thin_history():
    """One matching historical day is not enough signal to flip a close call -- the raw time/cost
    difference should still decide it, exactly like before reliability existed."""
    fast_flaky_thin_history = {**cand("fast_flaky", 20, 5, 1), "reliability": {"reliability_pct": 0, "history_days": 1}}
    slightly_slower = {**cand("slightly_slower", 21, 5, 1)}
    assert scoring.top("fastest", [fast_flaky_thin_history, slightly_slower], {"bags": 0})["id"] == "fast_flaky"


def test_reliable_bot_ranks_by_on_time_percentage_then_time_then_cost():
    def rel(c, pct):
        return {**c, "reliability": {"reliability_pct": pct, "history_days": 14}}

    solid_slow = rel(cand("solid_slow", 50, 3, 1), 95)
    flaky_fast = rel(cand("flaky_fast", 20, 3, 1), 60)
    no_data = cand("no_data", 10, 1, 0)  # fastest of all, but no on-time estimate: never ranks as a made-up 100%
    ranked = scoring.rank("reliable", [flaky_fast, no_data, solid_slow], {"bags": 0})
    assert [c["id"] for c in ranked] == ["solid_slow", "flaky_fast", "no_data"]
    tie_slow, tie_fast = rel(cand("tie_slow", 40, 3, 1), 90), rel(cand("tie_fast", 30, 9, 1), 90)
    assert scoring.top("reliable", [tie_slow, tie_fast], {"bags": 0})["id"] == "tie_fast"
    tie_cheap, tie_dear = rel(cand("tie_cheap", 30, 3, 1), 90), rel(cand("tie_dear", 30, 9, 1), 90)
    assert scoring.top("reliable", [tie_dear, tie_cheap], {"bags": 0})["id"] == "tie_cheap"


def test_no_best_overall_profile_remains():
    from bots.profiles import PROFILES

    assert list(PROFILES) == ["cheap", "fastest", "green", "reliable"]
    assert [p.name for p in PROFILES.values()] == ["Economic", "Efficient", "Environmental", "Reliable"]


def test_reliability_penalty_pure_function():
    assert scoring._reliability_penalty({}) == 0.0
    assert scoring._reliability_penalty({"reliability": {"reliability_pct": 100, "history_days": 30}}) == 0.0
    assert scoring._reliability_penalty({"reliability": {"reliability_pct": 50, "history_days": 7}}) == pytest.approx(0.25)
    assert scoring._reliability_penalty({"reliability": {"reliability_pct": 50, "history_days": 28}}) == pytest.approx(0.5)


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


def test_rule_brain_respects_avoid_ids():
    """Diversity rule: a bot told to avoid an id picks the next-best candidate instead."""
    user = {"origin": "a", "destination": "b", "constraints": {"bags": 0}}
    page = {**PAGE, "state": "done", "selected": None, "confirmed": None, "candidates": CANDS}
    top = RuleBrain("cheap", user).next_action(page, [])
    assert top["testid"] == "select-route-rail"
    avoided = RuleBrain("cheap", user, avoid_ids={"rail"}).next_action(page, [])
    assert avoided["testid"] != "select-route-rail"


def test_rule_brain_avoid_ids_falls_back_when_pool_empties():
    """If every candidate is avoided, don't strand the bot with no choice at all."""
    user = {"origin": "a", "destination": "b", "constraints": {"bags": 0}}
    page = {**PAGE, "state": "done", "selected": None, "confirmed": None, "candidates": CANDS}
    all_ids = {c["id"] for c in CANDS}
    a = RuleBrain("cheap", user, avoid_ids=all_ids).next_action(page, [])
    assert a["action"] == "click" and a["testid"].startswith("select-route-")


def test_shift_hhmm():
    assert _shift_hhmm("08:00", -15) == "07:45"
    assert _shift_hhmm("00:05", -15) == "23:50"
    assert _shift_hhmm("not-a-time", -15) is None


def test_knob_variant_prefers_lot_over_depart_time():
    page = {**PAGE, "controls": PAGE["controls"]
            + [{"testid": "depart-at-input", "value": "08:00"},
               {"testid": "park-ride-lot-auto", "pressed": "true"}, {"testid": "park-ride-lot-finch", "pressed": "false"}]}
    assert _knob_variant(page) == ("click", "park-ride-lot-finch", None, "try a different park & ride lot", "park-ride-lot-auto")


def test_knob_variant_none_when_no_knob_controls_present():
    assert _knob_variant(PAGE) is None


VARIANT_BETTER = [cand("rail_fast", 40, 1.0, 0.5)]
VARIANT_WORSE = [cand("rail_slow", 90, 10.0, 2.0)]


def test_rule_brain_explores_depart_time_and_keeps_better_variant():
    """A real, bounded iteration: try leaving 15 min earlier, recompute, and keep it since it won."""
    user = {"origin": "a", "destination": "b", "constraints": {"bags": 0}}
    page = {**PAGE, "controls": PAGE["controls"] + [{"testid": "depart-at-input", "value": "08:00"}],
            "state": "done", "selected": None, "confirmed": None, "candidates": CANDS}
    rb = RuleBrain("cheap", user)
    a1 = rb.next_action(page, [])
    assert a1 == {"action": "type", "testid": "depart-at-input", "text": "07:45", "reason": "try leaving 15 min earlier"}
    a2 = rb.next_action(page, [])
    assert a2["action"] == "click" and a2["testid"] == "compute-route"
    variant_page = {**page, "candidates": VARIANT_BETTER}
    a3 = rb.next_action(variant_page, [])
    assert a3["testid"] == "select-route-rail_fast"


def test_rule_brain_explores_depart_time_and_reverts_when_worse():
    """If the alternative is worse for this objective, undo it instead of settling for it."""
    user = {"origin": "a", "destination": "b", "constraints": {"bags": 0}}
    page = {**PAGE, "controls": PAGE["controls"] + [{"testid": "depart-at-input", "value": "08:00"}],
            "state": "done", "selected": None, "confirmed": None, "candidates": CANDS}
    rb = RuleBrain("cheap", user)
    rb.next_action(page, [])  # types the alternate departure time
    rb.next_action(page, [])  # clicks compute-route
    variant_page = {**page, "candidates": VARIANT_WORSE}
    a3 = rb.next_action(variant_page, [])
    assert a3 == {"action": "type", "testid": "depart-at-input", "text": "08:00",
                  "reason": "revert: the original setting scored better for this objective"}
    a4 = rb.next_action(variant_page, [])
    assert a4["action"] == "click" and a4["testid"] == "compute-route"
    reverted_page = {**page, "candidates": CANDS}
    a5 = rb.next_action(reverted_page, [])
    assert a5["testid"] == "select-route-rail"


def test_critic_flags_deadline_and_walk_and_bags():
    assert critic.flags_for(None, {}) == []
    missed = {"meets_deadline": False, "arrive": "17:42", "violates": [], "bag_friendliness": 1.0}
    assert "17:42" in critic.flags_for(missed, {"arrive_by": "17:00"})[0]
    over_walk = {"meets_deadline": True, "violates": ["max_walk"], "walk_m": 4000, "bag_friendliness": 1.0}
    assert "4000" in critic.flags_for(over_walk, {})[0]
    bag_hostile = {"meets_deadline": True, "violates": [], "bag_friendliness": 0.1}
    assert critic.flags_for(bag_hostile, {"bags": 2})
    assert critic.flags_for(bag_hostile, {"bags": 0}) == []
    clean = {"meets_deadline": True, "violates": [], "bag_friendliness": 1.0}
    assert critic.flags_for(clean, {"bags": 2, "arrive_by": "17:00"}) == []


def test_critic_apply_prepends_notes_and_sets_violations():
    results = [{"bot": "cheap", "route": {"meets_deadline": False, "arrive": "18:00", "violates": [],
                                          "bag_friendliness": 1.0, "notes": ["Presto $3.30"]}},
               {"bot": "fastest", "route": None}]
    critic.apply(results, {"arrive_by": "17:00"})
    assert results[0]["critic_violations"]
    assert results[0]["route"]["notes"][0].startswith("⚠")
    assert results[0]["route"]["notes"][-1] == "Presto $3.30"
    assert results[1]["critic_violations"] == []


@pytest.mark.asyncio
async def test_fallback_to_rule_brain_when_gemini_breaks(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    user = {"origin": "Union Station", "destination": "CN Tower", "constraints": {}}
    b = FallbackBrain("cheap", user)

    class Broken:
        async def next_actions(self, page, history):
            raise RuntimeError("quota exceeded")

    b.gemini = Broken()
    a = await b.next_action(PAGE, [])
    assert a["brain"] == "rule" and b.fallbacks == 1

    class Garbage:
        async def next_actions(self, page, history):
            return [{"action": "click", "testid": "does-not-exist"}]

    b.gemini = Garbage()
    assert (await b.next_action(PAGE, []))["brain"] == "rule"

    class Good:
        calls = 0

        async def next_actions(self, page, history):
            Good.calls += 1
            return [{"action": "click", "testid": "compute-route", "reason": "go"},
                    {"action": "type", "testid": "origin-input", "text": "x"}]

    b.gemini = Good()
    assert (await b.next_action(PAGE, []))["brain"] == "gemini"
    assert (await b.next_action(PAGE, []))["testid"] == "origin-input"   # second action comes from the queued plan
    assert Good.calls == 1                                                # one API call served two steps


def test_gemini_brain_builds_offline(monkeypatch):
    """Constructing the real brain and its schema must not need the network."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    from google.genai import types

    from bots.brain import GeminiBrain, Plan

    GeminiBrain("fastest", {"origin": "a", "destination": "b", "constraints": {}})
    types.GenerateContentConfig(response_mime_type="application/json", response_schema=Plan, temperature=0)


@pytest.mark.parametrize("text,o,d", [
    ("I'm at Kipling Station and need to reach the CN Tower by 6pm with a suitcase. No Presto card.", "Kipling Station", "CN Tower"),
    ("Kensington Market to Distillery District please", "Kensington Market", "Distillery District"),
    ("How do I do high park, ending at union station", "High Park", "Union Station"),
])
def test_parse_prompt_variants(text, o, d):
    r = nlp.parse_rules(text)
    assert (r["origin"].lower(), r["destination"].lower()) == (o.lower(), d.lower())
