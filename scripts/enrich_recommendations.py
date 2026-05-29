#!/usr/bin/env python3
"""Enrich v5 dish recommendations with structured layer data.

Takes the existing data/{city}_dish_recommendations_v5.json files and joins them
against:
    data/confidence_scores.json   — 0-100 composite + components
    data/operational_lift.json    — Low/Med/High lift + portability + LTO ref
    data/brand_lto_history.json   — proven-execution badge content

Writes v6 with new fields per card:
    confidence_score:       0-100
    confidence_components:  {trend, local, maturity, feasibility, recency, lto}
    maturity_stage:         Rising / Established / Peak / Steady / Weak
    lift_tier:              low / medium / high
    rollout_portability:    national / regional
    lto_proven:             {item_name, years, status} or null
    lift_breakdown:         list of {sku, lift, portability, kind}

Usage:
    python scripts/enrich_recommendations.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

CITY_TO_LABEL = {
    "weho": "West Hollywood",
    "williamsburg": "Williamsburg",
    "mission": "Mission District",
}


def find_lto_reference(brand: str, flavor: str, lto_history: dict):
    flavor_l = flavor.lower()
    for entry in lto_history.get(brand.lower(), []):
        for tag in (entry.get("flavor_tags") or []):
            t = tag.lower()
            if t == flavor_l or t in flavor_l or flavor_l in t:
                return {
                    "item_name": entry["item_name"],
                    "years": entry["shipped_years"],
                    "current_status": entry["current_status"],
                    "scope": entry["scope"],
                    "sources": entry.get("sources", []),
                }
    return None


def enrich_card(card: dict, city_label: str, conf: dict, lift: dict,
                lto_history: dict) -> dict:
    brand = card["brand"]
    flavor = card.get("signal_term") or card.get("target_flavor")
    if not flavor:
        return card

    c = conf.get(city_label, {}).get(flavor, {}).get(brand, {})
    l = lift.get(brand, {}).get(flavor, {})
    lto_ref = find_lto_reference(brand, flavor, lto_history)

    card["confidence_score"] = c.get("score")
    card["confidence_components"] = c.get("components")
    card["maturity_stage"] = c.get("maturity_stage")
    card["evidence_counts"] = c.get("evidence")
    card["lift_tier"] = l.get("lift")
    card["rollout_portability"] = l.get("portability")
    card["lift_rationale"] = l.get("rationale")
    card["lift_breakdown"] = l.get("missing_skus")
    card["lto_proven"] = lto_ref
    return card


def main():
    conf = json.loads((DATA / "confidence_scores.json").read_text())
    lift = json.loads((DATA / "operational_lift.json").read_text())
    lto_history = {k: v for k, v in
                   json.loads((DATA / "brand_lto_history.json").read_text()).items()
                   if not k.startswith("_")}

    for city_key, city_label in CITY_TO_LABEL.items():
        # Prefer v8 (tool-use agent) → v7 (LTO-aware prompt) → v5 (pre-LTO)
        src_v8 = DATA / f"{city_key}_dish_recommendations_v8.json"
        src_v7 = DATA / f"{city_key}_dish_recommendations_v7.json"
        src_v5 = DATA / f"{city_key}_dish_recommendations_v5.json"
        if src_v8.exists():
            src = src_v8
        elif src_v7.exists():
            src = src_v7
        elif src_v5.exists():
            src = src_v5
        else:
            print(f"Skip {city_key} — no v8, v7, or v5 found.")
            continue
        cards = json.loads(src.read_text())
        enriched = [enrich_card(c, city_label, conf, lift, lto_history)
                    for c in cards]
        # Maturity-aware ordering: highest confidence first. Confidence already
        # weights maturity (Rising 95 > Established 85 > Steady 65 > Peak 40 > Weak 20).
        # Stable secondary sort: ship_now before gap_fill at equal confidence.
        TYPE_RANK = {"ship_now": 0, "gap_fill": 1}
        enriched.sort(key=lambda c: (
            -(c.get("confidence_score") or 0),
            TYPE_RANK.get(c.get("type"), 99),
        ))
        out = DATA / f"{city_key}_dish_recommendations_v6.json"
        out.write_text(json.dumps(enriched, indent=2))
        print(f"Wrote {out} ({len(enriched)} cards)")

    # Hero verification
    print()
    print("HERO CARD (Mission al pastor, Chipotle):")
    cards = json.loads((DATA / "mission_dish_recommendations_v6.json").read_text())
    for c in cards:
        if c["brand"] == "Chipotle" and c.get("signal_term") == "al pastor":
            print(json.dumps({
                "dish_name": c.get("dish_name"),
                "confidence_score": c.get("confidence_score"),
                "maturity_stage": c.get("maturity_stage"),
                "lift_tier": c.get("lift_tier"),
                "rollout_portability": c.get("rollout_portability"),
                "lto_proven": c.get("lto_proven"),
                "evidence_counts": c.get("evidence_counts"),
            }, indent=2))


if __name__ == "__main__":
    main()
