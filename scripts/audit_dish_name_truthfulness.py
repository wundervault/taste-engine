#!/usr/bin/env python3
"""Post-generation dish-name truthfulness audit.

Every flavor/ingredient token in a dish name must point to a HEADLINE ingredient
in the dish's ingredients list. A token that is only delivered via a background
component (parenthetical marinade) makes the dish name misleading.

Example violation:
  Dish name: "Al Pastor Pineapple Crunch Taco Plate"
  Ingredients include: "chicken al pastor (LTO — pineapple, achiote, chipotle)"
  Audit: "al pastor" → headline ✓
         "pineapple"  → background only (only in marinade), no headline pineapple
                        ingredient on plate → MISLEADING
         "crunch"     → texture promise, needs crispy/crunchy ingredient
         "taco/plate" → vessel/form stop words, ignored

Reads:
    data/{city}_dish_recommendations_v6.json
    data/pantry_sku_presentation.json

Writes:
    data/name_truthfulness_audit.json

Usage:
    python scripts/audit_dish_name_truthfulness.py
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CITIES = ["weho", "williamsburg", "mission"]

# Words to ignore in dish names — vessels, forms, generic adjectives
STOP_WORDS = {
    "bowl", "plate", "plates", "taco", "tacos", "burrito", "burritos",
    "wrap", "wraps", "salad", "salads", "sandwich", "sandwiches",
    "pita", "pitas", "platter", "platters", "dish", "dishes", "special",
    "specials", "side", "sides", "lunch", "dinner", "brunch", "breakfast",
    "the", "a", "an", "and", "or", "of", "with", "for", "by", "on", "in", "to",
    "style", "inspired", "twist", "signature", "limited", "edition",
    "fresh", "organic", "healthy", "wholesome", "premium", "deluxe",
    "+", "&", "-", "—", "–",
}

# Texture promises — must be delivered by a crispy/crunchy/crusty ingredient
TEXTURE_WORDS = {
    "crunch": ("crispy", "crunch", "crunchy", "crusty"),
    "crunchy": ("crispy", "crunch", "crunchy", "crusty"),
    "crispy": ("crispy", "crunch", "crunchy", "crusty", "fried"),
    "crisp": ("crispy", "crunch", "crunchy", "crusty"),
}

# Prep promises — must be delivered by a matching prep word
PREP_WORDS = {
    "grilled": ("grilled", "char-grilled", "charred"),
    "roasted": ("roasted", "char-roasted"),
    "smoked": ("smoked"),
    "braised": ("braised", "braise"),
    "fried": ("fried", "crispy"),
    "charred": ("charred", "grilled"),
    "pickled": ("pickled",),
    "glazed": ("glazed",),
}


def tokenize_dish_name(name: str) -> tuple[list[str], list[str]]:
    """Returns (unigrams, bigrams) lowercased, with stop words removed."""
    raw = re.sub(r"[^a-zA-Z'\s+&-]+", " ", name.lower())
    words = [w for w in re.findall(r"[a-z']+", raw) if w not in STOP_WORDS]
    bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words) - 1)]
    return words, bigrams


def load_sku_presentation() -> dict:
    return json.loads((DATA / "pantry_sku_presentation.json").read_text())["skus"]


def find_in_ingredients(token: str, ingredients: list[str],
                        sku_table: dict) -> tuple[str, str | None]:
    """Search the dish's ingredients for the token.

    Returns ('headline', sku) if matched as headline, ('background', sku) if
    only as background, ('miss', None) if not found anywhere.
    """
    # Pass 1: headline match
    for ing in ingredients:
        spec = sku_table.get(ing.lower())
        if not spec:
            # Try contains-match fallback
            for k, v in sku_table.items():
                if k in ing.lower() or ing.lower() in k:
                    spec = v
                    break
        if spec and any(token in t or t == token for t in spec["headline_terms"]):
            return "headline", ing
    # Pass 2: background match
    for ing in ingredients:
        spec = sku_table.get(ing.lower())
        if not spec:
            for k, v in sku_table.items():
                if k in ing.lower() or ing.lower() in k:
                    spec = v
                    break
        if spec and any(token in t or t == token for t in spec["background_terms"]):
            return "background", ing
    # Pass 3: textual contains match against raw ingredient strings (catches
    # cases where the SKU isn't in our pantry table but the dish includes a
    # one-off ingredient).
    for ing in ingredients:
        if token in ing.lower():
            # Determine headline/background by parenthetical position
            paren = re.search(r"\(([^)]*)\)", ing)
            if paren and token in paren.group(1).lower():
                return "background", ing
            return "headline", ing
    return "miss", None


def audit_dish_name(dish_name: str, ingredients: list[str],
                    sku_table: dict) -> dict:
    """Check each significant unigram in the dish name. Multi-word headline
    items like 'al pastor' are matched via the SKU's headline_terms which
    include the multi-word phrase + each component. Bigrams from the dish
    name are NOT treated as separate claims — they only catch the cases where
    two adjacent words in the dish name happen to form a known compound flavor
    (e.g., "al pastor" or "hot honey")."""
    unigrams, _ = tokenize_dish_name(dish_name)

    # First, see which adjacent bigrams correspond to known multi-word vocab
    # flavors or pantry SKU headline terms. If yes, mark the component unigrams
    # as covered by that bigram.
    words = unigrams
    covered_by_bigram = set()
    bigram_results = []
    for i in range(len(words) - 1):
        bg = f"{words[i]} {words[i+1]}"
        kind, sku = find_in_ingredients(bg, ingredients, sku_table)
        if kind == "headline":
            covered_by_bigram.add(i)
            covered_by_bigram.add(i + 1)
            bigram_results.append({"token": bg, "kind": "flavor",
                                   "delivered_by": sku})

    violations = []
    confirmed = list(bigram_results)

    for i, token in enumerate(words):
        if i in covered_by_bigram:
            continue
        # Skip very short tokens
        if len(token) <= 2:
            continue

        # Texture words
        if token in TEXTURE_WORDS:
            accept = TEXTURE_WORDS[token]
            if any(any(a in ing.lower() for a in accept) for ing in ingredients):
                confirmed.append({"token": token, "kind": "texture",
                                  "delivered_by": "crispy/crunchy ingredient"})
            else:
                violations.append({"token": token, "kind": "unmet_texture",
                                   "issue": f"name promises '{token}' but no crispy/crunchy ingredient present"})
            continue

        # Prep words
        if token in PREP_WORDS:
            accept = PREP_WORDS[token]
            if any(any(a in ing.lower() for a in accept) for ing in ingredients):
                confirmed.append({"token": token, "kind": "prep",
                                  "delivered_by": "matching prep"})
            else:
                violations.append({"token": token, "kind": "unmet_prep",
                                   "issue": f"name promises '{token}' but no matching prep in ingredients"})
            continue

        # Flavor/ingredient tokens
        kind, sku = find_in_ingredients(token, ingredients, sku_table)
        if kind == "headline":
            confirmed.append({"token": token, "kind": "flavor",
                              "delivered_by": sku})
        elif kind == "background":
            violations.append({"token": token, "kind": "background_only",
                               "issue": f"'{token}' is only in marinade/background of '{sku}', not a headline element"})
        else:
            violations.append({"token": token, "kind": "phantom",
                               "issue": f"'{token}' in dish name but not in any ingredient"})

    verdict = "OK" if not violations else "MISLEADING"
    return {
        "dish_name": dish_name,
        "verdict": verdict,
        "violations": violations,
        "confirmed": confirmed,
    }


def main():
    sku_table = load_sku_presentation()
    out = {}
    for city in CITIES:
        path = DATA / f"{city}_dish_recommendations_v6.json"
        if not path.exists():
            continue
        cards = json.loads(path.read_text())
        city_audit = []
        for card in cards:
            if card.get("type") != "ship_now":
                continue
            audit = audit_dish_name(card.get("dish_name", ""),
                                    card.get("ingredients", []), sku_table)
            city_audit.append({
                "brand": card["brand"],
                "dish_name": card.get("dish_name"),
                "audit": audit,
            })
        out[city] = city_audit

    target = DATA / "name_truthfulness_audit.json"
    target.write_text(json.dumps(out, indent=2))
    print(f"Wrote {target}\n")

    # Summary
    print("AUDIT SUMMARY:")
    for city, audits in out.items():
        print(f"\n{city.upper()}:")
        for a in audits:
            v = a["audit"]["verdict"]
            mark = "✓" if v == "OK" else "⚠"
            print(f"  {mark} {v:<11s} {a['brand']:<11s} {a['dish_name']}")
            for viol in a["audit"]["violations"]:
                print(f"      → {viol['issue']}")


if __name__ == "__main__":
    main()
