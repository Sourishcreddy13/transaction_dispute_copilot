from __future__ import annotations

import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "reports" / "dashboard.png"
URL = "http://127.0.0.1:6006"


async def main() -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Install Playwright with `uv sync --extra dev` then run `uv run playwright install chromium`.") from exc

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
        try:
            await page.goto(URL, wait_until="networkidle", timeout=15_000)
        except Exception as exc:
            await browser.close()
            raise RuntimeError(f"Phoenix UI is not reachable at {URL}: {exc}") from exc
        await page.screenshot(path=str(TARGET), full_page=True)
        await browser.close()
    print(TARGET)


if __name__ == "__main__":
    asyncio.run(main())
