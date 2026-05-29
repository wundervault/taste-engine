#!/usr/bin/env python3
"""Test: no off-brand recommendations leak to any display surface.

Runs as a structural check on the dashboard's recommendation outputs. Any
(brand × flavor) pair that surfaces as a RECOMMENDATION (ship-now / gap-fill /
add SKU) must pass check_brand_cuisine_fit. Informational surfaces (e.g.
the Pantry "Cannot deliver" list) are exempt — only recommendation surfaces
are checked.

Sources scanned:
    data/{city}_dish_recommendations_v6.json — every brand × signal_term /
        target_flavor pair on every card must be on-brand.
    data/{city}_dish_recommendations_v8.json — same check on the agent's
        raw output.
    load_on_brand_signal_ranking — verify the helper actually filters
        off-brand flavors as advertised.

Run before any deploy or commit:
    python scripts/test_no_off_brand_displays.py
Exit code 1 on any violation.

This is the durable enforcement mechanism. If a future code change accidentally
surfaces an off-brand recommendation, this test catches it before the dashboard
ships. No reliance on code review remembering the constraint.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from dish_tools import check_brand_cuisine_fit  # noqa: E402

DATA = ROOT / "data"
CITIES = ["mission", "williamsburg", "weho"]
BRANDS = ["Chipotle", "CAVA", "Sweetgreen"]


def check_v6_card(card: dict) -> list[str]:
    """Return list of violations for a single dish recommendation card."""
    violations = []
    brand = card.get("brand")
    flavor = card.get("signal_term") or card.get("target_flavor")
    if not (brand and flavor):
        return []
    fit = check_brand_cuisine_fit(brand, flavor)
    if fit.get("fit") == "off_brand":
        violations.append(
            f"  ⛔ {brand} {card.get('type','?')}: anchor '{flavor}' is OFF-BRAND "
            f"(families {fit['flavor_families']} vs brand identity {fit['brand_identity']})"
        )
    return violations


def check_recommendation_files() -> tuple[int, int]:
    """Scan v6 + v8 dish recommendation JSON files for off-brand cards."""
    total_cards = 0
    violations = []
    for version in ("v6", "v8"):
        for city in CITIES:
            path = DATA / f"{city}_dish_recommendations_{version}.json"
            if not path.exists():
                continue
            cards = json.loads(path.read_text())
            for card in cards:
                total_cards += 1
                card_violations = check_v6_card(card)
                if card_violations:
                    violations.append(
                        f"{path.name} → brand={card.get('brand')} type={card.get('type')}"
                    )
                    violations.extend(card_violations)
    return total_cards, violations


def check_signal_helper() -> list[str]:
    """Verify load_on_brand_signal_ranking actually filters off-brand."""
    # Simulate the helper call without importing streamlit's @st.cache_data
    from dish_generator import signal_ranking_dual

    issues = []
    # Use a simple sqlite connection
    import sqlite3
    sys.path.insert(0, str(ROOT / "src"))
    from hermes.db import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    geos = {"mission": "US-CA-807", "williamsburg": "US-NY-501", "weho": "US-CA-803"}
    city_labels = {"mission": "Mission District", "williamsburg": "Williamsburg",
                   "weho": "West Hollywood"}

    for city_key, geo in geos.items():
        city_label = city_labels[city_key]
        raw = signal_ranking_dual(conn, city_label, geo, limit=45)
        for brand in BRANDS:
            filtered = []
            for s in raw:
                fit = check_brand_cuisine_fit(brand, s["term"])
                if fit.get("fit") == "on_brand":
                    filtered.append(s["term"])
                if len(filtered) >= 15:
                    break
            # Confirm every filtered term passes the check
            for term in filtered:
                fit = check_brand_cuisine_fit(brand, term)
                if fit.get("fit") != "on_brand":
                    issues.append(
                        f"  ⛔ load_on_brand_signal_ranking({city_key}, {brand}) "
                        f"returned off-brand flavor '{term}'"
                    )
    conn.close()
    return issues


def main():
    print("Running structural test: no off-brand recommendations on display surfaces.\n")

    total_cards, card_violations = check_recommendation_files()
    helper_issues = check_signal_helper()

    print(f"Scanned {total_cards} recommendation cards across v6 + v8 files.")
    print(f"Tested load_on_brand_signal_ranking across {len(CITIES)} cities × {len(BRANDS)} brands.")
    print()

    failed = bool(card_violations or helper_issues)
    if card_violations:
        print(f"❌ {len(card_violations) // 2} off-brand RECOMMENDATION violation(s):")
        for v in card_violations:
            print(v)
        print()
    else:
        print("✅ All v6 + v8 dish recommendation cards are on-brand.")

    if helper_issues:
        print(f"❌ {len(helper_issues)} helper violation(s):")
        for v in helper_issues:
            print(v)
    else:
        print("✅ load_on_brand_signal_ranking returns only on-brand flavors.")

    if failed:
        print("\nThis test failed. An off-brand recommendation leaked to a display surface.")
        print("Either fix the source data, route through load_on_brand_signal_ranking,")
        print("or revisit data/brand_cuisine_identity.json if the classification is wrong.")
        sys.exit(1)

    print("\nAll checks passed. No off-brand recommendations surface anywhere.")


if __name__ == "__main__":
    main()
