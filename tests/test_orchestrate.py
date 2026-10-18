import pytest

from bots import orchestrate
from bots.profiles import PROFILES


def route(id, dur, cost=1.0, co2=0.5):
    return {"id": id, "title": id, "available": True, "duration_min": dur, "cost": cost, "co2_kg": co2, "transfers": 0, "walk_m": 100}


async def _run(monkeypatch, shared, bot_routes, keys=None):
    """run_all with the parser, geocoder, engine and browsers faked: the bots just report `bot_routes[key]`."""
    async def parse(_):
        return {"origin": "A", "destination": "B", "constraints": {"bags": 0}, "parser": "rules"}

    async def locate(_):
        return {"origin": {"lat": 43.7, "lon": -79.4}, "destination": {"lat": 43.65, "lon": -79.38}, "stops": []}, None

    async def fake_shared(*_):
        return {"candidates": shared, "points": []}

    async def fake_local(keys, user, base_url, emit, max_steps, timeout_s, stream_frames, shared_set):
        assert shared_set is shared  # every bot receives the SAME candidate set object
        return [{"bot": k, "name": PROFILES[k].name, "status": "DONE" if bot_routes.get(k) else "FAILED",
                 "route": bot_routes.get(k), "error": None if bot_routes.get(k) else "boom", "elapsed_s": 1} for k in keys]

    async def no_warm(_):
        pass

    monkeypatch.setattr(orchestrate, "parse_prompt", parse)
    monkeypatch.setattr(orchestrate, "_locate", locate)
    monkeypatch.setattr(orchestrate, "shared_plan", fake_shared)
    monkeypatch.setattr(orchestrate, "_run_local", fake_local)
    monkeypatch.setattr(orchestrate, "_warm_up", no_warm)
    monkeypatch.setenv("BROWSER_BACKEND", "local")
    events = []
    out = await orchestrate.run_all("x", events.append, keys=keys, stream_frames=False)
    return out, events


@pytest.mark.asyncio
async def test_all_four_bots_always_reported_and_never_rerun_when_they_agree(monkeypatch):
    only = route("transit", 30)
    out, events = await _run(monkeypatch, [only], {k: only for k in PROFILES})
    assert [r["bot"] for r in out["results"]] == list(PROFILES)           # four bots listed even though one route won
    assert out["route_tags"] == {"transit": list(PROFILES)}               # one route, tagged with every objective it wins
    assert out["invariant_violations"] == []
    assert not any(e.get("type") == "diversity_rerun" for e in events)


@pytest.mark.asyncio
async def test_engine_bug_is_flagged_when_a_bot_returns_a_non_optimal_route(monkeypatch):
    fast, cheap = route("fast", 10, cost=9.0), route("cheap", 50, cost=1.0)
    wrong = {"cheap": cheap, "fastest": cheap, "green": cheap, "reliable": cheap}  # Efficient got the slow route
    out, events = await _run(monkeypatch, [fast, cheap], wrong)
    assert any(v.startswith("Efficient") for v in out["invariant_violations"])
    assert any(e.get("type") == "invariant_violation" for e in events)


@pytest.mark.asyncio
async def test_failed_bot_does_not_hide_the_others_or_trip_the_check(monkeypatch):
    fast, cheap = route("fast", 10, cost=9.0, co2=3), route("cheap", 50, cost=1.0, co2=0.2)
    got = {"cheap": cheap, "fastest": fast, "green": cheap}  # reliable has no route (failed)
    out, _ = await _run(monkeypatch, [fast, cheap], got)
    assert len(out["results"]) == 4 and out["results"][3]["status"] == "FAILED"
    assert out["invariant_violations"] == []


@pytest.mark.asyncio
async def test_ambiguous_words_stop_the_run_and_ask_instead_of_starting_bots(monkeypatch):
    async def parse(_):
        return {"origin": "A", "destination": "B", "stops": [], "constraints": {}, "parser": "rules",
                "clarifications": [{"id": "get-off", "question": "Bus or car?", "options": []}]}

    async def boom(*_a, **_k):
        raise AssertionError("no bot may start while a question is open")

    monkeypatch.setattr(orchestrate, "parse_prompt", parse)
    monkeypatch.setattr(orchestrate, "_run_local", boom)
    monkeypatch.setattr(orchestrate, "_locate", boom)
    out = await orchestrate.run_all("x", None, stream_frames=False)
    assert out["clarify"][0]["id"] == "get-off" and out["results"] == [] and "error" not in out


@pytest.mark.asyncio
async def test_an_impossible_trip_is_reported_with_the_reason(monkeypatch):
    from backend import trip

    async def impossible(*_a):
        raise trip.TripImpossible("You said to park somewhere, but you have not said you have a car")

    async def parse(_):
        return {"origin": "A", "destination": "B", "constraints": {}, "parser": "rules"}

    async def locate(_):
        return {"origin": {"lat": 43.7, "lon": -79.4}, "destination": {"lat": 43.65, "lon": -79.38}, "stops": []}, None

    monkeypatch.setattr(orchestrate, "parse_prompt", parse)
    monkeypatch.setattr(orchestrate, "_locate", locate)
    monkeypatch.setattr(orchestrate, "shared_plan", impossible)
    out = await orchestrate.run_all("x", None, stream_frames=False)
    assert "have a car" in out["error"] and out["results"] == []


@pytest.mark.asyncio
async def test_logistics_words_reach_the_planner_as_typed_endpoints(monkeypatch):
    seen = {}

    async def fake_plan_trip(o, stops, d, constraints, optimize):
        seen.update(o=o, d=d, c=constraints)
        return {"candidates": [], "points": []}

    monkeypatch.setattr(orchestrate.trip, "plan_trip", fake_plan_trip)
    user = {"origin": "A", "destination": "B", "origin_type": "pickup", "destination_type": "dropoff", "stops": [],
            "constraints": {"start_in_car": True, "car_needed_after": True, "plan_car_return": True}}
    places = {"origin": {"lat": 1, "lon": 2}, "destination": {"lat": 3, "lon": 4}, "stops": []}
    await orchestrate.shared_plan(user, {"origin": {**places["origin"], "type": "pickup"}, "destination": {**places["destination"], "type": "dropoff"}, "stops": []})
    assert seen["o"]["type"] == "pickup" and seen["d"]["type"] == "dropoff"
    assert seen["c"].start_in_car and seen["c"].has_car and seen["c"].plan_car_return and seen["c"].car_needed_after

@pytest.mark.asyncio
async def test_locate_keeps_the_pickup_dropoff_types_and_the_stop_details(monkeypatch):
    async def geocode(q):
        return {"name": q, "lat": 43.65, "lon": -79.38}

    monkeypatch.setattr(orchestrate.geo, "geocode", geocode)
    user = {"origin": "A", "destination": "B", "origin_type": "pickup", "destination_type": "dropoff",
            "stops": [{"name": "S", "type": "park", "dwell_min": 5.0}]}
    places, err = await orchestrate._locate(user)
    assert err is None and places["origin"]["type"] == "pickup" and places["destination"]["type"] == "dropoff"
    assert places["stops"] == [{"name": "S", "type": "park", "dwell_min": 5.0, "lat": 43.65, "lon": -79.38}]
    pts = orchestrate.trip_points(user, places)
    assert [(p["label"], p["type"]) for p in pts] == [("A", "pickup"), ("B", "park"), ("C", "dropoff")]