import pytest

from bots import critic, nlp, optimize
from bots.brain import FallbackBrain, RuleBrain, validate
from bots.profiles import modes_for


def cand(id, t, cost, co2, transfers=0, walk=300, bag=1.0, ok=True, deadline=True):
    return {"id": id, "available": True, "duration_min": t, "cost": cost, "co2_kg": co2, "transfers": transfers,
            "walk_m": walk, "bag_friendliness": bag, "meets_deadline": deadline, "title": id}


CANDS = [cand("walk", 200, 0, 0, walk=15000, bag=0.2), cand("rail", 46, 3.3, 0.6), cand("drive", 33, 12.4, 4.1),
         cand("bikeshare", 50, 8, 0, bag=0.2), cand("late_fast", 20, 40, 5, deadline=False)]


def test_each_objective_picks_its_own_optimum():
    c = {"bags": 0}
    assert optimize.top("cheap", CANDS, c)["id"] == "rail"      # walk is free but impractical (200 min)
    assert optimize.top("fastest", CANDS, c)["id"] == "drive"   # late_fast misses the deadline
    assert optimize.top("green", CANDS, c)["id"] == "bikeshare"


def test_bags_exclude_bag_hostile_routes():
    assert optimize.top("green", CANDS, {"bags": 2})["id"] == "rail"


def test_unavailable_and_no_routes():
    assert optimize.top("cheap", [{"id": "x", "available": False}], {}) is None


def test_efficient_is_pure_time_and_ignores_reliability():
    """Regression for the Phase 0 bug: Efficient used to add a reliability penalty and picked a 45 min drive over a
    40 min transit route that Best overall then beat it with. Efficient must take the faster route."""
    drive = cand("drive", 45, 14.0, 4.1)
    transit = {**cand("transit", 40, 3.3, 0.6), "reliability": {"reliability_pct": 50, "history_days": 14}}
    assert optimize.top("fastest", [drive, transit], {"bags": 0})["id"] == "transit"
    assert optimize.top("reliable", [drive, transit], {"bags": 0})["id"] == "transit"  # only one with an estimate


def test_reliable_bot_ranks_by_on_time_percentage_then_time_then_cost():
    def rel(c, pct):
        return {**c, "reliability": {"reliability_pct": pct, "history_days": 14}}

    solid_slow = rel(cand("solid_slow", 50, 3, 1), 95)
    flaky_fast = rel(cand("flaky_fast", 20, 3, 1), 60)
    no_data = cand("no_data", 10, 1, 0)  # fastest of all, but no on-time estimate: never ranks as a made-up 100%
    ranked = optimize.rank("reliable", [flaky_fast, no_data, solid_slow], {"bags": 0})
    assert [c["id"] for c in ranked] == ["solid_slow", "flaky_fast", "no_data"]
    tie_slow, tie_fast = rel(cand("tie_slow", 40, 3, 1), 90), rel(cand("tie_fast", 30, 9, 1), 90)
    assert optimize.top("reliable", [tie_slow, tie_fast], {"bags": 0})["id"] == "tie_fast"
    tie_cheap, tie_dear = rel(cand("tie_cheap", 30, 3, 1), 90), rel(cand("tie_dear", 30, 9, 1), 90)
    assert optimize.top("reliable", [tie_dear, tie_cheap], {"bags": 0})["id"] == "tie_cheap"


def test_no_best_overall_profile_remains():
    from bots.profiles import PROFILES

    assert list(PROFILES) == ["cheap", "fastest", "green", "reliable"]
    assert [p.name for p in PROFILES.values()] == ["Economic", "Efficient", "Environmental", "Reliable"]


def test_parse_rules():
    r = nlp.parse_rules("Get me from York University to Union Station by 5pm. I have 2 big bags, no Presto card, but I have my car.")
    assert (r["origin"], r["destination"]) == ("York University", "Union Station")
    c = r["constraints"]
    assert c["bags"] == 2 and c["no_presto"] and c["has_car"] and c["arrive_by"] == "17:00"
    r = nlp.parse_rules("I need to get to the CN Tower from Kipling Station, leaving at 7:30am with a suitcase and my e-bike")
    assert (r["origin"], r["destination"]) == ("Kipling Station", "CN Tower")
    assert r["constraints"]["depart_at"] == "07:30" and r["constraints"]["bags"] == 1 and r["constraints"]["has_ebike"]


