#!/usr/bin/env python3
"""Capture dashboard screenshots for the slide presentation.

Walks the live Streamlit dashboard (http://localhost:8501), takes the 10
screenshots called out in VIDEO_SCRIPT.md's "Required dashboard captures"
section, and saves them to dist/slides/.

Usage:
    python scripts/capture_slides.py
"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist" / "slides" / "shots"
OUT.mkdir(parents=True, exist_ok=True)

URL = "http://localhost:8501"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()
        page.goto(URL)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2500)

        # The sidebar city radio defaults to Mission per app.py (index=2).
        # Verify by checking sidebar text.
        print("Loaded dashboard. Capturing slides...")

        def shot(name: str, full: bool = False):
            path = OUT / f"{name}.png"
            page.screenshot(path=str(path), full_page=full)
            print(f"  → {path.name}")

        # 1. Sidebar / landing
        shot("01_landing")

        # 2. Sidebar zoomed — Bright Data scale block.
        # We crop by capturing full page; downstream slide layout uses CSS.
        shot("02_sidebar", full=True)

        # 3. Mission Overview — Maturity Landscape scatter (scroll to it)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)
        shot("03_maturity_landscape", full=True)

        # 4. Cross-city al pastor — switch to Williamsburg, then to WeHo
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(800)
        # Click Williamsburg radio
        page.locator('label:has-text("Williamsburg")').click(timeout=8000)
        page.wait_for_timeout(2500)
        shot("04a_williamsburg_overview", full=True)
        # WeHo
        page.locator('label:has-text("West Hollywood")').click(timeout=8000)
        page.wait_for_timeout(2500)
        shot("04b_weho_overview", full=True)
        # Back to Mission
        page.locator('label:has-text("Mission District")').click(timeout=8000)
        page.wait_for_timeout(2500)
        shot("05_mission_overview", full=True)

        # 6. Hero card — already in Overview header. Take a clean Overview shot.
        page.evaluate("window.scrollTo(0, 280)")
        page.wait_for_timeout(800)
        shot("06_hero_mole_card")

        # 7-8. Trends tab
        page.locator('button[role="tab"]:has-text("Trends")').click(timeout=8000)
        page.wait_for_timeout(2500)
        shot("07_trends_brief_movers", full=True)
        # Scroll to forecasts
        page.evaluate("window.scrollTo(0, 600)")
        page.wait_for_timeout(800)
        shot("08_trends_forecast")

        # 9. vs Naive LLM tab
        page.locator('button[role="tab"]:has-text("vs Naive LLM")').click(timeout=8000)
        page.wait_for_timeout(2500)
        shot("09_naive_vs_engine", full=True)

        # 10. Recommendations tab — full brand layout
        page.locator('button[role="tab"]:has-text("Recommendations")').click(timeout=8000)
        page.wait_for_timeout(2500)
        shot("10_recommendations", full=True)

        # Bonus: Methodology tab
        page.locator('button[role="tab"]:has-text("Methodology")').click(timeout=8000)
        page.wait_for_timeout(2500)
        shot("11_methodology", full=True)

        browser.close()
        print(f"\nDone. {len(list(OUT.glob('*.png')))} screenshots in {OUT}")


if __name__ == "__main__":
    main()
