import pytest

from backend import road_restrictions as rr


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    monkeypatch.setattr(rr, "_cache", None)


def test_disabled_by_default_returns_empty_with_no_network_call(monkeypatch):
    monkeypatch.delenv("ROAD_RESTRICTIONS_ENABLED", raising=False)

    def boom(timeout=20):
        raise AssertionError("must not touch the network when disabled")

    monkeypatch.setattr(rr.httpx, "Client", boom)
    assert rr.active_closures() == []


@pytest.mark.parametrize("value", ["false", "0", "no", "", "TRUE_ISH"])
def test_only_the_literal_string_true_enables_it(monkeypatch, value):
    monkeypatch.setenv("ROAD_RESTRICTIONS_ENABLED", value)
    assert rr.enabled() is False


def test_enabled_with_exact_true_string(monkeypatch):
    monkeypatch.setenv("ROAD_RESTRICTIONS_ENABLED", "true")
    assert rr.enabled() is True


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


class FakeClient:
    def __init__(self, body):
        self.body = body
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        self.calls += 1
        return FakeResponse(self.body)


def test_fetch_normalizes_and_filters_expired(monkeypatch):
    monkeypatch.setenv("ROAD_RESTRICTIONS_ENABLED", "true")
    body = {
        "Closure": [
            {"id": "1", "road": "King St W", "name": "King St W closure", "latitude": "43.65", "longitude": "-79.39",
             "description": "Construction", "directionsAffected": "BOTH_DIRECTIONS", "type": "ROAD_CLOSED",
             "maxImpact": "High", "currImpact": "High", "startTime": "1700000000000", "endTime": "9999999999000", "expired": "0"},
            {"id": "2", "road": "Old St", "name": "Old closure", "latitude": "43.1", "longitude": "-79.1",
             "description": "Done", "directionsAffected": "NB", "type": "ROAD_CLOSED",
             "maxImpact": "Low", "currImpact": "Low", "startTime": "1000000000000", "endTime": "1000000001000", "expired": "0"},
            {"id": "3", "road": "No coords", "latitude": None, "longitude": None, "expired": "0"},
        ]
    }
    client = FakeClient(body)
    monkeypatch.setattr(rr.httpx, "Client", lambda timeout=20: client)
    out = rr.active_closures()
    assert len(out) == 1  # #2 is in the past (end_ts long gone), #3 has no coordinates
    assert out[0]["id"] == "1" and out[0]["road"] == "King St W" and out[0]["lat"] == 43.65


def test_cache_avoids_a_second_fetch_within_the_ttl(monkeypatch):
    monkeypatch.setenv("ROAD_RESTRICTIONS_ENABLED", "true")
    client = FakeClient({"Closure": []})
    monkeypatch.setattr(rr.httpx, "Client", lambda timeout=20: client)
    rr.active_closures()
    rr.active_closures()
    assert client.calls == 1


def test_unreachable_falls_back_to_empty_without_crashing(monkeypatch):
    monkeypatch.setenv("ROAD_RESTRICTIONS_ENABLED", "true")

    class Boom:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            raise RuntimeError("down")

    monkeypatch.setattr(rr.httpx, "Client", lambda timeout=20: Boom())
    assert rr.active_closures() == []
