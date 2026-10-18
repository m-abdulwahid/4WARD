import pytest

from backend import conditions, reliability


@pytest.fixture(autouse=True)
def _no_real_tomtom_calls(monkeypatch, request):
    """Some test module in this session imports backend.main, which loads .env and puts a real
    TOMTOM_API_KEY into the process environment. Without this, backend.conditions._observed would
    make a real network call to TomTom during unrelated tests. tests/test_tomtom.py exercises the
    real backend.tomtom module directly, so it is exempt."""
    if request.node.module.__name__.endswith("test_tomtom"):
        return
    monkeypatch.setattr(conditions, "_observed", lambda when: None)


@pytest.fixture(autouse=True)
def _no_real_tiger_data_calls(monkeypatch, request):
    """Same reasoning as above: without this, any test that builds a leg with a route_id and runs it
    through reliability.score_route()/_delay_factors() would make a real Tiger Data query. Tests that
    want to exercise the real lookup (or its failure handling) monkeypatch store.active_alerts
    themselves afterward, which overrides this default."""
    monkeypatch.setattr(reliability.store, "active_alerts", lambda route_id, **k: [])
