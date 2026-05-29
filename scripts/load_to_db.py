#!/usr/bin/env python3
"""Load existing JSON dumps into data/hermes.db.

Idempotent: re-running won't duplicate rows. Reads from data/*.json,
writes to data/hermes.db. Does not modify the source JSON.

Usage:
    python scripts/load_to_db.py
"""
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hermes.db import init_db  # noqa: E402

DATA = ROOT / "data"
NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")

# (file, city) — city is implied by the scrape, not stored in the JSON
REVIEW_FILES = [
    ("yelp_weho_reviews.json", "West Hollywood"),
    ("yelp_williamsburg_reviews.json", "Williamsburg"),
]

BUCKET_MENUS = [  # chipotle/cava style: top-level keys are categories of ingredients
    ("chipotle_menu.json", "Chipotle"),
    ("cava_menu.json", "CAVA"),
]
BUCKET_CATEGORIES = (
    "bases", "proteins", "salsas", "toppings",
    "dips", "spreads", "dips_spreads",
    "dressings", "sauces", "vessels", "sides",
)

DISH_MENUS = [  # sweetgreen style: list of dishes with ingredients
    ("sweetgreen_menu.json", "Sweetgreen"),
]

# Sweetgreen JSON has occasional section-header / legal rows mixed in with dishes.
SWEETGREEN_NON_DISH = {"WRAPS HIT DIFFERENT", "Legal"}

TREND_FILES = ["trends_la_ca.json", "trends_ny.json"]


# --- helpers ----------------------------------------------------------------

def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%b %d, %Y").date().isoformat()
    except ValueError:
        return None


def parse_rating(s):
    if not s:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", str(s))
    return int(float(m.group(1))) if m else None


def sha1(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


# --- loaders ----------------------------------------------------------------

def load_reviews(conn):
    inserted = skipped = 0
    for fname, city in REVIEW_FILES:
        path = DATA / fname
        if not path.exists():
            print(f"  skip (missing): {fname}")
            continue
        for rest in json.loads(path.read_text()):
            rid = upsert_restaurant(
                conn,
                name=rest["name"],
                city=city,
                cuisine=rest.get("cuisine"),
                yelp_slug=rest.get("yelp_slug"),
            )
            for r in rest.get("reviews", []):
                text = r.get("text", "").strip()
                if not text:
                    continue
                h = sha1(text)
                cur = conn.execute(
                    """INSERT OR IGNORE INTO reviews
                       (restaurant_id, source, review_date, rating, text, text_hash, scraped_at)
                       VALUES (?, 'yelp', ?, ?, ?, ?, ?)""",
                    (rid, parse_date(r.get("date")), parse_rating(r.get("rating")), text, h, NOW),
                )
                if cur.rowcount:
                    inserted += 1
                else:
                    skipped += 1
    print(f"reviews: inserted={inserted} skipped_dupes={skipped}")


def upsert_restaurant(conn, *, name, city, cuisine=None, yelp_slug=None, gmaps_id=None):
    if yelp_slug:
        row = conn.execute("SELECT id FROM restaurants WHERE yelp_slug = ?", (yelp_slug,)).fetchone()
        if row:
            return row["id"]
    row = conn.execute(
        "SELECT id FROM restaurants WHERE name = ? AND city = ?", (name, city)
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        """INSERT INTO restaurants (name, city, cuisine, yelp_slug, gmaps_id)
           VALUES (?, ?, ?, ?, ?)""",
        (name, city, cuisine, yelp_slug, gmaps_id),
    )
    return cur.lastrowid


def load_bucket_menus(conn):
    # Rebuild ONLY the bucket-style rows this loader owns. Do NOT touch
    # sweetgreen_* (extract_sweetgreen_pantry.py) or sweetgreen_byo_*
    # (load_sweetgreen_byo.py) rows — those belong to other scripts.
    owned_brands = [b for _, b in BUCKET_MENUS]
    placeholders = ",".join("?" * len(owned_brands))
    conn.execute(f"DELETE FROM brand_menu_items WHERE brand IN ({placeholders})", owned_brands)
    n = 0
    for fname, brand in BUCKET_MENUS:
        path = DATA / fname
        if not path.exists():
            print(f"  skip (missing): {fname}")
            continue
        d = json.loads(path.read_text())
        location = d.get("location")
        for category in BUCKET_CATEGORIES:
            for item in d.get(category, []):
                cur = conn.execute(
                    """INSERT OR IGNORE INTO brand_menu_items
                       (brand, location, category, item, ingredients_text)
                       VALUES (?, ?, ?, ?, NULL)""",
                    (brand, location, category, item),
                )
                n += cur.rowcount
    print(f"bucket menu items inserted: {n}")


def load_dish_menus(conn):
    # Rebuild only the 'dish' category for the brands we own here.
    owned_brands = [b for _, b in DISH_MENUS]
    placeholders = ",".join("?" * len(owned_brands))
    conn.execute(
        f"DELETE FROM brand_menu_items WHERE category='dish' AND brand IN ({placeholders})",
        owned_brands,
    )
    n = 0
    for fname, brand in DISH_MENUS:
        path = DATA / fname
        if not path.exists():
            print(f"  skip (missing): {fname}")
            continue
        for d in json.loads(path.read_text()):
            name = (d.get("dish_name") or "").strip()
            if not name or name in SWEETGREEN_NON_DISH:
                continue
            cur = conn.execute(
                """INSERT OR IGNORE INTO brand_menu_items
                   (brand, location, category, item, ingredients_text)
                   VALUES (?, NULL, 'dish', ?, ?)""",
                (brand, name, d.get("ingredients_text")),
            )
            n += cur.rowcount
    print(f"dish menu items inserted: {n}")


def load_trends(conn):
    n = 0
    for fname in TREND_FILES:
        path = DATA / fname
        if not path.exists():
            print(f"  skip (missing): {fname}")
            continue
        d = json.loads(path.read_text())
        geo = d["geo"]
        timeframe = d["timeframe"]
        for term, v in d.get("terms", {}).items():
            cur = conn.execute(
                """INSERT INTO trends (term, geo, timeframe, avg_12m, recent_4w, peak, trend, scraped_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(term, geo, timeframe) DO UPDATE SET
                     avg_12m=excluded.avg_12m,
                     recent_4w=excluded.recent_4w,
                     peak=excluded.peak,
                     trend=excluded.trend,
                     scraped_at=excluded.scraped_at""",
                (term, geo, timeframe, v.get("avg_12m"), v.get("recent_4w"),
                 v.get("peak"), v.get("trend"), NOW),
            )
            n += 1
    print(f"trends upserted: {n}")


def summary(conn):
    print("\n--- summary ---")
    for tbl in ("restaurants", "reviews", "brand_menu_items", "trends", "flavor_mentions"):
        n = conn.execute(f"SELECT COUNT(*) AS c FROM {tbl}").fetchone()["c"]
        print(f"  {tbl}: {n}")


def main():
    conn = init_db()
    try:
        load_reviews(conn)
        load_bucket_menus(conn)
        load_dish_menus(conn)
        load_trends(conn)
        conn.commit()
        summary(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
