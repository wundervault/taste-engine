#!/usr/bin/env python3
"""Auto-tag every pantry SKU with a cuisine family.

Two-pass:
  Pass 1 — vocab flavor substring match. If a pantry item name contains a vocab
           flavor as a substring, inherit that vocab flavor's family.
           e.g. "miso glazed salmon" → miso family (Japanese)
                "harissa honey chicken" → harissa family (Mediterranean)
                "pesto vinaigrette" → pesto family (Italian)
  Pass 2 — keyword heuristic for items missed by Pass 1. Compact rule set tuned
           for Chipotle / CAVA / Sweetgreen pantries.
  Pass 3 — fallback "universal" for generic items (rice, beans, lettuce, etc.)

Writes:
    data/pantry_sku_families.json — { sku_name_lower: {primary, source, [secondary]} }

Usage:
    python scripts/tag_pantry_families.py
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from hermes.db import DB_PATH  # noqa: E402

DATA = ROOT / "data"

# Heuristic keyword → family mapping (case-insensitive substring match).
# Used as Pass 2 fallback.
KEYWORD_FAMILIES = {
    "mexican": [
        "queso", "salsa", "guac", "guacamole", "tortilla", "barbacoa",
        "sofrito", "chipotle", "lime", "cilantro", "chorizo", "carnitas",
        "asada", "elote", "jalapeño", "jalapeno", "poblano", "habanero",
        "achiote", "pollo", "fajita", "burrito", "taco", "chimichurri",
        "pinto", "black beans", "ranchero",
    ],
    "mediterranean": [
        "feta", "olive", "olives", "pita", "hummus", "tzatziki", "skhug",
        "saffron", "lemon herb", "lemon-herb", "balsamic", "kale",
        "supergreens", "mediterranean", "yogurt dill",
    ],
    "levantine": [
        "tahini", "sumac", "za'atar", "zaatar", "labneh", "shawarma",
        "lentil", "lentils", "chickpea", "chickpeas", "falafel", "freekeh",
        "lamb", "harissa",
    ],
    "italian": [
        "pesto", "parmesan", "mozzarella", "burrata", "calabrian", "basil",
        "pomodoro", "marinara", "balsamic", "italian", "prosciutto",
        "focaccia", "panini",
    ],
    "japanese": [
        "miso", "yuzu", "matcha", "furikake", "nori", "dashi", "bonito",
        "tonkotsu", "ginger", "sesame ginger", "wasabi", "japanese",
        "kabocha",
    ],
    "korean": [
        "kbbq", "korean", "gochujang", "kimchi", "bulgogi", "kalbi",
        "galbi", "banchan", "ssamjang", "ssam", "apple kimchi",
    ],
    "caribbean": [
        "jerk", "scotch bonnet", "plantain", "oxtail", "allspice",
        "callaloo", "curry goat",
    ],
    "chinese": [
        "bao", "steamed bun", "five spice", "hoisin", "oyster sauce",
        "sichuan", "szechuan",
    ],
    "indian": [
        "masala", "tikka", "tandoori", "garam", "curry powder", "ghee",
    ],
    "french": [
        "dijon", "tarragon", "shallot", "beurre", "béarnaise", "remoulade",
    ],
    "premium_universal": [
        "truffle", "caviar", "foie gras",
    ],
    "plant_forward": [
        "tofu", "edamame", "tempeh", "seitan", "lentil", "lentils",
        "chickpea", "chickpeas", "cauliflower", "broccoli", "brussels",
        "kale", "spinach", "arugula", "quinoa", "farro", "freekeh",
        "wild rice", "brown rice", "supergreens", "kale", "power greens",
        "mixed greens", "romaine", "butter lettuce",
    ],
}

# Universal/structural items that pair with anything — bases, vessels, condiments,
# common proteins, dairy, produce, and generic preparations.
UNIVERSAL_KEYWORDS = [
    # bases + grains + legumes
    "rice", "beans", "lettuce", "greens", "spinach", "wild rice",
    "spring mix", "salad", "salad mix", "mixed",
    # produce
    "tomato", "tomatoes", "onion", "onions", "cucumber", "cucumbers",
    "avocado", "apples", "apple", "carrot", "carrots", "raw carrots",
    "broccoli", "portobello", "mushroom", "mushrooms", "warm portobello",
    "cabbage", "napa cabbage", "slaw", "napa", "raw",
    # dressings + oils + acids
    "vinaigrette", "dressing", "vinegar", "oil", "olive oil",
    "honey", "lemon", "lime", "salt", "pepper", "garlic",
    "salt and pepper", "ranch", "caesar", "green goddess",
    # vessels + structural
    "chips", "bowl", "wrap", "wheat", "bread", "pita", "wrap",
    # common dairy not specific to a cuisine
    "sour cream", "white cheddar", "cheddar", "goat cheese",
    # common proteins not specific to a cuisine
    "chicken", "blackened chicken", "steak", "grass-fed steak",
    "beef", "pork", "salmon", "glazed salmon", "egg", "eggs",
    "hard boiled egg", "hard boiled",
    # common preps
    "pickled", "fresh", "roasted", "grilled", "baked",
    "shredded", "diced", "crispy", "crumbled", "crumbled bacon",
    "bacon",
    # textural / generic
    "crispy noodles", "noodles", "crunch", "sesame crunch",
    "spicy cashew", "cashew", "hot sauce", "sweetgreen hot sauce",
    "seasoning", "umami seasoning", "strawberry sesame",
]


def load_flavor_families() -> dict:
    raw = json.loads((DATA / "flavor_cuisine_families.json").read_text())
    return raw["families"]


def fetch_pantry_skus() -> list[str]:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT DISTINCT item FROM brand_menu_items "
        "WHERE available = 1 AND category != 'dish' "
        "ORDER BY item"
    ).fetchall()
    return [r["item"] for r in rows]


def family_for_sku(name: str, vocab_families: dict) -> dict:
    """Returns {primary, source, secondary?} or universal fallback."""
    n = name.lower()

    # Pass 1: vocab flavor substring match
    matched_vocab = []
    for vocab, spec in vocab_families.items():
        # Word-boundary match to avoid "mole" matching "guacamole"
        if re.search(rf"\b{re.escape(vocab)}\b", n):
            matched_vocab.append((vocab, spec))
    if matched_vocab:
        # If multiple vocab flavors match, take the longest (more specific)
        matched_vocab.sort(key=lambda x: -len(x[0]))
        vocab, spec = matched_vocab[0]
        return {
            "primary": spec["primary"],
            "secondary": spec.get("secondary") or [],
            "source": f"vocab:{vocab}",
        }

    # Pass 2: keyword heuristic
    matches = []
    for family, keywords in KEYWORD_FAMILIES.items():
        for kw in keywords:
            if re.search(rf"\b{re.escape(kw)}\b", n):
                matches.append((family, kw, len(kw)))
    if matches:
        matches.sort(key=lambda x: -x[2])  # longest keyword wins
        family, kw, _ = matches[0]
        return {
            "primary": family,
            "secondary": list({m[0] for m in matches if m[0] != family}),
            "source": f"keyword:{kw}",
        }

    # Pass 3: universal — pairs with any family
    for kw in UNIVERSAL_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", n):
            return {"primary": "universal", "source": f"universal:{kw}"}

    return {"primary": "unclassified", "source": "fallback"}


def main():
    vocab_families = load_flavor_families()
    skus = fetch_pantry_skus()

    out = {}
    by_family = {}
    unclassified = []
    for sku in skus:
        spec = family_for_sku(sku, vocab_families)
        out[sku.lower()] = spec
        fam = spec["primary"]
        by_family.setdefault(fam, []).append(sku)
        if fam == "unclassified":
            unclassified.append(sku)

    target = DATA / "pantry_sku_families.json"
    target.write_text(json.dumps({
        "_meta": {
            "schema_version": 1,
            "description": "Cuisine family tag per pantry SKU. 'universal' pairs "
                           "with any family (rice, beans, lettuce, etc.). "
                           "'unclassified' need hand review. Tagged via two passes: "
                           "vocab flavor substring + keyword heuristic.",
            "total_skus": len(skus),
            "by_family_count": {k: len(v) for k, v in by_family.items()},
        },
        "skus": out,
    }, indent=2))

    print(f"Tagged {len(skus)} pantry SKUs.")
    print()
    print("Distribution by family:")
    for fam in sorted(by_family, key=lambda f: -len(by_family[f])):
        print(f"  {fam:<20s} {len(by_family[fam])}")
    if unclassified:
        print(f"\nUnclassified ({len(unclassified)}) — may need hand review:")
        for sku in unclassified[:40]:
            print(f"  {sku}")
        if len(unclassified) > 40:
            print(f"  ... and {len(unclassified) - 40} more")

    print(f"\nWrote {target}")


if __name__ == "__main__":
    main()
