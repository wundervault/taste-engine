#!/usr/bin/env python3
"""Source candidate indie restaurants per target city using Yelp's neighborhood
search results, fetched via Bright Data Scraping Browser.

This script does two passes:
  1. FETCH: hit Yelp search pages, save raw HTML to a cache dir, save the raw
     parsed cards to data/restaurant_candidates_raw.json. BD-billable; skipped
     for any (city, page) whose HTML is already cached.
  2. (parsing iteration happens against the cached HTML — re-run is free.)

Final cuisine-spread filtering happens in a separate step (filter_candidates.py)
so we can iterate on filter rules without re-spending BD credits.

Usage:
    python scripts/source_restaurants.py
    python scripts/source_restaurants.py --refetch    # ignore cache, re-fetch all
"""
import json
import os
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
BROWSER_WS = os.environ["BROWSER_WS"]
DATA = ROOT / "data"
CACHE = DATA / "yelp_search_html_cache"
CACHE.mkdir(parents=True, exist_ok=True)

CITIES = {
    "weho":         ("West+Hollywood%2C+CA",                     "West Hollywood"),
    "williamsburg": ("Williamsburg%2C+Brooklyn%2C+NY",           "Williamsburg"),
    "mission":      ("Mission+District%2C+San+Francisco%2C+CA",  "Mission District"),
}

PAGES_PER_CITY = 3


def cache_path(city: str, page: int) -> Path:
    return CACHE / f"{city}_p{page}.html"


