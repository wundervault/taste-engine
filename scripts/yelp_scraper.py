#!/usr/bin/env python3
"""
Scrape Yelp reviews for WeHo restaurants via Bright Data Scraping Browser.
Output: data/yelp_weho_reviews.json
"""
import asyncio, json, os, re, sys
from pathlib import Path
from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
BROWSER_WS = os.environ["BROWSER_WS"]
DATA_DIR = Path(__file__).parent.parent / "data"

RESTAURANTS = [
    {"name": "Gracias Madre", "slug": "gracias-madre-west-hollywood-3", "cuisine": "plant-based Mexican"},
    {"name": "Crossroads Kitchen", "slug": "crossroads-kitchen-los-angeles-5", "cuisine": "plant-based Mediterranean"},
    {"name": "Open Sesame", "slug": "open-sesame-los-angeles", "cuisine": "Lebanese Mediterranean"},
    {"name": "Moonbowls WeHo", "slug": "moonbowls-west-hollywood", "cuisine": "Korean bowls"},
]

PAGES_PER_RESTAURANT = 3  # 3 pages × ~10 reviews = ~30 per restaurant


async def scrape_yelp_page(page, url):
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3000)
    spans = await page.query_selector_all("span[lang]")
    reviews = []
    for span in spans:
        text = (await span.inner_text()).strip()
        if len(text) > 40:
            reviews.append(text)
    return reviews


async def scrape_restaurant(browser, restaurant):
    slug = restaurant["slug"]
    base_url = f"https://www.yelp.com/biz/{slug}"
    all_reviews = []

    context = await browser.new_context()
    page = await context.new_page()

    for page_num in range(PAGES_PER_RESTAURANT):
        start = page_num * 10
        url = base_url if start == 0 else f"{base_url}?start={start}"
        print(f"  Fetching {restaurant['name']} page {page_num+1}: {url}")
        try:
            reviews = await scrape_yelp_page(page, url)
            all_reviews.extend(reviews)
            print(f"    Got {len(reviews)} reviews")
            await asyncio.sleep(2)
        except Exception as e:
            print(f"    Error: {e}")
            break

    await context.close()
    return {
        "name": restaurant["name"],
        "cuisine": restaurant["cuisine"],
        "yelp_slug": slug,
        "review_count": len(all_reviews),
        "reviews": all_reviews,
    }


async def main():
    print(f"Connecting to Bright Data Scraping Browser...")
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(BROWSER_WS)
        print("Connected.\n")

        results = []
        for restaurant in RESTAURANTS:
            print(f"Scraping: {restaurant['name']} ({restaurant['cuisine']})")
            data = await scrape_restaurant(browser, restaurant)
            results.append(data)
            print(f"  Total reviews collected: {data['review_count']}\n")

        await browser.close()

    output = DATA_DIR / "yelp_weho_reviews.json"
    with open(output, "w") as f:
        json.dump(results, f, indent=2)

    total = sum(r["review_count"] for r in results)
    print(f"Done. {total} total reviews saved to {output}")


if __name__ == "__main__":
    asyncio.run(main())
