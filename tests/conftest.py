import pytest

from backend import ontime, tomtom


@pytest.fixture(autouse=True)
def _no_live_traffic_in_tests(monkeypatch):
    """Tests never call TomTom (the real key may be in the environment) and never see each other's in-memory conditions."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    tomtom.reset()
    ontime._hist_cache.clear()
    ontime._alert_cache = None
    yield
    tomtom.reset()
