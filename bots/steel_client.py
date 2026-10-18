"""One Steel cloud browser session per bot. Always released, even on error.

Docs: https://docs.steel.dev/llms.txt
"""
import asyncio
import os

from playwright.async_api import BrowserContext, async_playwright
from steel import Steel

STEEL_API_KEY = os.environ.get("STEEL_API_KEY", "")


class SteelBot:
    def __init__(self, session_id: str, live_view_url: str, context: BrowserContext):
        self.id = session_id
        self.live_view_url = live_view_url
        self.context = context


class steel_session:
    """async with steel_session() as bot: ... bot.context, bot.live_view_url"""

    def __init__(self, api_timeout_ms: int = 240_000):
        """api_timeout_ms: Steel session max duration in MILLISECONDS (Steel's API field, not seconds).
        Must comfortably exceed the bot's own timeout_s so Steel never kills the session mid-run."""
        self.api_timeout_ms = api_timeout_ms
        self._client: Steel | None = None
        self._session = None
        self._pw = None
        self._browser = None

    async def __aenter__(self) -> SteelBot:
        if not STEEL_API_KEY:
            raise RuntimeError("STEEL_API_KEY is not set")
        self._client = Steel(steel_api_key=STEEL_API_KEY)
        self._session = await asyncio.to_thread(self._client.sessions.create, api_timeout=self.api_timeout_ms)
        self._pw = await async_playwright().start()
        try:
            url = f"wss://connect.steel.dev?apiKey={STEEL_API_KEY}&sessionId={self._session.id}"
            try:
                self._browser = await self._pw.chromium.connect_over_cdp(url)
            except Exception:
                # fall back to the session's own websocket url if the built one fails to connect
                self._browser = await self._pw.chromium.connect_over_cdp(self._session.websocket_url)
        except Exception:
            await self._pw.stop()
            await asyncio.to_thread(self._client.sessions.release, self._session.id)
            raise
        ctx = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
        # debug_url (.../player) is the public, unauthenticated embeddable live view. session_viewer_url
        # (app.steel.dev/...) is Steel's own dashboard and requires signing into a Steel account to view --
        # wrong for embedding in front of our own users.
        return SteelBot(self._session.id, self._session.debug_url, ctx)

    async def __aexit__(self, *exc) -> bool:
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
        if self._session and self._client:
            try:
                await asyncio.to_thread(self._client.sessions.release, self._session.id)
            except Exception:
                pass
        return False
