#!/usr/bin/env python3
"""Render SUBMISSION.md and VIDEO_SCRIPT.md to judge-ready HTML and PDF.

Output paths:
    dist/SUBMISSION.html        + dist/SUBMISSION.pdf
    dist/VIDEO_SCRIPT.html      + dist/VIDEO_SCRIPT.pdf
    dist/DESIGN_AUDIT.html      + dist/DESIGN_AUDIT.pdf   (if file exists)

Typography is tuned for reading: Georgia body, generous line height, large
margins, single accent color matching the dashboard. Print-to-PDF uses
playwright headless chromium (same toolchain we already have installed).

Usage:
    python scripts/build_submission_artifacts.py             # builds all
    python scripts/build_submission_artifacts.py --no-pdf    # HTML only
"""
import argparse
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
DIST.mkdir(exist_ok=True)

# Source files (basename → title)
DOCS = {
    "SUBMISSION.md":  "Taste Engine — Submission",
    "VIDEO_SCRIPT.md": "Taste Engine — Video Script",
    "DESIGN_AUDIT.md": "Taste Engine — Design Audit",
}

# Dashboard meaning + brand colors (so the rendered docs feel like the same product)
ACCENT = "#2C5F87"   # slate from MEANING_COLORS["high"]
ACCENT2 = "#9C7F4A"  # tan
TEXT = "#2A2825"
MUTED = "#5a4f3c"
BG = "#fffaf2"

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  html {{ background: #f4ede0; }}
  body {{
    font-family: Georgia, 'Times New Roman', serif;
    font-size: 16px;
    line-height: 1.65;
    color: {TEXT};
    background: {BG};
    max-width: 780px;
    margin: 0 auto;
    padding: 2.5rem 2.2rem 4rem 2.2rem;
    box-shadow: 0 1px 4px rgba(58,50,38,0.10);
    border-radius: 4px;
  }}
  h1 {{
    font-family: Georgia, serif;
    font-size: 2.1rem;
    line-height: 1.2;
    color: {ACCENT};
    margin: 0 0 0.4rem 0;
    border-bottom: 2px solid {ACCENT};
    padding-bottom: 0.4rem;
  }}
  h2 {{
    font-family: Georgia, serif;
    font-size: 1.45rem;
    color: {ACCENT};
    margin-top: 2.2rem;
    margin-bottom: 0.6rem;
    border-bottom: 1px solid #d6cab2;
    padding-bottom: 0.25rem;
  }}
  h3 {{
    font-family: Georgia, serif;
    font-size: 1.18rem;
    color: {TEXT};
    margin-top: 1.6rem;
    margin-bottom: 0.4rem;
  }}
  h4 {{
    font-size: 0.95rem;
    color: {ACCENT2};
    margin-top: 1.2rem;
    margin-bottom: 0.3rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }}
  p {{ margin: 0.55rem 0 0.85rem 0; }}
  blockquote {{
    margin: 0.9rem 0;
    padding: 0.7rem 1.1rem;
    border-left: 3px solid {ACCENT2};
    background: #f4ede0;
    color: {MUTED};
    font-style: italic;
    border-radius: 0 4px 4px 0;
  }}
  strong {{ color: {TEXT}; }}
  em {{ color: {MUTED}; }}
  code {{
    font-family: 'JetBrains Mono', 'SF Mono', Menlo, monospace;
    font-size: 0.86em;
    background: #f4ede0;
    padding: 0.05rem 0.35rem;
    border-radius: 3px;
    color: {ACCENT};
  }}
  pre {{
    background: #2c3e50;
    color: #f4ede0;
    padding: 0.9rem 1rem;
    border-radius: 4px;
    overflow-x: auto;
    font-size: 0.85em;
    line-height: 1.5;
  }}
  pre code {{ background: transparent; color: inherit; padding: 0; }}
  table {{
    border-collapse: collapse;
    margin: 1rem 0;
    font-size: 0.92rem;
    width: 100%;
  }}
  th, td {{
    text-align: left;
    padding: 0.45rem 0.7rem;
    border-bottom: 1px solid #d6cab2;
    vertical-align: top;
  }}
  th {{
    background: #f4ede0;
    color: {ACCENT};
    font-family: Georgia, serif;
    font-weight: 700;
    border-bottom: 2px solid {ACCENT};
  }}
  tr:nth-child(even) td {{ background: #fdfaf3; }}
  ul, ol {{ padding-left: 1.5rem; }}
  li {{ margin: 0.25rem 0; }}
  a {{ color: {ACCENT}; text-decoration: underline; }}
  a:visited {{ color: {ACCENT2}; }}
  hr {{
    border: none;
    border-top: 1px solid #d6cab2;
    margin: 1.8rem 0;
  }}
  .header-meta {{
    font-family: Georgia, serif;
    font-size: 0.78rem;
    color: {MUTED};
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-bottom: 0.3rem;
  }}
  @media print {{
    html, body {{ background: #fff; }}
    body {{ box-shadow: none; padding: 1.2rem 0.8rem; max-width: none; }}
    h1 {{ break-after: avoid; }}
    h2, h3 {{ break-after: avoid; }}
    table, pre, blockquote {{ break-inside: avoid; }}
    a {{ color: {ACCENT} !important; text-decoration: none; }}
  }}
</style>
</head>
<body>
<div class="header-meta">Taste Engine · Bright Data Hackathon · May 2026</div>
{body}
</body>
</html>
"""


def render_html(md_path: Path, title: str) -> str:
    text = md_path.read_text()
    html_body = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "toc", "sane_lists", "attr_list"],
    )
    return HTML_TEMPLATE.format(title=title, body=html_body,
                                 ACCENT=ACCENT, ACCENT2=ACCENT2,
                                 TEXT=TEXT, MUTED=MUTED, BG=BG)


def write_pdf(html_path: Path, pdf_path: Path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f"file://{html_path}")
        page.wait_for_load_state("networkidle")
        page.pdf(
            path=str(pdf_path),
            format="Letter",
            margin={"top": "0.75in", "bottom": "0.75in",
                    "left": "0.7in", "right": "0.7in"},
            print_background=True,
        )
        browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-pdf", action="store_true",
                        help="HTML only, skip PDF rendering")
    args = parser.parse_args()

    for basename, title in DOCS.items():
        md_path = ROOT / basename
        if not md_path.exists():
            print(f"skip {basename} — not found")
            continue
        html = render_html(md_path, title)
        stem = md_path.stem
        html_path = DIST / f"{stem}.html"
        html_path.write_text(html)
        print(f"wrote {html_path}")

        if not args.no_pdf:
            pdf_path = DIST / f"{stem}.pdf"
            write_pdf(html_path, pdf_path)
            print(f"wrote {pdf_path}")


if __name__ == "__main__":
    main()