def parse_cards(html: str) -> list[dict]:
    """Extract every biz card on a search-results page.

    No filtering here — caller does that. Captures everything we can find
    around each /biz/<slug> anchor: name, review count, rating, categories,
    address fragment.
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen = set()
    # Skip anchors whose text is a generic CTA — those link to biz pages but
    # aren't the restaurant's name anchor.
    CTA_TEXT = {"order", "order delivery", "order pickup", "directions",
                "menu", "website", "call", "more info"}
    for a in soup.select('a[href^="/biz/"]'):
        href = a.get("href", "")
        m = re.match(r"^/biz/([^?#/]+)", href)
        if not m:
            continue
        slug = m.group(1)
        if slug in seen:
            continue
        name = a.get_text(strip=True)
        if not name or len(name) < 2 or len(name) > 80:
            continue
        if name.lower() in CTA_TEXT:
            continue
        # Walk up looking for a container that holds the FULL card metadata
        # (must contain "reviews)" or "(N reviews)" string to count as found).
        card = a
        text_pool = ""
        for _ in range(10):
            card = card.parent
            if card is None:
                break
            text_pool = card.get_text(" ", strip=True)
            if re.search(r"\(\s*[\d.,]+[kKmM]?\s*reviews?\)", text_pool):
                break
        # Yelp's modern format: "(5k reviews)" or "(3.2k reviews)" or "(714 reviews)".
        # Handle the "k" / "M" abbreviations.
        review_count = None
        rc_match = re.search(r"\(([\d.,]+)([kKmM])?\s*reviews?\)", text_pool)
        if rc_match:
            n_str = rc_match.group(1).replace(",", "")
            suffix = (rc_match.group(2) or "").lower()
            try:
                n = float(n_str)
                if suffix == "k":
                    n *= 1_000
                elif suffix == "m":
                    n *= 1_000_000
                review_count = int(n)
            except ValueError:
                pass
        # Rating: in the card text right before the "(N reviews)" string, e.g. "4.5 (5k reviews)"
        rating_match = re.search(r"(\d\.\d)\s*\([\d.,]+[kKmM]?\s*reviews?\)", text_pool)
        if not rating_match:
            rating_match = re.search(r"(\d\.\d)\s*star", text_pool, re.I)
        rating = float(rating_match.group(1)) if rating_match else None
        # Neighborhood: appears after "(N reviews)" and before price tier ($, $$, ...)
        nbhd_match = re.search(r"reviews?\)\s+([A-Za-z][\w .'/-]{2,40}?)\s+\$", text_pool)
        neighborhood = nbhd_match.group(1).strip() if nbhd_match else None
        # Categories — collect inner anchors that look like category links
        categories = []
        if card is not None:
            for c in card.select('a[href*="cflt="], a[href*="/c/"]'):
                ct = c.get_text(strip=True)
                if ct and ct not in categories and 2 < len(ct) < 40:
                    categories.append(ct)
        seen.add(slug)
        results.append({
            "name": name,
            "slug": slug,
            "review_count": review_count,
            "rating": rating,
            "neighborhood": neighborhood,
            "categories": categories[:5],
        })
    return results


def fetch_page(page, city: str, loc_enc: str, page_num: int, refetch: bool) -> str | None:
    """Returns HTML string (from cache or fresh fetch). None if blocked."""
    cp = cache_path(city, page_num)
    if cp.exists() and not refetch:
        print(f"  [{city}] page {page_num+1}: cache hit ({cp.stat().st_size} bytes)", flush=True)
        return cp.read_text()
    start = page_num * 10
    url = f"https://www.yelp.com/search?find_desc=Restaurants&find_loc={loc_enc}&start={start}"
    print(f"  [{city}] page {page_num+1}: fetching {url}", flush=True)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=90_000)
    except Exception as e:
        print(f"    goto error: {e}", flush=True)
        return None
    try:
        page.wait_for_selector('a[href^="/biz/"]', timeout=30_000)
    except Exception as e:
        print(f"    selector timeout: {e}", flush=True)
    page.wait_for_timeout(2000)
    try:
        html = page.content()
    except Exception as e:
        print(f"    content() error: {e}", flush=True)
        return None
    if len(html) < 5000:
        print(f"    ⚠ page too small ({len(html)} bytes) — likely blocked", flush=True)
        return None
    cp.write_text(html)
    print(f"    cached → {cp} ({len(html)} bytes)", flush=True)
    return html


def main():
    refetch = "--refetch" in sys.argv
    output = {}

    # Skip BD entirely if all pages already cached
    need_browser = refetch or any(
        not cache_path(c, p).exists()
        for c in CITIES for p in range(PAGES_PER_CITY)
    )

    pw_ctx = sync_playwright() if need_browser else None
    pw = pw_ctx.__enter__() if pw_ctx else None

    try:
        for city_key, (loc_enc, label) in CITIES.items():
            print(f"\n=== {city_key} ({label}) ===", flush=True)
            all_cards = []
            browser = None
            page = None
            for page_num in range(PAGES_PER_CITY):
                cp = cache_path(city_key, page_num)
                if cp.exists() and not refetch:
                    html = cp.read_text()
                    print(f"  [{city_key}] page {page_num+1}: cache hit ({cp.stat().st_size} bytes)", flush=True)
                else:
                    if page is None:
                        print("  connecting to scraping browser...", flush=True)
                        browser = pw.chromium.connect_over_cdp(BROWSER_WS)
                        page = browser.contexts[0].new_page()
                    html = fetch_page(page, city_key, loc_enc, page_num, refetch=True)
                    if html is None:
                        continue
                cards = parse_cards(html)
                print(f"    parsed {len(cards)} cards", flush=True)
                all_cards.extend(cards)
            # dedupe by slug, keep first occurrence
            seen = set()
            unique = []
            for c in all_cards:
                if c["slug"] in seen:
                    continue
                seen.add(c["slug"])
                unique.append(c)
            output[city_key] = {"label": label, "candidates": unique}
            print(f"  unique cards: {len(unique)}", flush=True)
            if page is not None:
                try:
                    page.close()
                    browser.close()
                except Exception:
                    pass
    finally:
        if pw_ctx:
            pw_ctx.__exit__(None, None, None)

    out_path = DATA / "restaurant_candidates_raw.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\n→ {out_path}")

    print("\n--- summary ---")
    for ck, v in output.items():
        n = len(v["candidates"])
        with_rc = sum(1 for c in v["candidates"] if c["review_count"])
        print(f"  {ck}: {n} cards ({with_rc} with review_count)")
        for c in v["candidates"][:5]:
            rc = c["review_count"]
            cats = ", ".join(c["categories"][:2]) or "—"
            print(f"    {rc!s:>5} reviews  {c['name']:<35} [{cats}]")


if __name__ == "__main__":
    main()