def test_every_bot_searches_the_same_modes():
    sets = {k: modes_for(k, {"has_car": True}) for k in ("cheap", "fastest", "green", "reliable")}
    assert len({tuple(v) for v in sets.values()}) == 1
    assert {"drive", "rideshare", "park_ride", "transit", "walk"} <= set(sets["cheap"])
    assert modes_for("cheap", {"has_car": False}) == modes_for("fastest", {"has_car": False})


PAGE = {"controls": [{"testid": "origin-input", "value": ""}, {"testid": "compute-route"}], "state": "idle", "error": "",
        "selected": None, "confirmed": None, "candidates": []}


def test_validate_rejects_bad_actions():
    with pytest.raises(ValueError):
        validate({"action": "click", "testid": "nope"}, PAGE)
    with pytest.raises(ValueError):
        validate({"action": "type", "testid": "origin-input"}, PAGE)
    with pytest.raises(ValueError):
        validate({"action": "finish", "route_id": "ghost"}, PAGE)
    assert validate({"action": "finish", "route_id": "ghost"}, PAGE, {"ghost"}).route_id == "ghost"  # in the shared set
    assert validate({"action": "click", "testid": "compute-route"}, PAGE).action == "click"


def test_rule_brain_types_origin_first():
    user = {"origin": "Union Station", "destination": "CN Tower", "constraints": {}}
    a = RuleBrain("cheap", user).next_action(PAGE, [])
    assert a["action"] == "type" and a["testid"] == "origin-input" and a["text"] == "Union Station"


def _done_page(candidates, controls=()):
    return {**PAGE, "controls": PAGE["controls"] + list(controls), "state": "done", "candidates": candidates}


def test_rule_brain_selects_the_engines_pick_from_the_shared_set():
    """The page may show different candidates (live data moved); the pick still comes from the shared set."""
    user = {"origin": "a", "destination": "b", "constraints": {"bags": 0}}
    page = _done_page([cand("only_on_page", 10, 99, 9)], controls=[{"testid": "select-route-rail"}])
    a = RuleBrain("cheap", user, shared=CANDS).next_action(page, [])
    assert a["testid"] == "select-route-rail"


def test_rule_brain_finishes_with_engine_route_when_page_cannot_show_it():
    user = {"origin": "a", "destination": "b", "constraints": {"bags": 0}}
    a = RuleBrain("cheap", user, shared=CANDS).next_action(_done_page([cand("other", 10, 99, 9)]), [])
    assert a["action"] == "finish" and a["route_id"] == "rail"


def test_rule_brain_never_reruns_or_explores_alternate_settings():
    """The old explore-and-revert 'knob' changed departure times per bot and broke comparability."""
    user = {"origin": "a", "destination": "b", "constraints": {"bags": 0}}
    page = _done_page(CANDS, controls=[{"testid": "depart-at-input", "value": "08:00"}, {"testid": "select-route-rail"}])
    a = RuleBrain("cheap", user).next_action(page, [])
    assert a["testid"] == "select-route-rail"


def _setup_page(controls):
    return {**PAGE, "controls": [{"testid": "origin-input", "value": "A"}, {"testid": "destination-input", "value": "D"},
                                 {"testid": "add-stop"}, {"testid": "compute-route"}] + controls}


def test_rule_brain_builds_the_stop_list_in_order_before_computing():
    user = {"origin": "A", "destination": "D", "constraints": {}, "optimize_order": False,
            "stops": [{"name": "Eaton Centre", "type": "stop", "dwell_min": 20}, {"name": "High Park", "type": "via", "dwell_min": 0}]}
    brain = RuleBrain("cheap", user)
    assert brain.next_action(_setup_page([]), [])["testid"] == "add-stop"                       # no stop rows yet
    row = lambda i, name="", pressed="false", dwell="0": [
        {"testid": f"stop-{i}-input", "value": name}, {"testid": f"stop-{i}-dwell", "value": dwell}, {"testid": f"stop-{i}-type-stop", "pressed": pressed}]
    assert brain.next_action(_setup_page(row(0)), [])["testid"] == "add-stop"                    # rows first: 1 of 2 present
    two_empty = row(0) + [{"testid": "stop-1-input", "value": ""}]
    assert brain.next_action(_setup_page(two_empty), []) == {"action": "type", "testid": "stop-0-input", "text": "Eaton Centre", "reason": "enter stop 1"}
    named = row(0, "Eaton Centre") + [{"testid": "stop-1-input", "value": ""}]
    assert brain.next_action(_setup_page(named), [])["testid"] == "stop-0-type-stop"             # then its type
    typed = row(0, "Eaton Centre", "true") + [{"testid": "stop-1-input", "value": ""}]
    a = brain.next_action(_setup_page(typed), [])
    assert (a["testid"], a["text"]) == ("stop-0-dwell", "20")                                    # then its stay
    done0 = row(0, "Eaton Centre", "true", "20") + [{"testid": "stop-1-input", "value": ""}]
    assert brain.next_action(_setup_page(done0), [])["testid"] == "stop-1-input"                # then on to the second stop


