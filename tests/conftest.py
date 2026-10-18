import pytest

from backend import conditions


@pytest.fixture(autouse=True)
def _no_real_tomtom_calls(monkeypatch, request):
    """Some test module in this session imports backend.main, which loads .env and puts a real
    TOMTOM_API_KEY into the process environment. Without this, backend.conditions._observed would
    make a real network call to TomTom during unrelated tests. tests/test_tomtom.py exercises the
    real backend.tomtom module directly, so it is exempt."""
    if request.node.module.__name__.endswith("test_tomtom"):
        return
    monkeypatch.setattr(conditions, "_observed", lambda when: None)
