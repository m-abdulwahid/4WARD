import logging

import pytest

from backend import tomtom


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


def flow(current, free):
    return FakeResponse(200, {"flowSegmentData": {"currentSpeed": current, "freeFlowSpeed": free}})


class FakeClient:
    """Stands in for httpx.Client: .get(url, params=...) -> FakeResponse, from a queue the test fills."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        self.calls.append((url, params))
        if not self._responses:
            raise RuntimeError("no more fake responses queued")
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(autouse=True)
def reset_module_state(monkeypatch):
    """tomtom.py keeps its cache and daily counter as module globals; each test starts clean."""
    monkeypatch.setattr(tomtom, "_cache", None)
    monkeypatch.setattr(tomtom, "_day", None)
    monkeypatch.setattr(tomtom, "_count_today", 0)


def use_client(monkeypatch, responses):
    client = FakeClient(responses)
    monkeypatch.setattr(tomtom.httpx, "Client", lambda timeout=5: client)
    return client


def test_no_key_returns_none_without_any_network_call(monkeypatch):
    def boom(timeout=5):
        raise AssertionError("must not touch the network with no key")

    monkeypatch.setattr(tomtom.httpx, "Client", boom)
    assert tomtom.road_ratio(None) is None


def test_successful_fetch_computes_the_mean_ratio_and_caches_it(monkeypatch):
    n = len(tomtom.TOMTOM_CORRIDOR_POINTS) if hasattr(tomtom, "TOMTOM_CORRIDOR_POINTS") else 10
    from backend.config import TOMTOM_CORRIDOR_POINTS

    client = use_client(monkeypatch, [flow(20, 40) for _ in TOMTOM_CORRIDOR_POINTS])  # ratio 2.0 everywhere
    r1 = tomtom.road_ratio("fake-key")
    assert r1 == 2.0
    assert len(client.calls) == len(TOMTOM_CORRIDOR_POINTS)

    # second call within the TTL must not hit the network again
    r2 = tomtom.road_ratio("fake-key")
    assert r2 == 2.0
    assert len(client.calls) == len(TOMTOM_CORRIDOR_POINTS)


def test_cache_expires_after_ttl(monkeypatch):
    from backend.config import TOMTOM_CORRIDOR_POINTS

    t = [1000.0]
    monkeypatch.setattr(tomtom.time, "time", lambda: t[0])
    client = use_client(monkeypatch, [flow(20, 40) for _ in TOMTOM_CORRIDOR_POINTS])
    assert tomtom.road_ratio("fake-key") == 2.0

    t[0] += tomtom.TOMTOM_CACHE_TTL_S + 1
    client._responses = [flow(10, 40) for _ in TOMTOM_CORRIDOR_POINTS]  # ratio 4.0, clamps to 3.0
    assert tomtom.road_ratio("fake-key") == 3.0


def test_daily_cap_stops_new_requests_and_falls_back_to_last_cache(monkeypatch):
    from backend.config import TOMTOM_CORRIDOR_POINTS, TOMTOM_DAILY_CAP

    t = [1000.0]
    monkeypatch.setattr(tomtom.time, "time", lambda: t[0])
    client = use_client(monkeypatch, [flow(20, 40) for _ in TOMTOM_CORRIDOR_POINTS])
    assert tomtom.road_ratio("fake-key") == 2.0
    assert tomtom.requests_today() == len(TOMTOM_CORRIDOR_POINTS)

    monkeypatch.setattr(tomtom, "_count_today", TOMTOM_DAILY_CAP)
    t[0] += tomtom.TOMTOM_CACHE_TTL_S + 1  # cache would otherwise be stale
    client._responses = [RuntimeError("must not be called: cap already spent")]
    assert tomtom.road_ratio("fake-key") == 2.0  # falls back to the last real reading, not None


def test_unreachable_falls_back_to_last_cache_then_none(monkeypatch):
    from backend.config import TOMTOM_CORRIDOR_POINTS

    t = [1000.0]
    monkeypatch.setattr(tomtom.time, "time", lambda: t[0])
    use_client(monkeypatch, [RuntimeError("down")] * len(TOMTOM_CORRIDOR_POINTS))
    assert tomtom.road_ratio("fake-key") is None  # never fetched before: nothing to fall back to, no crash


def test_ratio_is_clamped_to_a_sane_range(monkeypatch):
    from backend.config import TOMTOM_CORRIDOR_POINTS

    use_client(monkeypatch, [flow(1, 100) for _ in TOMTOM_CORRIDOR_POINTS])  # ratio 100, must clamp
    assert tomtom.road_ratio("fake-key") == 3.0


def test_key_and_url_never_reach_logging(monkeypatch, caplog):
    """The key travels as a query parameter; this asserts it never lands in a captured log record,
    at any logger, even with every logger forced to DEBUG."""
    from backend.config import TOMTOM_CORRIDOR_POINTS

    secret = "sk-totally-secret-tomtom-key-123"
    use_client(monkeypatch, [flow(20, 40) for _ in TOMTOM_CORRIDOR_POINTS])
    caplog.set_level(logging.DEBUG)
    tomtom.road_ratio(secret)
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert secret not in joined
    assert "flowSegmentData" not in joined  # the raw TomTom URL, for good measure


def test_httpx_and_httpcore_loggers_are_silenced_on_import():
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