def test_rule_brain_removes_surplus_stops_and_sets_the_reorder_flag():
    user = {"origin": "A", "destination": "D", "constraints": {}, "stops": [], "optimize_order": False}
    page = _setup_page([{"testid": "stop-0-input", "value": "x"}])
    assert RuleBrain("cheap", user).next_action(page, [])["testid"] == "stop-0-remove"
    user2 = {"origin": "A", "destination": "D", "constraints": {}, "optimize_order": True,
             "stops": [{"name": "B", "type": "stop", "dwell_min": 0}, {"name": "C", "type": "stop", "dwell_min": 0}]}
    rows = [{"testid": f"stop-{i}-input", "value": n} for i, n in enumerate("BC")] + \
           [{"testid": f"stop-{i}-type-stop", "pressed": "true"} for i in range(2)] + \
           [{"testid": f"stop-{i}-dwell", "value": "0"} for i in range(2)] + [{"testid": "toggle-optimize_order", "pressed": "false"}]
    assert RuleBrain("cheap", user2).next_action(_setup_page(rows), [])["testid"] == "toggle-optimize_order"


def test_rule_brain_sets_logistics_controls_exactly_as_the_request_says():
    user = {"origin": "A", "destination": "D", "origin_type": "pickup", "destination_type": "dropoff", "optimize_order": False,
            "constraints": {"start_in_car": True, "has_car": True, "car_needed_after": True, "plan_car_return": False},
            "stops": [{"name": "Union", "type": "stop", "dwell_min": 0, "window": ["18:00", "18:05"]}]}
    controls = [{"testid": "stop-0-input", "value": "Union"}, {"testid": "stop-0-type-stop", "pressed": "true"}, {"testid": "stop-0-dwell", "value": "0"},
                {"testid": "stop-0-window-start", "value": ""}, {"testid": "stop-0-window-end", "value": ""},
                {"testid": "origin-type-pickup", "pressed": "false"}, {"testid": "destination-type-dropoff", "pressed": "false"},
                {"testid": "destination-type-park", "pressed": "false"},
                {"testid": "toggle-start_in_car", "pressed": "false"}, {"testid": "toggle-car_needed_after", "pressed": "false"},
                {"testid": "toggle-plan_car_return", "pressed": "false"}]
    brain = RuleBrain("cheap", user)
    page = _setup_page(controls)
    seen = []
    for _ in range(12):
        a = brain.next_action(page, [])
        if a.get("testid") == "compute-route":
            break
        seen.append((a["action"], a["testid"], a.get("text")))
        for c in page["controls"]:  # apply the action to the fake page like the browser would
            if c["testid"] == a["testid"]:
                if a["action"] == "type":
                    c["value"] = a["text"]
                else:
                    c["pressed"] = "false" if c.get("pressed") == "true" else "true"
    assert ("type", "stop-0-window-start", "18:00") in seen and ("type", "stop-0-window-end", "18:05") in seen
    assert ("click", "origin-type-pickup", None) in seen and ("click", "destination-type-dropoff", None) in seen
    for flag in ("start_in_car", "car_needed_after"):
        assert ("click", f"toggle-{flag}", None) in seen
    assert not any(t == "toggle-plan_car_return" for _, t, _ in seen)          # not asked for: left off
    assert not any(t == "destination-type-park" for _, t, _ in seen)


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


def test_critic_explains_logistics_violations_in_plain_words():
    r = {"meets_deadline": True, "bag_friendliness": 1.0,
         "violates": ["highway_walk:Gardiner Expressway", "car_left_behind", "window:Eaton Centre"]}
    flags = critic.flags_for(r, {})
    assert flags == ["Walks or cycles along Gardiner Expressway, a controlled-access highway",
                     "Leaves your car behind, but you said you will need it later", "Misses the time you must be at Eaton Centre"]


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
            return [{"action": "type", "testid": "origin-input", "text": "Union Station", "reason": "go"},
                    {"action": "type", "testid": "origin-input", "text": "Union Station", "reason": "again"}]

    b.gemini = Good()
    assert (await b.next_action(PAGE, []))["brain"] == "gemini"
    assert (await b.next_action(PAGE, []))["reason"] == "again"          # second action comes from the queued plan
    assert Good.calls == 1                                                # one API call served two steps


