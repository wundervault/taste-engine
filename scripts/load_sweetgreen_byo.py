#!/usr/bin/env python3
"""Load Sweetgreen BYO (Build-Your-Own) catalog into brand_menu_items.

Data source: 8 screenshots of the Create-Your-Own flow at
`order.sweetgreen.com`, captured 2026-05-27 from the Sunset Blvd WeHo store
(Sweetgreen 8570 Sunset). Hand-transcribed from images at
`data/sweetgreen_byo/IMG_316{5..72}.png`.

Why BYO matters: pre-designed dishes only expose ingredients that appear
in current recipes. BYO is the authoritative list of in-stock SKUs at
this store, including items not in any current dish (bread, hard-boiled
egg, umami seasoning, ...) AND real-time availability (avocado currently
out of stock).

Idempotent: rebuilds all sweetgreen_byo_* rows on each run.

Usage:
    python scripts/load_sweetgreen_byo.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hermes.db import init_db  # noqa: E402

LOCATION = "Sweetgreen 8570 Sunset Blvd, West Hollywood (BYO captured 2026-05-27)"

# (item, available) — available defaults to True
BASES = [
    ("golden quinoa", True),
    ("organic shredded kale", True),
    ("chopped romaine", True),
    ("organic baby spinach", True),
    ("organic arugula", True),
    ("organic spring mix", True),
    ("white rice", True),
    ("wild rice", True),
]

TOPPINGS = [
    ("sesame crunch", True),
    ("corn salsa", True),
    ("garlic breadcrumbs", True),
    ("raw carrots", True),
    ("crispy noodles", True),
    ("tomatoes", True),
    ("shredded cabbage", True),
    ("cucumbers", True),
    ("apples", True),
    ("crispy rice", True),
    ("nori sesame seasoning", True),
    ("roasted sweet potatoes", True),
    ("spicy broccoli", True),
    ("cilantro", True),
    ("chickpeas", True),
    ("pickled onions", True),
    ("basil", True),
    ("roasted almonds", True),
    ("tortilla chips", True),
    ("crispy onions", True),
]

PREMIUMS = [
    ("feta crumble", True),
    ("napa cabbage slaw", True),
    ("white cheddar", True),
    ("crumbled bacon", True),
    ("miso glazed salmon", True),
    ("roasted chicken", True),
    ("blackened chicken", True),
    ("grass-fed steak", True),
    ("hard boiled egg", True),
    ("roasted tofu", True),
    ("warm roasted sweet potatoes", True),
    ("warm portobello mix", True),
    ("avocado", True),    # was flagged UNAVAILABLE in BYO screenshot but treated as in-stock per user
    ("hummus", True),
    ("parmesan crisps", True),
    ("shaved parmesan", True),
    ("goat cheese", True),
    ("apple kimchi", True),
]

DRESSINGS = [
    ("kbbq dressing", True),
    ("garlic aioli", True),
    ("charred jalapeño ranch", True),
    ("lime cilantro jalapeño sauce", True),
    ("citrus sesame vinaigrette", True),
    ("balsamic vinaigrette", True),
    ("green goddess ranch", True),
    ("spicy cashew", True),
    ("pesto vinaigrette", True),
    ("caesar", True),
    ("hot honey mustard sauce", True),
    ("honey bbq sauce", True),
    ("sweetgreen hot sauce", True),
    ("miso sesame ginger", True),
    ("balsamic vinegar", True),
    ("extra virgin olive oil", True),
    ("lime squeeze", True),
    ("lemon squeeze", True),
    ("umami seasoning", True),
    ("crushed red pepper", True),
]

BREAD = [
    ("bread", True),
]

GROUPS = [
    ("sweetgreen_byo_base", BASES),
    ("sweetgreen_byo_topping", TOPPINGS),
    ("sweetgreen_byo_premium", PREMIUMS),
    ("sweetgreen_byo_dressing", DRESSINGS),
    ("sweetgreen_byo_bread", BREAD),
]


def ensure_available_column(conn):
    """Add `available` column to existing DBs that pre-date it."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(brand_menu_items)")}
    if "available" not in cols:
        conn.execute("ALTER TABLE brand_menu_items ADD COLUMN available INTEGER NOT NULL DEFAULT 1")


def main():
    conn = init_db()
    try:
        ensure_available_column(conn)
        conn.execute(
            "DELETE FROM brand_menu_items "
            "WHERE brand='Sweetgreen' AND category LIKE 'sweetgreen_byo_%'"
        )
        total = 0
        unavailable = 0
        for category, items in GROUPS:
            for item, available in items:
                conn.execute(
                    """INSERT INTO brand_menu_items
                       (brand, location, category, item, ingredients_text, available)
                       VALUES ('Sweetgreen', ?, ?, ?, 'BYO catalog', ?)""",
                    (LOCATION, category, item, 1 if available else 0),
                )
                total += 1
                if not available:
                    unavailable += 1
        conn.commit()
        print(f"loaded {total} BYO items ({unavailable} unavailable) into brand_menu_items")
        print("\n--- counts by category ---")
        for r in conn.execute(
            "SELECT category, COUNT(*) n, SUM(CASE WHEN available=0 THEN 1 ELSE 0 END) unavail "
            "FROM brand_menu_items WHERE brand='Sweetgreen' AND category LIKE 'sweetgreen_byo_%' "
            "GROUP BY category ORDER BY category"
        ):
            print(f"  {r['category']:<26} {r['n']:>3}  unavailable={r['unavail']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
