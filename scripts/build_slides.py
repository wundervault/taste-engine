#!/usr/bin/env python3
"""Assemble dashboard screenshots into a slide deck.

Produces dist/slides/PRESENTATION.html — a self-contained scrollable deck
that mirrors the video script beats. Each slide is one talking point + the
matching dashboard screenshot. Also exports to PDF via playwright headless
chromium.

The deck is structured so each slide can stand alone — useful for static
submission packets, judge review, or pasting into Google Slides / Keynote
as image-backed slides.

Usage:
    python scripts/build_slides.py
"""
import base64
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "dist" / "slides" / "shots"
OUT_HTML = ROOT / "dist" / "slides" / "PRESENTATION.html"
OUT_PDF = ROOT / "dist" / "slides" / "PRESENTATION.pdf"


SLIDES = [
    {
        "n": 1,
        "title": "Taste Engine",
        "subtitle": "Neighborhood-aware culinary intelligence",
        "kicker": "Bright Data Hackathon · May 2026",
        "body": "Turning local taste signals into operational menu strategy for fast-casual brands.",
        "shot": None,
        "layout": "title",
    },
    {
        "n": 2,
        "kicker": "0:00 – 0:30 · Cold open",
        "title": "Brands run quarterly menu planning",
        "body": "Chipotle shortened its limited-time-offer cycle. CAVA does four seasonal menus a year. The bottleneck isn't creativity — it's neighborhood-level intelligence.",
        "shot": "01_landing.png",
    },
    {
        "n": 3,
        "kicker": "0:30 – 1:00 · Bright Data dependency",
        "title": "Powered by Bright Data",
        "body": "64,372 dated indie reviews · 383 restaurants · 3 metro markets · 24 months. Four Bright Data products: Google Maps Reviews Dataset, Fast Maps Search, SERP API, Scraping Browser.",
        "shot": "02_sidebar.png",
    },
    {
        "n": 4,
        "kicker": "1:00 – 2:00 · Act 1",
        "title": "Trend Maturity Spread",
        "body": "Indie restaurants tagged into competitive (lunch) vs leading (chef-driven) pools. The split between pools is structural and tells the maturity story.",
        "shot": "03_maturity_landscape.png",
    },
    {
        "n": 5,
        "kicker": "1:00 – 2:00 · Act 1 continued",
        "title": "Same flavor, three cities, three stages",
        "body": "Williamsburg al pastor: 3 competitive, 8 leading → Rising, premature for chains. Mission: 58 competitive, 1 leading → Established, ship-now zone. WeHo: Established but thin.",
        "shot": "04a_williamsburg_overview.png",
    },
    {
        "n": 6,
        "kicker": "2:00 – 3:00 · Act 2",
        "title": "Validation: the system caught the shift before the brand announced",
        "body": "Between Q4 2025 and Q1 2026, al pastor crossed Weak → Established in Mission indie reviews. On January 27, Chipotle announced the al pastor relaunch for February 10. The classifier flipped first.",
        "shot": "05_mission_overview.png",
    },
    {
        "n": 7,
        "kicker": "2:00 – 3:00 · Act 2 continued",
        "title": "The next call: Mole gap-fill for Chipotle in Mission",
        "body": "Mole is the strongest mover in Mission this quarter — +7 mentions, 132 total. None of the chains can deliver it from current pantry. Confidence 68. Peak maturity. Low operational lift, national rollout. Californios verified as the comp.",
        "shot": "06_hero_mole_card.png",
    },
    {
        "n": 8,
        "kicker": "3:00 – 3:45 · Act 3",
        "title": "Quarterly cadence — what changed and what's next",
        "body": "Every visit shows movers up (mole +7, al pastor +6, pesto +5), maturity transitions, and a directional forecast. Brands come back monthly for the deltas, not a one-shot analysis.",
        "shot": "07_trends_brief_movers.png",
    },
    {
        "n": 9,
        "kicker": "3:00 – 3:45 · Act 3 continued",
        "title": "Next-quarter forecasts with confidence bands",
        "body": "Mission birria forecast to rise to ~27 mentions in Q2 (±2). Multi-snapshot Trends velocity and cross-city diffusion predictions are explicit v2 roadmap — accruing now.",
        "shot": "08_trends_forecast.png",
    },
    {
        "n": 10,
        "kicker": "3:45 – 4:15 · Naive LLM vs Taste Engine",
        "title": "Eight hallucination classes caught. Naive LLM: zero.",
        "body": "Same model. Same prompt. The naive LLM hallucinated nine SKUs across two dishes and reinvented al pastor as a vegan mushroom dish — completely missing the actual brand decision happening live. The LLM is a reasoning layer; the pipeline is the innovation.",
        "shot": "09_naive_vs_engine.png",
    },
    {
        "n": 11,
        "kicker": "Recommendations surface",
        "title": "Six recommendations per neighborhood — every claim tool-validated",
        "body": "Two cards per brand × three brands. Each card carries confidence (0-100), maturity stage, lift tier, rollout portability, proven badge with newsroom URLs, and comp restaurant verification.",
        "shot": "10_recommendations.png",
    },
    {
        "n": 12,
        "kicker": "4:15 – 4:45 · Close",
        "title": "Taste Engine",
        "subtitle": "Neighborhood-aware culinary intelligence",
        "body": "Built on Bright Data. Quarterly intelligence brief for brand strategy teams. Demo coverage: Chipotle, CAVA, Sweetgreen across Mission District, Williamsburg, West Hollywood.",
        "shot": None,
        "layout": "close",
    },
]


SLATE = "#2C5F87"
TAN = "#9C7F4A"
RUST = "#A04E2C"
BG = "#fffaf2"
TEXT = "#2A2825"
MUTED = "#5a4f3c"


def encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def render_slide(slide):
    layout = slide.get("layout", "content")
    kicker = slide.get("kicker", "")
    title = slide.get("title", "")
    subtitle = slide.get("subtitle", "")
    body = slide.get("body", "")
    n = slide["n"]

    if layout == "title":
        return f"""
<section class="slide title-slide">
  <div class="title-card">
    <div class="kicker">{slide['kicker']}</div>
    <h1>{title}</h1>
    <div class="subtitle">{subtitle}</div>
    <p>{body}</p>
  </div>
  <div class="slide-num">{n} / {len(SLIDES)}</div>
</section>
"""

    if layout == "close":
        return f"""
<section class="slide close-slide">
  <div class="close-card">
    <div class="kicker">{kicker}</div>
    <h1>{title}</h1>
    <div class="subtitle">{subtitle}</div>
    <p>{body}</p>
  </div>
  <div class="slide-num">{n} / {len(SLIDES)}</div>
</section>
"""

    shot_path = SHOTS / slide["shot"] if slide.get("shot") else None
    img_html = ""
    if shot_path and shot_path.exists():
        b64 = encode_image(shot_path)
        img_html = f'<div class="shot"><img src="data:image/png;base64,{b64}" alt="{title}"></div>'

    return f"""
<section class="slide content-slide">
  <div class="text-pane">
    <div class="kicker">{kicker}</div>
    <h1>{title}</h1>
    <p>{body}</p>
  </div>
  {img_html}
  <div class="slide-num">{n} / {len(SLIDES)}</div>
</section>
"""


HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Taste Engine — Presentation</title>
<style>
@page {{ size: 1280px 720px; margin: 0; }}
html, body {{ margin: 0; padding: 0; background: #f4ede0; font-family: Georgia, 'Times New Roman', serif; color: %TEXT%; }}
body {{ display: flex; flex-direction: column; align-items: center; gap: 22px; padding: 22px 0; }}

.slide {{
  position: relative;
  width: 1280px;
  height: 720px;
  background: %BG%;
  border: 1px solid #d6cab2;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(58,50,38,0.12);
  overflow: hidden;
  page-break-after: always;
  break-after: page;
}}

.slide-num {{
  position: absolute;
  bottom: 18px;
  right: 24px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  color: %MUTED%;
  letter-spacing: 0.06em;
}}
.kicker {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 14px;
  color: %SLATE%;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  margin-bottom: 12px;
}}
h1 {{
  font-family: Georgia, serif;
  font-size: 36px;
  color: %TEXT%;
  margin: 0 0 16px 0;
  line-height: 1.2;
}}
.subtitle {{
  font-family: Georgia, serif;
  font-style: italic;
  font-size: 22px;
  color: %SLATE%;
  margin-bottom: 16px;
}}
p {{
  font-size: 18px;
  line-height: 1.55;
  color: %TEXT%;
  margin: 0;
}}

/* Title slide layout */
.title-slide {{
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #f4ede0 0%, #ede5d0 100%);
}}
.title-card {{
  text-align: center;
  max-width: 900px;
  padding: 40px;
}}
.title-card h1 {{
  font-size: 64px;
  color: %SLATE%;
  margin-bottom: 8px;
}}
.title-card .subtitle {{
  font-size: 28px;
  color: %MUTED%;
  margin-bottom: 24px;
}}
.title-card p {{
  font-size: 20px;
  color: %MUTED%;
}}

/* Close slide layout */
.close-slide {{
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, %SLATE% 0%, #1f4361 100%);
  color: #fff;
}}
.close-slide .kicker, .close-slide h1, .close-slide .subtitle, .close-slide p, .close-slide .slide-num {{
  color: #fff;
}}
.close-slide .kicker {{ opacity: 0.75; }}
.close-card {{
  text-align: center;
  max-width: 900px;
  padding: 40px;
}}
.close-card h1 {{
  font-size: 64px;
  margin-bottom: 8px;
}}
.close-card .subtitle {{
  font-size: 28px;
  opacity: 0.9;
}}

/* Content slide layout — text left, screenshot right */
.content-slide {{
  display: grid;
  grid-template-columns: 460px 1fr;
  gap: 32px;
  padding: 48px 48px 56px 48px;
  align-items: center;
}}
.text-pane {{
  padding-right: 8px;
}}
.text-pane h1 {{
  font-size: 28px;
  margin-bottom: 18px;
}}
.text-pane p {{
  font-size: 16px;
  color: %MUTED%;
}}
.shot {{
  height: 600px;
  overflow: hidden;
  border-radius: 6px;
  border: 1px solid #d6cab2;
  background: #fff;
}}
.shot img {{
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: top;
  display: block;
}}
</style>
</head>
<body>
{slides}
</body>
</html>
"""


def main():
    if not SHOTS.exists() or not list(SHOTS.glob("*.png")):
        sys.exit("No screenshots found. Run scripts/capture_slides.py first.")

    slides_html = "\n".join(render_slide(s) for s in SLIDES)
    html_doc = (
        HTML
        .replace("{slides}", slides_html)
        .replace("%SLATE%", SLATE)
        .replace("%TAN%", TAN)
        .replace("%RUST%", RUST)
        .replace("%BG%", BG)
        .replace("%TEXT%", TEXT)
        .replace("%MUTED%", MUTED)
    )
    OUT_HTML.write_text(html_doc)
    print(f"wrote {OUT_HTML}")

    # PDF via playwright
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f"file://{OUT_HTML}")
        page.wait_for_load_state("networkidle")
        page.pdf(
            path=str(OUT_PDF),
            width="1280px",
            height="720px",
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            print_background=True,
        )
        browser.close()
    print(f"wrote {OUT_PDF}")


if __name__ == "__main__":
    main()
