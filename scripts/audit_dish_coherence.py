#!/usr/bin/env python3
"""Post-generation cuisine coherence audit for dish recommendations.

For each ship_now dish, this audit:
  1. Identifies the anchor flavor (signal_term — a vocab flavor)
  2. Maps each ingredient to its cuisine family via pantry_sku_families.json
  3. Checks compatibility between the anchor's families and each ingredient's
     families using check_cuisine_coherence.best_score
  4. Flags any cross-family combination below threshold 0.5 (blocked) or
     between 0.5 and 0.7 (needs justification)

For gap_fill dishes, runs the same check using target_flavor + missing_skus.

Universal-family ingredients (rice, lettuce, oil) pair with everything.

Reads:
    data/{city}_dish_recommendations_v6.json
    data/flavor_cuisine_families.json
    data/cuisine_compatibility.json
    data/pantry_sku_families.json

Writes:
    data/coherence_audit.json — per-dish report

Usage:
    python scripts/audit_dish_coherence.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
DATA = ROOT / "data"

from check_cuisine_coherence import (  # noqa: E402
    pair_score, families_for, load_compat, load_families,
    THRESHOLD_ALLOWED, THRESHOLD_JUSTIFICATION,
)


CITIES = ["weho", "williamsburg", "mission"]


def load_pantry_families() -> dict:
    raw = json.loads((DATA / "pantry_sku_families.json").read_text())
    return raw["skus"]


def families_for_pantry_item(item: str, pantry_table: dict) -> list[str]:
    """Look up family for a pantry ingredient line. Strips parentheticals
    (e.g. 'chicken al pastor (LTO — pineapple, achiote, chipotle)' → 'chicken al pastor')
    before matching, since we want the core item not the marinade notes."""
    key = re.sub(r"\([^)]*\)", "", item).strip().lower()
    spec = pantry_table.get(key)
    if not spec:
        # Try substring fallback
        for k, v in pantry_table.items():
            if k in key or key in k:
                spec = v
                break
    if not spec:
        return ["unclassified"]
    out = [spec["primary"]]
    out.extend(spec.get("secondary") or [])
    return out


def is_universal(fam: str) -> bool:
    return fam in ("universal", "plant_forward", "premium_universal", "unclassified")


def audit_dish(dish: dict, vocab_families: dict, compat: dict,
               pantry_table: dict) -> dict:
    """Returns coherence report for a single dish."""
    anchor = dish.get("signal_term") or dish.get("target_flavor") or ""
    anchor_families = families_for(anchor, vocab_families)

    if not anchor_families:
        return {"audited": False, "reason": "anchor not in vocab families"}

    # Ingredients (ship_now) or missing_skus (gap_fill)
    if dish.get("type") == "gap_fill":
        ingredients = dish.get("missing_skus", [])
    else:
        ingredients = dish.get("ingredients", [])

    incompat = []
    borderline = []
    pair_log = []

    for ing in ingredients:
        ing_families = families_for_pantry_item(ing, pantry_table)
        # If ALL of an ingredient's family memberships are universal, it pairs
        # with anything — skip the check.
        if all(is_universal(f) for f in ing_families):
            continue

        # Find best score across anchor families × ingredient families
        best = -1.0
        best_pair = ("?", "?")
        for af in anchor_families:
            for ifam in ing_families:
                if is_universal(af) or is_universal(ifam):
                    s = 0.95
                else:
                    s = pair_score(af, ifam, compat)
                if s > best:
                    best = s
                    best_pair = (af, ifam)

        pair_log.append({
            "ingredient": ing, "score": round(best, 2),
            "families": ing_families, "best_pair": best_pair,
        })

        if best < THRESHOLD_JUSTIFICATION:
            incompat.append({"ingredient": ing, "score": round(best, 2),
                             "anchor_family": best_pair[0], "ing_family": best_pair[1]})
        elif best < THRESHOLD_ALLOWED:
            borderline.append({"ingredient": ing, "score": round(best, 2),
                               "anchor_family": best_pair[0], "ing_family": best_pair[1]})

    justified = bool(dish.get("cross_family_justification"))

    return {
        "audited": True,
        "anchor": anchor,
        "anchor_families": anchor_families,
        "incompatible_ingredients": incompat,
        "borderline_ingredients": borderline,
        "cross_family_justification_present": justified,
        "verdict": (
            "BLOCKED" if incompat and not justified
            else "NEEDS_JUSTIFICATION" if borderline and not justified
            else "JUSTIFIED" if (incompat or borderline) and justified
            else "OK"
        ),
        "pair_log": pair_log,
    }


def main():
    vocab_families = load_families()
    compat = load_compat()
    pantry_table = load_pantry_families()

    out = {}
    for city in CITIES:
        path = DATA / f"{city}_dish_recommendations_v6.json"
        if not path.exists():
            continue
        cards = json.loads(path.read_text())
        city_audit = []
        for card in cards:
            audit = audit_dish(card, vocab_families, compat, pantry_table)
            city_audit.append({
                "brand": card["brand"],
                "type": card["type"],
                "dish_name": card.get("dish_name") or f'gap_fill: {card.get("target_flavor")}',
                "audit": audit,
            })
        out[city] = city_audit

    target = DATA / "coherence_audit.json"
    target.write_text(json.dumps(out, indent=2))
    print(f"Wrote {target}\n")

    # Summary
    print("AUDIT SUMMARY:")
    for city, audits in out.items():
        print(f"\n{city.upper()}:")
        for a in audits:
            verdict = a["audit"].get("verdict", "?")
            mark = {"OK": "✓", "BLOCKED": "⛔", "NEEDS_JUSTIFICATION": "⚠",
                    "JUSTIFIED": "✓"}.get(verdict, "?")
            print(f"  {mark} {verdict:<22s} {a['brand']:<11s} {a['dish_name']}")
            for inc in a["audit"].get("incompatible_ingredients", []):
                print(f"      ⛔ {inc['ingredient']:<35s} "
                      f"score={inc['score']:.2f}  "
                      f"({inc['anchor_family']}×{inc['ing_family']})")
            for b in a["audit"].get("borderline_ingredients", []):
                print(f"      ⚠ {b['ingredient']:<35s} "
                      f"score={b['score']:.2f}  "
                      f"({b['anchor_family']}×{b['ing_family']})")


if __name__ == "__main__":
    main()
