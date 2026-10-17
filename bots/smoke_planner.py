"""Smoke test: drive /planner with plain Playwright (no LLM) and screenshot the result."""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parent.parent / "data" / "screenshots"


async def main(base: str = "http://localhost:3000") -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch()
        page = await b.new_page(viewport={"width": 1400, "height": 850})
        page.on("console", lambda m: print("console:", m.type, m.text[:300]) if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: print("pageerror:", str(e)[:500]))
        await page.goto(f"{base}/planner")
        await page.get_by_test_id("origin-input").fill("York University")
        await page.get_by_test_id("destination-input").fill("Union Station")
        for m in ("drive", "park_ride"):
            await page.get_by_test_id(f"mode-toggle-{m}").click()
        await page.get_by_test_id("toggle-has_car").click()
        await page.get_by_test_id("compute-route").click()
        await page.wait_for_function("window.__4ward && window.__4ward.state === 'done'", timeout=60000)
        await page.get_by_test_id("select-route-transit_rail").click()
        await page.get_by_test_id("confirm-route").click()
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(OUT / "planner.png"))
        print(await page.evaluate("JSON.stringify(window.__4ward.confirmed && window.__4ward.confirmed.title)"))
        await b.close()


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:]))
