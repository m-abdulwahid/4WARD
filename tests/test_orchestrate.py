import pytest

from bots import orchestrate


def route(id, dur):
    return {"route": {"id": id, "duration_min": dur}}


@pytest.mark.asyncio
async def test_no_rerun_when_nothing_duplicates(monkeypatch):
    results = [route("a", 10), route("b", 20), route("c", 30), route("d", 40)]
    called = False

    async def fake_run_one(*a, **k):
        nonlocal called
        called = True
        return route("should-not-run", 0)

    monkeypatch.setattr(orchestrate, "_run_one_local", fake_run_one)
    out = await orchestrate._resolve_diversity(["cheap", "fastest", "green", "best"], results, {}, "", None, 1, 1, False, "local")
    assert out == results
    assert not called


@pytest.mark.asyncio
async def test_duplicate_reruns_with_only_the_one_duplicated_id_avoided(monkeypatch):
    results = [route("transit_rail", 22), route("rideshare", 20), route("transit_rail", 22), route("transit_rail", 22)]
    seen_avoid_ids = []

    async def fake_run_one(key, user, base_url, emit, max_steps, timeout_s, stream_frames, avoid_ids=None):
        seen_avoid_ids.append(set(avoid_ids or set()))
        return route("walk", 25)  # a real, reasonably comparable alternative

    monkeypatch.setattr(orchestrate, "_run_one_local", fake_run_one)
    out = await orchestrate._resolve_diversity(["cheap", "fastest", "green", "best"], results, {}, "", None, 1, 1, False, "local")
    assert out[0]["route"]["id"] == "transit_rail"  # cheap claimed it first, never reran
    assert out[1]["route"]["id"] == "rideshare"      # fastest was never a duplicate
    assert out[2]["route"]["id"] == "walk"           # green duplicated cheap's pick -> reran, "walk" is free, accepted
    # best also duplicated cheap's original pick, but by the time it's processed "walk" is already
    # claimed by green -- its rerun would just be a *different* duplicate, so it keeps its honest original
    assert out[3]["route"]["id"] == "transit_rail"
    # every rerun avoided only the single id that bot actually duplicated, never the whole claimed set
    assert seen_avoid_ids == [{"transit_rail"}, {"transit_rail"}]


@pytest.mark.asyncio
async def test_keeps_original_when_rerun_is_still_a_duplicate(monkeypatch):
    results = [route("transit_rail", 22), route("rideshare", 20), route("transit_rail", 22)]

    async def fake_run_one(key, user, base_url, emit, max_steps, timeout_s, stream_frames, avoid_ids=None):
        return route("rideshare", 20)  # collides with fastest's already-claimed pick

    monkeypatch.setattr(orchestrate, "_run_one_local", fake_run_one)
    out = await orchestrate._resolve_diversity(["cheap", "fastest", "green"], results, {}, "", None, 1, 1, False, "local")
    # green's rerun landed on an id someone else already has -> keep the original honest duplicate
    assert out[2]["route"]["id"] == "transit_rail"


@pytest.mark.asyncio
async def test_keeps_original_when_rerun_is_dramatically_worse(monkeypatch):
    results = [route("transit_rail", 22), route("transit_rail", 22)]

    async def fake_run_one(key, user, base_url, emit, max_steps, timeout_s, stream_frames, avoid_ids=None):
        return route("walk", 200)  # technically different, but absurdly worse than the 22 min duplicate

    monkeypatch.setattr(orchestrate, "_run_one_local", fake_run_one)
    out = await orchestrate._resolve_diversity(["cheap", "best"], results, {}, "", None, 1, 1, False, "local")
    assert out[1]["route"]["id"] == "transit_rail"
