"""CORS, shared bot token, and basic rate limiting. Local-friendly defaults."""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

# Paths that may be called without a bot token (public dashboard / health checks).
PUBLIC_PATH_PREFIXES = (
    "/health",
    "/assumptions",
    "/suggest",
    "/geocode",
    "/bikeshare",
    "/route",
    "/insights",
    "/live",
    "/docs",
    "/openapi.json",
    "/redoc",
)


def cors_origins() -> list[str]:
    raw = os.environ.get("CORS_ORIGINS", "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    # Local dashboard defaults when unset.
    return ["http://localhost:3000", "http://127.0.0.1:3000"]


def bot_token() -> str:
    return os.environ.get("BOT_TOKEN", "").strip()


def requires_bot_token(path: str) -> bool:
    token = bot_token()
    if not token:
        return False  # local / unset: no auth gate
    if path.startswith("/ws/"):
        return True
    for prefix in PUBLIC_PATH_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return False
    return True


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple per-IP sliding window. RATE_LIMIT_PER_MIN=0 disables."""

    def __init__(self, app, per_min: int | None = None):
        super().__init__(app)
        self.per_min = per_min if per_min is not None else int(os.environ.get("RATE_LIMIT_PER_MIN", "120"))
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next) -> Response:
        if self.per_min <= 0 or request.url.path in ("/health", "/docs", "/openapi.json", "/redoc"):
            return await call_next(request)
        ip = request.client.host if request.client else "unknown"
        now = time.time()
        window = self.hits[ip]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.per_min:
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
        window.append(now)
        return await call_next(request)


class BotTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if not requires_bot_token(path):
            return await call_next(request)
        expected = bot_token()
        got = request.headers.get("x-bot-token", "")
        # WebSocket: Starlette may not expose custom headers the same way; also allow ?bot_token=
        if path.startswith("/ws/") and not got:
            got = request.query_params.get("bot_token", "")
        if got != expected:
            return JSONResponse({"detail": "missing or invalid X-Bot-Token"}, status_code=401)
        return await call_next(request)


def assert_bot_token_header(request: Request) -> None:
    """Dependency-style check for routes that always want the token when configured."""
    if not requires_bot_token(request.url.path):
        return
    if request.headers.get("x-bot-token", "") != bot_token():
        raise HTTPException(401, "missing or invalid X-Bot-Token")
