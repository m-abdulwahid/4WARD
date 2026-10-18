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
        return {"origin": {"lat": 43.7, "lon": -79.4}, "destination": {"lat": 43.65, "lon": -79.38}}, None

    async def fake_shared(*_):
        return shared

    async def fake_local(keys, user, base_url, emit, max_steps, timeout_s, stream_frames, shared_set):
        assert shared_set is shared  # every bot receives the SAME candidate set object
        return [{"bot": k, "name": PROFILES[k].name, "status": "DONE" if bot_routes.get(k) else "FAILED",
                 "route": bot_routes.get(k), "error": None if bot_routes.get(k) else "boom", "elapsed_s": 1} for k in keys]

    async def no_warm(_):
        pass

    monkeypatch.setattr(orchestrate, "parse_prompt", parse)
    monkeypatch.setattr(orchestrate, "_locate", locate)
    monkeypatch.setattr(orchestrate, "shared_candidates", fake_shared)
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
