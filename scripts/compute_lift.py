#!/usr/bin/env python3
"""Operational lift classifier per (brand, flavor).

Lift tier (Low / Medium / High) reflects what it takes for a brand to ship a
flavor it doesn't currently deliver:

    Low     Seasoning/sauce remix from existing or shelf-stable additions.
    Medium  Prep workflow change OR new shelf-stable SKU. Training + procurement.
    High    New refrigerated/frozen SKU, new cooking method, or specialty regional sourcing.

Rules:
    - If brand DELIVERS the flavor today, lift = Low (it's a remix using existing pantry).
    - Otherwise, lift = max(individual missing-SKU lift tiers).
    - If brand has shipped this flavor before as an LTO (per brand_lto_history.json),
      lift is downgraded by one tier — supply chain dormant, not new.

Rollout portability (National / Regional):
    - National if ALL missing SKUs are National.
    - Regional if ANY missing SKU is Regional.
    - National automatically if brand has shipped this flavor before nationally.

Reads:
    data/pantry_fit.json
    data/sku_lift_classifications.json
    data/brand_lto_history.json

Writes:
    data/operational_lift.json — { brand: { flavor: {lift, portability, breakdown} } }

Usage:
    python scripts/compute_lift.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BRANDS = ["Chipotle", "CAVA", "Sweetgreen"]
TIER_RANK = {"low": 1, "medium": 2, "high": 3}
RANK_TIER = {v: k for k, v in TIER_RANK.items()}


def downgrade(tier: str) -> str:
    rank = TIER_RANK.get(tier, 2)
    return RANK_TIER[max(1, rank - 1)]


def classify_skus(missing_skus: list, sku_table: dict) -> list:
    out = []
    for sku in missing_skus:
        spec = sku_table.get(sku) or {"lift": "medium", "portability": "national",
                                       "kind": "unclassified"}
        out.append({"sku": sku, **spec})
    return out


def lift_for_flavor(brand: str, flavor: str, fit: dict, sku_table: dict,
                    lto_history: dict) -> dict:
    sku_details = classify_skus(fit.get("missing", []), sku_table)

    if fit.get("deliverable"):
        base_lift = "low"
        portability = "national"
        rationale = "Brand delivers this flavor from current pantry — sauce/seasoning remix."
    elif not sku_details:
        base_lift = "low"
        portability = "national"
        rationale = "No SKUs missing."
    else:
        # Max lift tier across missing SKUs
        max_rank = max(TIER_RANK[s["lift"]] for s in sku_details)
        base_lift = RANK_TIER[max_rank]
        # Portability: regional if any is regional
        portability = "regional" if any(s["portability"] == "regional"
                                        for s in sku_details) else "national"
        rationale = f"{len(sku_details)} missing SKU(s); ceiling set by " \
                    f"{', '.join(s['sku'] for s in sku_details if s['lift'] == base_lift)}."

    # LTO downgrade — match if any tag is a substring of the flavor or vice versa.
    # This catches "adobo" tag vs "adobo sauce" flavor, "hot honey" tag vs "honey", etc.
    lto_match = None
    flavor_lower = flavor.lower()
    brand_lto = lto_history.get(brand.lower(), [])
    for entry in brand_lto:
        for tag in (entry.get("flavor_tags") or []):
            t = tag.lower()
            if t == flavor_lower or t in flavor_lower or flavor_lower in t:
                lto_match = entry
                break
        if lto_match:
            break

    final_lift = base_lift
    lto_applied = False
    if lto_match and base_lift != "low":
        final_lift = downgrade(base_lift)
        lto_applied = True
        rationale += f" Downgraded from {base_lift.upper()} via LTO history " \
                     f"({lto_match['item_name']}, shipped {lto_match['shipped_years']})."

    # If LTO is active or still in pantry → portability bumps to national
    if lto_match and (lto_match.get("current_status") == "active"
                      or lto_match.get("still_in_pantry")):
        portability = "national"

    return {
        "lift": final_lift,
        "portability": portability,
        "rationale": rationale,
        "missing_skus": sku_details,
        "lto_history_applied": lto_applied,
        "lto_reference": (lto_match["item_name"] if lto_match else None),
    }


def main():
    pantry_fit = json.loads((DATA / "pantry_fit.json").read_text())
    sku_table = json.loads((DATA / "sku_lift_classifications.json").read_text())["skus"]
    lto_history = json.loads((DATA / "brand_lto_history.json").read_text())
    # strip metadata keys
    lto_history = {k: v for k, v in lto_history.items() if not k.startswith("_")}

    out = {}
    for brand in BRANDS:
        out[brand] = {}
        for flavor, fit in pantry_fit.get(brand, {}).items():
            out[brand][flavor] = lift_for_flavor(brand, flavor, fit, sku_table,
                                                  lto_history)

    target = DATA / "operational_lift.json"
    target.write_text(json.dumps(out, indent=2))

    print(f"Wrote {target}")
    print()

    # Hero check: al pastor across brands
    print("Hero check — al pastor lift by brand (LTO history should downgrade Chipotle):")
    for brand in BRANDS:
        rec = out[brand].get("al pastor", {})
        print(f"  {brand:10s} lift={rec.get('lift','?'):<6} "
              f"portability={rec.get('portability','?'):<8} "
              f"lto={'yes' if rec.get('lto_history_applied') else 'no':<3} "
              f"ref={rec.get('lto_reference')}")

    print()
    print("Lift distribution across brands:")
    for brand in BRANDS:
        tiers = {"low": 0, "medium": 0, "high": 0}
        for rec in out[brand].values():
            tiers[rec["lift"]] += 1
        print(f"  {brand:10s} low={tiers['low']:<3} medium={tiers['medium']:<3} high={tiers['high']:<3}")


if __name__ == "__main__":
    main()
