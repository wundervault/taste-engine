#!/usr/bin/env python3
"""Post-generation comp restaurant verification.

The dish_generator's comp_context, signal_rank_note, and dish_potential fields
often name specific indie restaurants as benchmarks (e.g., "El Farolito and
La Taqueria define al pastor in the Mission"). This audit extracts those
named restaurants and checks each against the city's restaurants table.

Three possible verdicts per named restaurant:
  verified           — name found in our scrape (high confidence: review data backs the claim)
  external_reference — name is real (per LLM training) but not in our scrape
                       (medium confidence: not invented but unverified)
  hallucination_risk — name pattern looks like a restaurant but doesn't match
                       any known landmark in this neighborhood

For hackathon scope: we only enforce the verified vs external split. Both
are acceptable; the dashboard surfaces verification status as a credibility marker.

Reads:
    data/{city}_dish_recommendations_v6.json
    data/hermes.db

Writes:
    data/comp_restaurant_audit.json

Usage:
    python scripts/audit_comp_restaurants.py
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

CITY_TO_LABEL = {
    "weho": "West Hollywood",
    "williamsburg": "Williamsburg",
    "mission": "Mission District",
}

# Words that often follow a restaurant name but aren't part of it
TRAILING_NOISE = {
    "is", "are", "has", "have", "was", "were", "in", "on", "at", "near",
    "with", "and", "but", "the", "their", "its", "for", "to", "of",
}

# Generic words that look capitalized in context but aren't restaurant names.
# Massively expanded to filter out: brand names, neighborhoods, cuisines,
# sentence-starting verbs/adjectives, dish-component terms, abbreviations.
GENERIC_NAMES = {
    # Target brands
    "Chipotle", "CAVA", "Sweetgreen", "Chipotle's", "CAVA's", "Sweetgreen's",
    # Neighborhoods + cities
    "Mission", "Mission's", "Williamsburg", "Williamsburg's", "Brooklyn",
    "Manhattan", "Hollywood", "WeHo", "WeHo's", "LA", "NYC", "SF",
    "San Francisco", "Los Angeles", "New York", "California",
    "Sunset", "Strip", "Eastside", "Westside",
    # Cuisines / cuisine adjectives
    "Mexican", "Italian", "Japanese", "Korean", "Caribbean",
    "Mediterranean", "Levantine", "Israeli", "Lebanese", "Chinese",
    "Halal", "Kosher", "Oaxacan", "East Asian", "Pan-Asian",
    "American", "French", "Indian", "Thai", "Vietnamese", "Filipino",
    # Days/months/seasons
    "Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
    "Summer", "Spring", "Winter", "Fall", "Autumn",
    # Abbreviations + business terms
    "LTO", "LTOs", "SKU", "SKUs", "KBBQ", "BYO", "DMA", "POS", "QSR",
    "BBQ", "TBD", "ETA", "ROI", "MVP",
    # Dish names that look capitalized
    "Mole Burrito", "Mole Chicken Bowl", "Crispy Rice Bowl",
    "Miso Glazed Salmon", "Miso Glazed Salmon Plate",
    "Hot Honey Chicken", "Hot Honey Chicken Plate",
    "Harissa Cauliflower Bowl", "Harissa Lentil Bowl",
    # Sentence-starting verbs / adjectives (common in LLM prose)
    "Adding", "Distinct", "Every", "Pairs", "Opens", "Medium", "Low",
    "High", "Signal", "Roasted", "Grilled", "Crispy", "Crunchy",
    "Sweet", "Spicy", "Cold", "Hot", "Warm", "Fresh", "Bold",
    "Notable", "Strong", "Weak", "Established", "Rising", "Peak", "Steady",
    "Confidence", "Maturity", "Lift", "Portability", "Source",
    # Single-word food terms that are too generic
    "Miso", "Pesto", "Harissa", "Tahini", "Sumac", "Lentils", "Lentil",
    "Cauliflower", "Salmon", "Chicken", "Beef", "Pork", "Steak", "Tofu",
    "Mushroom", "Mushrooms", "Burrito", "Taco", "Tacos", "Bowl", "Plate",
    "Plates", "Pita", "Wrap", "Salad", "Side", "Sides", "Dressing",
    "Vinaigrette", "Crunch", "Honey", "Mole", "Jerk", "Truffle",
    "Tahini", "Sumac", "Cheese", "Greens", "Rice", "Beans", "Bread",
    # Common LLM-prose phrasings
    "Patacon", "Patacón", "Pa", "Muertos", "Muertos LTO",
    "Dia", "Día", "Cinco", "Mayo", "Halloween", "Thanksgiving",
    # Stand-alone determiners that get capitalized
    "Both", "Either", "Neither", "Most", "Many", "Several", "Few",
    # Cuisines stand-alone phrases
    "Korean BBQ", "Korean Bbq", "BBQ", "Texas BBQ",
    # Additional sentence-mid noise found in audit
    "Active LTO", "Pastor", "Farolito", "Asian", "Sesame", "Kitchen",
    "Glazed", "Shawarma", "Cauliflower Shawarma", "Melrose",
    "Sweetgreen's Miso Glazed Salmon", "Urbana",
    "Active", "Direct", "Pairs", "Adds", "Drops", "Slots", "Drives",
    "Catches", "Opens", "Unlocks", "Builds", "Combines", "Includes",
    "Sweet", "Sour", "Sharp", "Tangy", "Smoky", "Earthy", "Rich",
    "Acceptable", "Recognized", "Established", "Approved", "Available",
    "East", "West", "North", "South", "Upper", "Lower",
    "Pre", "Pre-", "Post", "Post-",
    "Slaw", "Cabbage", "Broccoli", "Avocado", "Tomato", "Onion",
    "Pickled", "Roasted", "Charred", "Crispy",
    "Achiote", "Chipotle", "Pineapple", "Adobo", "Allspice",
}

# Restaurant-name patterns: prefer multi-word names with telltale markers
# (Spanish/Italian articles, restaurant-type suffixes, or 2+ capitalized words).
NAME_PATTERNS = [
    # Spanish/Italian/French article + capitalized noun (1-3 follow words)
    re.compile(r"\b(?:El|La|Los|Las|Le|Les|Il|Lo|Gli)\s+[A-Z][a-zA-Z'áéíóúñ]+(?:\s+[A-Z][a-zA-Z'áéíóúñ]+){0,2}\b"),
    # Name ending in a restaurant-type word (Taqueria, Cafe, Kitchen, etc.)
    re.compile(r"\b(?:[A-Z][a-zA-Z'áéíóúñ]+\s+){1,3}(?:Taqueria|Taquería|Cafe|Café|Kitchen|Bistro|Cantina|Grill|Bakery|Pizzeria|Trattoria|Restaurant|Diner|Tavern|Bar|Eatery|Pub|Steakhouse|Brewery)\b"),
    # 2+ capitalized words in sequence (likely a proper noun phrase)
    re.compile(r"\b[A-Z][a-zA-Z'áéíóúñ]{2,}\s+[A-Z][a-zA-Z'áéíóúñ]{2,}(?:\s+[A-Z][a-zA-Z'áéíóúñ]+){0,2}\b"),
    # Single distinctive proper noun (5+ chars, not in generic list)
    re.compile(r"\b[A-Z][a-zA-Z'áéíóúñ]{4,}\b"),
]


def extract_restaurant_names(text: str) -> list[str]:
    if not text:
        return []
    found = set()
    # Track positions where a capitalized word begins a sentence — those are
    # most likely verbs/adjectives, not proper nouns.
    sentence_start_positions = set()
    for m in re.finditer(r"(?:^|[.!?]\s+)([A-Z])", text):
        sentence_start_positions.add(m.start(1))

    for pat in NAME_PATTERNS:
        for m in pat.finditer(text):
            name = m.group(0).strip()
            # Strip trailing noise words
            words = name.split()
            while words and words[-1].lower() in TRAILING_NOISE:
                words.pop()
            name = " ".join(words)
            if not name or name in GENERIC_NAMES:
                continue
            if len(name) < 5:
                continue
            # Filter: any individual word in this name that's a generic must
            # not be the only word (single-word matches that are generic = drop)
            if len(words) == 1 and name in GENERIC_NAMES:
                continue
            # Single-word match at the start of a sentence is very likely a
            # verb/adjective like "Distinct" or "Adding"
            if len(words) == 1 and m.start() in sentence_start_positions:
                continue
            # Drop names that are entirely composed of generic tokens
            if all(w in GENERIC_NAMES or w.lower() in TRAILING_NOISE
                   for w in words):
                continue
            found.add(name)
    return sorted(found)


def load_city_restaurants(db, city_label: str) -> list[str]:
    return [r["name"] for r in db.execute(
        "SELECT name FROM restaurants WHERE city = ?", (city_label,)
    )]


def verify(name: str, city_restaurants: list[str]) -> tuple[str, str | None]:
    """Returns (status, matched_name). status ∈ {verified, external_reference}.

    Tight match: only verify if the name appears as a contiguous substring
    inside the restaurant name (lowercased). This avoids single-word "Asian"
    matching "Saucy Asian" or "Sesame" matching "Open Sesame"."""
    lower = name.lower().strip()
    for r in city_restaurants:
        rl = r.lower()
        if lower == rl:
            return "verified", r
        # Require contiguous substring match with word boundary
        if re.search(rf"\b{re.escape(lower)}\b", rl):
            # And the matched name must be multi-word OR a long unique single token
            if len(lower.split()) >= 2 or len(lower) >= 7:
                return "verified", r
    return "external_reference", None


def audit_card(card: dict, city_restaurants: list[str]) -> dict:
    fields_to_check = ["comp_context", "signal_rank_note", "dish_potential",
                       "operational_lift", "novelty_check", "confidence_reason"]
    extracted = set()
    for field in fields_to_check:
        text = card.get(field) or ""
        for name in extract_restaurant_names(text):
            extracted.add(name)

    verifications = []
    for name in sorted(extracted):
        status, matched = verify(name, city_restaurants)
        verifications.append({
            "name": name, "status": status, "matched_in_db": matched,
        })

    verified_count = sum(1 for v in verifications if v["status"] == "verified")
    external_count = sum(1 for v in verifications if v["status"] == "external_reference")

    return {
        "total_named": len(verifications),
        "verified_count": verified_count,
        "external_count": external_count,
        "all_verifications": verifications,
        "verdict": "OK" if not verifications or verified_count > 0 else "ALL_EXTERNAL",
    }


def main():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    out = {}
    for city_key, city_label in CITY_TO_LABEL.items():
        path = DATA / f"{city_key}_dish_recommendations_v6.json"
        if not path.exists():
            continue
        cards = json.loads(path.read_text())
        restaurants = load_city_restaurants(db, city_label)
        city_audit = []
        for card in cards:
            audit = audit_card(card, restaurants)
            city_audit.append({
                "brand": card["brand"],
                "type": card["type"],
                "dish_name": card.get("dish_name") or f'gap_fill: {card.get("target_flavor")}',
                "audit": audit,
            })
        out[city_key] = city_audit

    target = DATA / "comp_restaurant_audit.json"
    target.write_text(json.dumps(out, indent=2))
    print(f"Wrote {target}\n")

    # Summary
    total_named = 0
    total_verified = 0
    total_external = 0
    print("AUDIT SUMMARY:")
    for city, audits in out.items():
        print(f"\n{city.upper()}:")
        for a in audits:
            au = a["audit"]
            total_named += au["total_named"]
            total_verified += au["verified_count"]
            total_external += au["external_count"]
            if au["total_named"] == 0:
                print(f"  {a['brand']:<10s} {a['dish_name']:<45s} (no comp restaurants named)")
                continue
            print(f"  {a['brand']:<10s} {a['dish_name']:<45s} "
                  f"named={au['total_named']:>2} "
                  f"verified={au['verified_count']:>2} "
                  f"external={au['external_count']:>2}")
            for v in au["all_verifications"]:
                if v["status"] == "verified":
                    print(f"      ✓ {v['name']:<25s} → {v['matched_in_db']}")
                else:
                    print(f"      ⚠ {v['name']:<25s} (external reference — not in our scrape)")
    print()
    print(f"OVERALL: {total_named} restaurants named across all cards. "
          f"{total_verified} verified in scrape. {total_external} external references.")


if __name__ == "__main__":
    main()
