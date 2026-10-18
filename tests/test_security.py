"""Security and hosting rule (CLAUDE.md): a shared bot token header, CORS limits, basic rate
limiting. No auth gate at all when BOT_TOKEN is unset (the local-dev default)."""
import asyncio

import pytest

from backend import security


class FakeURL:
    def __init__(self, path, query=""):
        self.path = path
        self._query = query


class FakeRequest:
    def __init__(self, path, headers=None, query="", client_host="1.2.3.4"):
        self.url = FakeURL(path, query)
        self.headers = headers or {}
        self.query_params = dict(p.split("=", 1) for p in query.split("&") if "=" in p) if query else {}
        self.client = type("C", (), {"host": client_host})()


async def _ok(request):
    return "ok"


def test_no_token_configured_means_no_auth_gate(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    assert security.bot_token() == ""
    assert security.requires_bot_token("/run") is False
    assert security.requires_bot_token("/ws/run/abc") is False


@pytest.mark.parametrize("path", ["/health", "/assumptions", "/suggest", "/geocode",
                                  "/bikeshare", "/route", "/insights/bikes", "/live", "/docs"])
def test_public_paths_never_require_a_token(monkeypatch, path):
    monkeypatch.setenv("BOT_TOKEN", "secret")
    assert security.requires_bot_token(path) is False


def test_run_and_websocket_require_a_token_when_configured(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "secret")
    assert security.requires_bot_token("/run") is True
    assert security.requires_bot_token("/ws/run/abc") is True


def test_bot_token_middleware_rejects_missing_or_wrong_token(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "secret")
    mw = security.BotTokenMiddleware(app=None)
    req = FakeRequest("/run", headers={})
    resp = asyncio.run(mw.dispatch(req, _ok))
    assert resp.status_code == 401

    req = FakeRequest("/run", headers={"x-bot-token": "wrong"})
    resp = asyncio.run(mw.dispatch(req, _ok))
    assert resp.status_code == 401


def test_bot_token_middleware_accepts_the_right_token(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "secret")
    mw = security.BotTokenMiddleware(app=None)
    req = FakeRequest("/run", headers={"x-bot-token": "secret"})
    assert asyncio.run(mw.dispatch(req, _ok)) == "ok"


def test_bot_token_middleware_lets_public_paths_through_with_no_header(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "secret")
    mw = security.BotTokenMiddleware(app=None)
    req = FakeRequest("/health", headers={})
    assert asyncio.run(mw.dispatch(req, _ok)) == "ok"


def test_websocket_token_falls_back_to_query_param(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "secret")
    mw = security.BotTokenMiddleware(app=None)
    req = FakeRequest("/ws/run/abc", headers={}, query="bot_token=secret")
    assert asyncio.run(mw.dispatch(req, _ok)) == "ok"

    req = FakeRequest("/ws/run/abc", headers={}, query="bot_token=wrong")
    resp = asyncio.run(mw.dispatch(req, _ok))
    assert resp.status_code == 401


def test_assert_bot_token_header_raises_only_when_required_and_wrong(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setenv("BOT_TOKEN", "secret")
    with pytest.raises(HTTPException):
        security.assert_bot_token_header(FakeRequest("/run", headers={}))
    security.assert_bot_token_header(FakeRequest("/run", headers={"x-bot-token": "secret"}))  # no raise
    security.assert_bot_token_header(FakeRequest("/health", headers={}))  # public: no raise either


def test_cors_origins_defaults_to_localhost_when_unset(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    origins = security.cors_origins()
    assert "http://localhost:3000" in origins


def test_cors_origins_reads_a_comma_separated_env_var(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example.com, https://b.example.com")
    assert security.cors_origins() == ["https://a.example.com", "https://b.example.com"]


def test_rate_limit_blocks_after_the_per_minute_cap():
    mw = security.RateLimitMiddleware(app=None, per_min=3)
    req = FakeRequest("/route", client_host="9.9.9.9")
    for _ in range(3):
        assert asyncio.run(mw.dispatch(req, _ok)) == "ok"
    resp = asyncio.run(mw.dispatch(req, _ok))
    assert resp.status_code == 429


def test_rate_limit_is_per_ip():
    mw = security.RateLimitMiddleware(app=None, per_min=1)
    a = FakeRequest("/route", client_host="1.1.1.1")
    b = FakeRequest("/route", client_host="2.2.2.2")
    assert asyncio.run(mw.dispatch(a, _ok)) == "ok"
    assert asyncio.run(mw.dispatch(b, _ok)) == "ok"  # different IP, own budget
    resp = asyncio.run(mw.dispatch(a, _ok))
    assert resp.status_code == 429


def test_rate_limit_zero_disables_it():
    mw = security.RateLimitMiddleware(app=None, per_min=0)
    req = FakeRequest("/route", client_host="3.3.3.3")
    for _ in range(50):
        assert asyncio.run(mw.dispatch(req, _ok)) == "ok"


def test_health_is_exempt_from_rate_limiting():
    mw = security.RateLimitMiddleware(app=None, per_min=1)
    req = FakeRequest("/health", client_host="4.4.4.4")
    for _ in range(10):
        assert asyncio.run(mw.dispatch(req, _ok)) == "ok"