@pytest.mark.asyncio
async def test_gemini_cannot_change_the_shared_mode_set(monkeypatch):
    """If Gemini switches a mode OFF 'because it is irrelevant for this objective', that action is rejected and the
    rule brain takes over, so all four bots still compute the same candidate set."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    user = {"origin": "a", "destination": "b", "constraints": {}}
    page = {**PAGE, "controls": PAGE["controls"] + [{"testid": "mode-toggle-park_ride", "pressed": "true"}]}
    b = FallbackBrain("cheap", user)

    class TurnsModeOff:
        async def next_actions(self, page, history):
            return [{"action": "click", "testid": "mode-toggle-park_ride", "reason": "not relevant for cheap"}]

    b.gemini = TurnsModeOff()
    a = await b.next_action(page, [])
    assert a["brain"] == "rule" and b.fallbacks == 1 and a["testid"] != "mode-toggle-park_ride"


@pytest.mark.asyncio
async def test_gemini_cannot_compute_before_the_shared_setup_is_complete(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    user = {"origin": "a", "destination": "b", "constraints": {}}
    page = {**PAGE, "controls": [{"testid": "origin-input", "value": "a"}, {"testid": "destination-input", "value": "b"},
                                 {"testid": "mode-toggle-rideshare", "pressed": "false"}, {"testid": "compute-route"}]}
    b = FallbackBrain("cheap", user)

    class ComputesEarly:
        async def next_actions(self, page, history):
            return [{"action": "click", "testid": "compute-route", "reason": "go"}]

    b.gemini = ComputesEarly()
    a = await b.next_action(page, [])
    # computing early is never executed: the missing mode is switched on first (the merged-in guard swaps the click for a
    # corrective one; the older fallback let the rule brain do it) -- either way the first action is the mode toggle
    assert a["action"] == "click" and a["testid"] == "mode-toggle-rideshare"


def test_mode_fix_returns_none_when_toggles_already_match():
    from bots.brain import _mode_fix

    page = {"controls": [{"testid": "mode-toggle-drive", "pressed": "true"}, {"testid": "mode-toggle-transit", "pressed": "true"},
                         {"testid": "mode-toggle-walk", "pressed": "false"}]}
    assert _mode_fix(page, ["drive", "transit"]) is None


def test_mode_fix_flags_a_missing_required_mode():
    from bots.brain import _mode_fix

    page = {"controls": [{"testid": "mode-toggle-drive", "pressed": "false"},  # required but left off
                         {"testid": "mode-toggle-park_ride", "pressed": "true"}, {"testid": "mode-toggle-transit", "pressed": "true"}]}
    fix = _mode_fix(page, ["drive", "park_ride", "transit"])
    assert fix == {"action": "click", "testid": "mode-toggle-drive",
                    "reason": "(corrected) enable drive: required for this objective's mode set"}


@pytest.mark.asyncio
async def test_fallback_brain_corrects_a_plan_that_skips_a_required_mode(monkeypatch):
    """The real bug this guards against: Gemini's free-form plan decided the 'fastest' objective didn't
    need plain driving, went straight to compute-route, and the deterministic picker then correctly chose
    the best of an incomplete pool -- a route that was not actually the fastest one available."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    user = {"origin": "a", "destination": "b", "constraints": {"has_car": True}}
    b = FallbackBrain("fastest", user)
    assert "drive" in b.want_modes  # modes_for("fastest", has_car=True) always includes it

    class SkipsDrive:
        async def next_actions(self, page, history):
            return [{"action": "click", "testid": "compute-route", "reason": "go"}]

    b.gemini = SkipsDrive()
    page = {"state": "idle", "controls": [
        {"testid": "mode-toggle-drive", "pressed": "false"},
        {"testid": "mode-toggle-park_ride", "pressed": "true"},
        {"testid": "mode-toggle-transit", "pressed": "true"},
        {"testid": "mode-toggle-bikeshare", "pressed": "true"},
        {"testid": "mode-toggle-walk", "pressed": "true"},
        {"testid": "compute-route"},
    ]}
    a = await b.next_action(page, [])
    assert a["brain"] == "gemini" and a["action"] == "click" and a["testid"] == "mode-toggle-drive"
    # once the live toggles actually match, the originally-planned compute-route click is let through
    page["controls"][0]["pressed"] = "true"
    a2 = await b.next_action(page, [])
    assert a2["testid"] == "compute-route"


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
