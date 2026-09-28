from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "reports" / "dashboard.png"
URL = os.getenv("PHOENIX_UI_URL", "http://127.0.0.1:6006")
PROJECT_NAME = os.getenv("PHOENIX_PROJECT_NAME", "transaction-dispute-copilot")


async def main() -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Install Playwright with `uv sync --extra dev` then run `uv run playwright install chromium`."
        ) from exc

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(
            viewport={"width": 1600, "height": 1000},
            device_scale_factor=1,
        )
        try:
            await page.goto(URL, wait_until="networkidle", timeout=30_000)
            project = page.get_by_text(PROJECT_NAME, exact=True).first
            await project.wait_for(state="visible", timeout=15_000)
            await project.click()
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception as exc:
            await browser.close()
            raise RuntimeError(
                f"Phoenix UI/project '{PROJECT_NAME}' is not reachable or selectable at {URL}: {exc}"
            ) from exc

        # Fail closed if the UI never left the project list.
        if "/projects" not in page.url:
            await browser.close()
            raise RuntimeError(f"Phoenix project navigation did not reach a project page: {page.url}")

        await page.screenshot(path=str(TARGET), full_page=True)
        final_url = page.url
        await browser.close()

    TARGET.with_suffix(".png.capture-meta.json").write_text(
        json.dumps(
            {
                "generated_by": "scripts/capture_phoenix_dashboard.py",
                "source": final_url,
                "phoenix_project": PROJECT_NAME,
                "capture_type": "phoenix_ui_project_page",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(TARGET)


if __name__ == "__main__":
    asyncio.run(main())
