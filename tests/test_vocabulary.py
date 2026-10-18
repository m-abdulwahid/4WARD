"""The vocabulary glossary and few-shot examples (bots/vocabulary.py) are shown to Gemini AND enforced on the rule
parser here, so the two parsers agree on what "dropped off", "park at", "via", "meet at 6" mean."""
import pytest

from bots import nlp, vocabulary


def test_there_are_at_least_eight_worked_examples_and_a_glossary_for_every_phrase():
    assert len(vocabulary.FEW_SHOTS) >= 8
    phrases = " ".join(p for p, _ in vocabulary.GLOSSARY).lower()
    for needle in ("start from", "starting in my car", "dropped off", "picked up", "park at", "stop at", "via", "meet", "get off"):
        assert needle in phrases
    text = vocabulary.gemini_vocabulary()
    assert all(prompt in text for prompt, _ in vocabulary.FEW_SHOTS)


@pytest.mark.parametrize("prompt,expected", vocabulary.FEW_SHOTS, ids=[p[:50] for p, _ in vocabulary.FEW_SHOTS])
def test_rule_parser_follows_every_worked_example(prompt, expected):
    r = nlp.parse_rules(prompt)
    for key, want in expected.items():
        if key == "constraints":
            for k, v in want.items():
                assert r["constraints"].get(k) == v, f"{k}: {r['constraints'].get(k)!r} != {v!r}"
        elif key == "clarifications":
            assert [q["id"] for q in r["clarifications"]] == want
        else:
            assert r[key] == want, f"{key}: {r[key]!r} != {want!r}"


def answer(prompt: str, clar_id: str, option: int) -> str:
    """What the UI does when the user picks an option: replace a snippet or append a sentence."""
    q = next(q for q in nlp.parse_rules(prompt)["clarifications"] if q["id"] == clar_id)
    o = q["options"][option]
    return prompt.replace(*o["replace"]) if "replace" in o else f"{prompt} {o['append']}"


def test_a_time_without_am_or_pm_is_asked_about_not_guessed():
    p = "From Union Station to High Park, meet a friend at the Eaton Centre at 6."
    q = nlp.parse_rules(p)["clarifications"][0]
    assert q["id"] == "meet-time" and [o["label"] for o in q["options"]] == ["6 AM", "6 PM"]
    assert "window" not in nlp.parse_rules(p)["stops"][0]
    pm = nlp.parse_rules(answer(p, "meet-time", 1))
    assert pm["clarifications"] == [] and pm["stops"][0]["window"] == ["18:00", "18:05"]
    am = nlp.parse_rules(answer(p, "meet-time", 0))
    assert am["stops"][0]["window"] == ["06:00", "06:05"]


def test_get_off_at_is_asked_about_and_both_answers_are_understood():
    p = "From Union Station to High Park, get off at Bloor."
    q = nlp.parse_rules(p)["clarifications"][0]
    assert q["id"] == "get-off" and "bus" in q["question"] and "car" in q["question"]
    bus = nlp.parse_rules(answer(p, "get-off", 0))
    assert bus["clarifications"] == [] and bus["stops"] == [{"name": "Bloor", "type": "alight", "dwell_min": 0.0, "vehicle": "transit"}]
    car = nlp.parse_rules(answer(p, "get-off", 1))
    assert car["clarifications"] == [] and car["stops"] == [{"name": "Bloor", "type": "dropoff", "dwell_min": 0.0}]


def test_needing_the_car_later_asks_whether_to_plan_the_way_back():
    p = "From Oshawa to King and Bay with my car. I will need my car later."
    q = nlp.parse_rules(p)["clarifications"][0]
    assert q["id"] == "car-return" and [o["label"][:3] for o in q["options"]] == ["Yes", "No,"]
    yes = nlp.parse_rules(answer(p, "car-return", 0))
    assert yes["clarifications"] == [] and yes["constraints"]["plan_car_return"] and yes["constraints"]["car_needed_after"]
    no = nlp.parse_rules(answer(p, "car-return", 1))
    assert no["clarifications"] == [] and not no["constraints"]["plan_car_return"] and not no["constraints"]["car_needed_after"]


def test_a_later_stop_that_needs_the_car_also_asks():
    p = "From Union Station to High Park, stop at Eaton Centre, then park at Kipling Station."
    r = nlp.parse_rules(p)
    assert [s["type"] for s in r["stops"]] == ["stop", "park"] or r["destination_type"] == "park"
    q = next(q for q in r["clarifications"] if q["id"] == "car-return")
    assert "Kipling Station" in q["question"]


def test_a_car_trip_with_nothing_left_behind_asks_nothing():
    assert nlp.parse_rules("From Union Station to High Park with my car.")["clarifications"] == []
    assert nlp.parse_rules("From Union Station to High Park.")["clarifications"] == []


def test_starting_in_the_car_is_not_mistaken_for_an_origin_called_my_car():
    r = nlp.parse_rules("I'm starting in my car at Kipling Station and need to reach the CN Tower.")
    assert r["origin"] == "Kipling Station" and r["constraints"]["start_in_car"]


def test_a_dropped_off_stop_is_a_logistics_stop_and_a_picked_up_stop_too():
    r = nlp.parse_rules("From Kipling Station to High Park, I get dropped off at Eaton Centre and picked up at Eaton Centre.")
    assert [(s["name"], s["type"]) for s in r["stops"]] == [("Eaton Centre", "dropoff"), ("Eaton Centre", "pickup")]


def test_the_long_oshawa_example_is_read_correctly():
    p = ("I'm in Oshawa with my car and I need to be at King and Bay in downtown Toronto by 8:30 AM. I don't want to drive downtown "
         "or pay for parking there, and everything I need is walkable from Union Station. I'd like to keep my extra stuff in the car "
         "for later, and I don't want to pay the full GO fare from Oshawa. I have two bags and no Presto card. Find me the best way to get there.")
    r = nlp.parse_rules(p)
    assert (r["origin"], r["destination"]) == ("Oshawa", "King and Bay")
    c = r["constraints"]
    assert c["has_car"] and c["bags"] == 2 and c["no_presto"] and c["arrive_by"] == "08:30" and c["car_needed_after"]
    assert [q["id"] for q in r["clarifications"]] == ["car-return"]
