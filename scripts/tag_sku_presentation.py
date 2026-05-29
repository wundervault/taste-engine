#!/usr/bin/env python3
"""Tag each pantry SKU with headline_terms vs background_terms.

The visible part of the SKU's name (everything outside parentheticals) is the
headline — flavors a customer sees and tastes as distinct elements.

Parenthetical content describes marinade components, LTO notes, or process
details — these are background and may NOT appear in a dish name as if they
were headline ingredients.

For example:
  "chicken al pastor (LTO — pineapple, achiote, chipotle)"
    headline_terms:   ["chicken al pastor", "al pastor", "chicken"]
    background_terms: ["pineapple", "achiote", "chipotle"]

  A dish that names "Al Pastor Pineapple Bowl" is misleading unless there's
  also a SEPARATE pineapple-headline SKU (e.g., "charred pineapple salsa")
  in the dish.

Writes:
    data/pantry_sku_presentation.json
        { sku_lower: {headline_terms, background_terms, presentation, raw} }

Usage:
    python scripts/tag_sku_presentation.py
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

# Words that get stripped from headline before extracting flavor tokens
HEADLINE_NOISE = {
    "lto", "limited", "fresh", "organic", "antibiotic", "free",
    "grass", "fed", "wild", "caught", "responsibly", "raised",
    "humanely", "naturally", "no", "added", "extra", "made",
    "with", "and", "or", "the", "a", "an", "of", "for", "by",
    "to", "from", "on", "in", "at", "is", "are", "be",
}


def fetch_pantry_skus() -> list[tuple[str, str]]:
    """Returns [(brand, item), ...] for all available pantry rows except dishes."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT brand, item FROM brand_menu_items "
        "WHERE available = 1 AND category != 'dish' "
        "ORDER BY brand, item"
    ).fetchall()
    return [(r["brand"], r["item"]) for r in rows]


def split_headline_background(item: str) -> tuple[str, list[str]]:
    """Returns (headline_text, [background_components])."""
    # Pull all parenthetical groups
    parens = re.findall(r"\(([^)]*)\)", item)
    # Headline is everything outside parentheses
    headline = re.sub(r"\([^)]*\)", " ", item).strip()
    headline = re.sub(r"\s+", " ", headline)
    # Background components are comma/dash-separated inside parens
    bg = []
    for p in parens:
        parts = re.split(r"[,—–\-]+", p)
        for part in parts:
            part = part.strip().lower()
            # Drop pure noise tokens
            if not part or part in HEADLINE_NOISE:
                continue
            # Drop tokens like "lto" or version markers
            if part in ("lto", "limited time", "limited"):
                continue
            # Drop multi-noise-word tokens like "limited time"
            if all(w in HEADLINE_NOISE or w in ("lto", "limited", "time")
                   for w in part.split()):
                continue
            bg.append(part)
    return headline, bg


# Cross-language / cross-cuisine equivalents. A SKU whose headline contains
# the LHS term also delivers the RHS term (and vice versa). Used so a dish
# named "Furikake Bowl" passes the audit when the brand stocks
# "nori sesame seasoning" — they are the same culinary element.
SKU_ALIASES = {
    "nori sesame seasoning": ["furikake"],
    "furikake":              ["nori sesame seasoning", "nori sesame"],
    "nori sesame":           ["furikake"],
    "yogurt dill":           ["tzatziki"],
    "tzatziki":              ["yogurt dill"],
    "miso sesame ginger":    ["miso dressing", "miso vinaigrette"],
    "spicy cashew":          ["cashew sauce", "cashew dressing"],
    "skhug":                 ["zhug", "schug"],
    "labneh":                ["strained yogurt"],
}


def extract_headline_tokens(headline: str) -> list[str]:
    """Returns flavor/ingredient tokens from the headline portion.

    Includes the full headline phrase + each bigram + each unigram (minus
    noise words). Downstream matching uses substring containment, so
    over-extraction is safe.
    """
    h = headline.lower()
    tokens = set()
    # Full phrase
    tokens.add(h)
    # Strip noise words then add the cleaned phrase
    words = [w for w in re.findall(r"[a-z']+", h) if w not in HEADLINE_NOISE]
    if words:
        tokens.add(" ".join(words))
    # Each individual word (excluding noise)
    for w in words:
        tokens.add(w)
    # Bigrams
    for i in range(len(words) - 1):
        tokens.add(f"{words[i]} {words[i+1]}")
    return sorted(t for t in tokens if t)


def main():
    skus = fetch_pantry_skus()
    out = {}
    for brand, item in skus:
        headline, background = split_headline_background(item)
        headline_terms = extract_headline_tokens(headline)
        # Merge alias terms: if headline matches any alias key, add the alias
        # value(s) as additional headline terms.
        extra = []
        for alias_key, alias_vals in SKU_ALIASES.items():
            if alias_key in headline.lower() or any(alias_key in t for t in headline_terms):
                extra.extend(alias_vals)
        if extra:
            headline_terms = sorted(set(headline_terms) | set(extra))
        out[item.lower()] = {
            "brand": brand,
            "raw": item,
            "headline_text": headline,
            "headline_terms": headline_terms,
            "background_terms": background,
            "presentation": "headline",
        }

    # Examples worth verifying
    examples = [
        "chicken al pastor (lto — pineapple, achiote, chipotle)",
        "miso glazed salmon",
        "pesto vinaigrette",
        "harissa honey chicken",
        "miso sesame ginger",
        "chicken al pastor",
    ]
    print("Sample tags:")
    for ex in examples:
        if ex in out:
            spec = out[ex]
            print(f"\n  {ex}")
            print(f"    headline_terms:   {spec['headline_terms']}")
            print(f"    background_terms: {spec['background_terms']}")

    target = DATA / "pantry_sku_presentation.json"
    target.write_text(json.dumps({
        "_meta": {
            "schema_version": 1,
            "description": "Per pantry SKU: headline_terms (visible/tasted as "
                           "distinct elements) vs background_terms (marinade or "
                           "embedded components named only in parentheticals). "
                           "Used by dish-name truthfulness audit.",
            "total_skus": len(out),
            "skus_with_background": sum(1 for v in out.values() if v["background_terms"]),
        },
        "skus": out,
    }, indent=2))
    print(f"\nTagged {len(out)} pantry SKUs.")
    bg_count = sum(1 for v in out.values() if v["background_terms"])
    print(f"  {bg_count} SKUs have background-only components (parenthetical marinades/notes).")
    print(f"Wrote {target}")


if __name__ == "__main__":
    main()
