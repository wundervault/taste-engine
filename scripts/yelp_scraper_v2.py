#!/usr/bin/env python3
"""
Yelp review scraper — fixed version.
Key fixes vs v1:
- Uses sync_playwright (matches working probe)
- Uses browser.contexts[0] to reuse BD's pre-authenticated session (bypasses DataDome)
- Uses wait_for_selector with 30s timeout instead of fixed sleep
- Follows probe pattern exactly for the parts that worked
"""
import re, json, os, sys
from pathlib import Path
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
BROWSER_WS = os.environ["BROWSER_WS"]
DATA_DIR = Path(__file__).parent.parent / "data"

RESTAURANTS = {
    "weho": [
        {"name": "Gracias Madre",    "slug": "gracias-madre-west-hollywood-3",   "cuisine": "plant-based Mexican"},
        {"name": "Crossroads Kitchen","slug": "crossroads-kitchen-los-angeles-5", "cuisine": "vegan Mediterranean"},
        {"name": "Open Sesame",      "slug": "open-sesame-los-angeles",           "cuisine": "Lebanese Mediterranean"},
        {"name": "Moonbowls WeHo",   "slug": "moonbowls-west-hollywood",          "cuisine": "Korean bowls"},
    ],
    "williamsburg": [
        {"name": "Kokomo",       "slug": "kokomo-brooklyn",          "cuisine": "Caribbean"},
        {"name": "K'Far",        "slug": "kfar-brooklyn",            "cuisine": "Israeli cafe"},
        {"name": "Laser Wolf",   "slug": "laser-wolf-brooklyn",      "cuisine": "Israeli grill"},
        {"name": "Ensenada",     "slug": "ensenada-brooklyn",        "cuisine": "coastal Mexican"},
    ],
}

PAGES_PER_RESTAURANT = 3  # ~30 reviews each


def extract_reviews(html):
    """Extract reviews using span[lang] + walk-up card pattern from working extractor."""
    soup = BeautifulSoup(html, 'html.parser')
    reviews = []
    seen = set()
    for span in soup.select('span[lang]'):
        text = span.get_text(strip=True)
        if len(text) < 50 or text in seen:
            continue
        seen.add(text)
        card = span
        date_str = rating_str = None
        for _ in range(10):
            card = card.parent
            if card is None:
                break
            date_el = card.find(string=re.compile(r'[A-Z][a-z]{2,8}\s+\d{1,2},\s+20\d\d'))
            rating_el = card.find(attrs={'aria-label': re.compile(r'\d.*star')})
            if date_el:
                m = re.search(r'[A-Z][a-z]{2,8}\s+\d{1,2},\s+20\d\d', str(date_el))
                date_str = m.group(0) if m else None
            if rating_el:
                rating_str = rating_el.get('aria-label')
            if date_str and rating_str:
                break
        reviews.append({"text": text, "date": date_str, "rating": rating_str})
    return reviews


def scrape_restaurant(page, restaurant, pages=PAGES_PER_RESTAURANT):
    slug = restaurant["slug"]
    base_url = f"https://www.yelp.com/biz/{slug}"
    all_reviews = []

    for page_num in range(pages):
        start = page_num * 10
        url = base_url if start == 0 else f"{base_url}?start={start}"
        print(f"  [{restaurant['name']}] page {page_num+1}: {url}", flush=True)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        except Exception as e:
            print(f"    goto timeout/error: {e}", flush=True)
            page.wait_for_timeout(3000)

        # Wait for review content — gives DataDome up to 30s to resolve
        try:
            page.wait_for_selector(
                '[data-testid="reviews-section"], section[aria-label*="Recommended"] li, li[class*="review"], span[lang]',
                timeout=30_000
            )
        except Exception as e:
            print(f"    selector timeout: {e}", flush=True)

        page.wait_for_timeout(2000)

        try:
            html = page.content()
        except Exception as e:
            print(f"    content() error: {e} — skipping page", flush=True)
            continue

        reviews = extract_reviews(html)
        print(f"    got {len(reviews)} reviews (html={len(html)} bytes)", flush=True)

        if len(html) < 5000:
            print(f"    ⚠ page too small — likely blocked, stopping this restaurant", flush=True)
            break

        all_reviews.extend(reviews)

    return {
        "name": restaurant["name"],
        "cuisine": restaurant["cuisine"],
        "yelp_slug": slug,
        "review_count": len(all_reviews),
        "reviews": all_reviews,
    }


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "weho"
    skip = int(sys.argv[2]) if len(sys.argv) > 2 else 0  # skip first N restaurants
    restaurants = RESTAURANTS.get(target, RESTAURANTS["weho"])[skip:]

    print(f"Connecting to Bright Data Scraping Browser...", flush=True)
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(BROWSER_WS)
        print(f"Connected. Contexts: {len(browser.contexts)}", flush=True)

        # Use existing context if available — this preserves BD session cookies
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()

        # Verify setup works with known-good URL first
        print("\nVerifying with Parks BBQ (known good)...", flush=True)
        page.goto("https://www.yelp.com/biz/parks-bbq-los-angeles",
                  wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_selector("span[lang]", timeout=30_000)
        except:
            pass
        page.wait_for_timeout(2000)
        test_html = page.content()
        test_reviews = extract_reviews(test_html)
        print(f"Verification: {len(test_reviews)} reviews, html={len(test_html)} bytes", flush=True)

        if len(test_html) < 5000:
            print("❌ Verification failed — scraping browser blocked. Check BROWSER_WS credentials.", flush=True)
            browser.close()
            return

        print(f"✅ Verification passed. Scraping {len(restaurants)} {target} restaurants...\n", flush=True)
        browser.close()

    # Reconnect fresh for each restaurant to avoid 502 no_peer degradation
    results = []
    for restaurant in restaurants:
        print(f"\nScraping: {restaurant['name']} ({restaurant['cuisine']})", flush=True)
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(BROWSER_WS)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()
            try:
                data = scrape_restaurant(page, restaurant)
            except Exception as e:
                print(f"  Error: {e}", flush=True)
                data = {"name": restaurant["name"], "cuisine": restaurant["cuisine"],
                        "yelp_slug": restaurant["slug"], "review_count": 0, "reviews": []}
            finally:
                browser.close()
        results.append(data)
        print(f"  Total: {data['review_count']} reviews\n", flush=True)

    # Merge with any existing saved results for restaurants we didn't re-scrape
    output = DATA_DIR / f"yelp_{target}_reviews.json"
    if output.exists() and skip > 0:
        with open(output) as f:
            existing = json.load(f)
        # Keep existing entries not in current results
        existing_names = {r["name"] for r in results}
        merged = [r for r in existing if r["name"] not in existing_names] + results
        results = merged

    with open(output, "w") as f:
        json.dump(results, f, indent=2)

    total = sum(r["review_count"] for r in results)
    print(f"\nDone. {total} total reviews → {output}")

    # Sample output
    for r in results:
        print(f"\n{r['name']} ({r['review_count']} reviews):")
        for rev in r['reviews'][:2]:
            print(f"  [{rev.get('date','?')}] {rev.get('rating','?')} — {rev['text'][:100]}...")


if __name__ == "__main__":
    main()
