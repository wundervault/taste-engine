#!/usr/bin/env python3
"""Parse Sweetgreen's de-facto SKU pool from existing dish ingredients_text.

The 27 Sweetgreen dishes already in brand_menu_items each ship with a comma-
separated ingredient string. The union of those strings is the actually-prepped
ingredient inventory. Categorize each into base/protein/topping/dressing and
load as additional rows so dish-gen has the full constraint set.

Idempotent: deletes prior `sweetgreen_*` ingredient rows before reloading.
Leaves the 27 `category='dish'` rows untouched.

Usage:
    python scripts/extract_sweetgreen_pantry.py
"""
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hermes.db import init_db  # noqa: E402

# Pattern fragments — case-insensitive substring match against the lowercased phrase.
PROTEIN_HINTS = ("chicken", "salmon", "steak", "tofu", "shrimp", "beef",
                 "turkey", "bacon", "egg")
BASE_HINTS = ("rice", "quinoa", "kale", "romaine", "cabbage", "greens",
              "lentil", "arugula", "spinach", "farro")
DRESSING_HINTS = ("vinaigrette", "aioli", "sauce", "ranch", "dressing",
                  "caesar", "tahini", "pesto", "balsamic", "hummus",
                  "garlic aioli", "lime squeeze")

# Junk fragments seen in ingredients_text that aren't ingredients
JUNK_PREFIXES = ("wrapped", "topped", "served", "meet the", "crafted",
                 "for our", "available", "comes with")
LEGAL_MARKERS = ("trademark", "®", "™", "sweetgreen.com")


def categorize(phrase: str) -> str:
    p = phrase.lower()
    if any(h in p for h in DRESSING_HINTS):
        return "sweetgreen_dressing"
    if any(h in p for h in PROTEIN_HINTS):
        return "sweetgreen_protein"
    if any(h in p for h in BASE_HINTS):
        return "sweetgreen_base"
    return "sweetgreen_topping"


def is_junk(phrase: str) -> bool:
    p = phrase.lower().strip()
    if not p or len(p) > 60 or len(p) < 3:
        return True
    if any(p.startswith(j) for j in JUNK_PREFIXES):
        return True
    if any(m in p for m in LEGAL_MARKERS):
        return True
    # Reject phrases that are mostly punctuation or have no letters
    if not re.search(r"[a-z]", p):
        return True
    return False


def split_ingredients(text: str):
    """Split a dish's ingredient_text into atomic ingredient phrases."""
    if not text:
        return []
    # Split on commas and the word " and "
    parts = re.split(r",|\sand\s", text)
    return [p.strip() for p in parts if p.strip()]


# Marketing modifiers that don't change SKU identity — strip wherever they appear.
NOISE_WORDS = ("antibiotic-free", "organic", "seed oil-free", "grass-fed")


def normalize(phrase: str) -> str:
    """Lowercase, trim, collapse whitespace, drop marketing modifiers that
    don't change the SKU identity. Modifiers can appear anywhere in the phrase."""
    p = phrase.lower().strip().rstrip(".")
    p = re.sub(r"\s+", " ", p)
    for word in NOISE_WORDS:
        p = re.sub(rf"\b{re.escape(word)}\b\s*", "", p)
    p = re.sub(r"\s+", " ", p).strip()
    # Manual SKU aliases — Sweetgreen names the same thing two ways
    aliases = {
        "caesar": "caesar dressing",
        "crispy rice": "crispy brown rice",
        "quinoa": "golden quinoa",
        "kale": "shredded kale",
    }
    return aliases.get(p, p)


def main():
    conn = init_db()
    try:
        rows = conn.execute(
            "SELECT item, ingredients_text FROM brand_menu_items "
            "WHERE brand='Sweetgreen' AND category='dish'"
        ).fetchall()
        print(f"parsing {len(rows)} Sweetgreen dishes...")

        usage = Counter()  # phrase -> # of dishes it appears in
        for row in rows:
            seen_in_dish = set()
            for phrase in split_ingredients(row["ingredients_text"]):
                p = normalize(phrase)
                if is_junk(p) or p in seen_in_dish:
                    continue
                seen_in_dish.add(p)
                usage[p] += 1

        print(f"  extracted {len(usage)} distinct ingredient phrases")

        # Clear prior sweetgreen_* rows (idempotent rebuild of pantry rows only,
        # NOT the 'dish' rows)
        conn.execute(
            "DELETE FROM brand_menu_items "
            "WHERE brand='Sweetgreen' AND category LIKE 'sweetgreen_%'"
        )

        per_cat = Counter()
        for phrase, n in usage.items():
            cat = categorize(phrase)
            conn.execute(
                """INSERT OR IGNORE INTO brand_menu_items
                   (brand, location, category, item, ingredients_text)
                   VALUES ('Sweetgreen', NULL, ?, ?, ?)""",
                (cat, phrase, f"appears in {n} dish(es)"),
            )
            per_cat[cat] += 1

        conn.commit()

        print("\n--- pantry rows added, by category ---")
        for cat, n in per_cat.most_common():
            print(f"  {cat:<22} {n}")

        print("\n--- proteins ---")
        for r in conn.execute(
            "SELECT item, ingredients_text FROM brand_menu_items "
            "WHERE brand='Sweetgreen' AND category='sweetgreen_protein' ORDER BY item"
        ):
            print(f"  {r['item']}   ({r['ingredients_text']})")

        print("\n--- dressings/sauces ---")
        for r in conn.execute(
            "SELECT item, ingredients_text FROM brand_menu_items "
            "WHERE brand='Sweetgreen' AND category='sweetgreen_dressing' ORDER BY item"
        ):
            print(f"  {r['item']}   ({r['ingredients_text']})")

        print("\n--- bases ---")
        for r in conn.execute(
            "SELECT item FROM brand_menu_items "
            "WHERE brand='Sweetgreen' AND category='sweetgreen_base' ORDER BY item"
        ):
            print(f"  {r['item']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
