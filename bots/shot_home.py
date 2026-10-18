"""Run a full prompt through the home page UI and screenshot it (demo dry-run helper)."""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parent.parent / "data" / "screenshots"


async def main(base: str = "http://localhost:3000") -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch()
        page = await b.new_page(viewport={"width": 1500, "height": 950})
        page.on("pageerror", lambda e: print("pageerror:", str(e)[:300]))
        await page.goto(base)
        await page.get_by_test_id("example-1").click()
        await page.get_by_test_id("plan-trip").click()
        await page.wait_for_timeout(9000)
        await page.screenshot(path=str(OUT / "home_mid.png"))
        await page.get_by_test_id("choose-fastest").wait_for(timeout=120000)
        await page.get_by_test_id("choose-fastest").click()
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(OUT / "home_done.png"), full_page=True)
        await b.close()


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:]))
