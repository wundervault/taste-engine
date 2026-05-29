#!/usr/bin/env python3
"""For each (flavor, brand) combination, decide whether the brand's current
pantry can HONESTLY deliver the flavor (vs just gesture at it).

Reads:
    data/flavor_definitions.json  — curated match_terms per flavor
    brand_menu_items (DB)         — available SKUs per brand

Writes:
    data/pantry_fit.json — { brand: { flavor: {deliverable, matched, missing} } }

Match rules:
  - Pantry items are lowercased and parentheticals stripped (so "lamb meatballs
    (harissa, cilantro)" does NOT count as a harissa SKU — only "harissa" as a
    standalone item does).
  - A flavor is "deliverable" if at least `min_matches` of its `match_terms`
    are found as substrings in the pantry.
  - Only available=1 rows count.

Usage:
    python scripts/compute_pantry_fit.py
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
BRANDS = ["Chipotle", "CAVA", "Sweetgreen"]


def load_flavor_definitions() -> dict:
    d = json.loads((DATA / "flavor_definitions.json").read_text())
    return {k: v for k, v in d.items() if not k.startswith("_")}


def load_brand_pantry(conn, brand: str) -> list[str]:
    """Return list of standalone-SKU names for one brand (parentheticals stripped)."""
    if brand == "Sweetgreen":
        cat_filter = "AND category LIKE 'sweetgreen_byo_%'"
    else:
        cat_filter = "AND category != 'dish'"
    rows = conn.execute(
        f"SELECT item FROM brand_menu_items "
        f"WHERE brand = ? AND available = 1 {cat_filter}",
        (brand,),
    ).fetchall()
    cleaned = []
    for r in rows:
        # Strip parenthetical contents (bundled ingredients aren't standalone SKUs)
        name = re.sub(r"\([^)]*\)", "", r[0]).strip().lower()
        if name:
            cleaned.append(name)
    return cleaned


def check_fit(definition: dict, pantry_text: str) -> tuple[bool, list[str], list[str]]:
    """Match each term as a whole word (regex \\b...\\b) — otherwise 'mole'
    would false-positive on 'guacamole' and 'egg' on 'eggplant'."""
    matched, missing = [], []
    for term in definition["match_terms"]:
        # Escape regex metachars, allow flexible whitespace for multi-word,
        # allow optional trailing 's' so "lentil" matches "lentils" and
        # "chickpea" matches "chickpeas". Still uses word boundary so "mole"
        # won't match "guacamole".
        pat = r"\b" + re.escape(term.lower()).replace(r"\ ", r"\s+") + r"s?\b"
        if re.search(pat, pantry_text):
            matched.append(term)
        else:
            missing.append(term)
    deliverable = len(matched) >= definition["min_matches"]
    return deliverable, matched, missing


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    flavors = load_flavor_definitions()
    out: dict = {}
    summary_rows = []
    for brand in BRANDS:
        pantry_items = load_brand_pantry(conn, brand)
        pantry_text = " | ".join(pantry_items)
        out[brand] = {}
        deliverable_count = 0
        for flavor, defn in flavors.items():
            deliverable, matched, missing = check_fit(defn, pantry_text)
            out[brand][flavor] = {
                "deliverable": deliverable,
                "matched": matched,
                "missing": missing,
                "min_matches": defn["min_matches"],
            }
            if deliverable:
                deliverable_count += 1
        summary_rows.append((brand, deliverable_count, len(pantry_items)))
    conn.close()

    out_path = DATA / "pantry_fit.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"→ {out_path}")
    print("\n=== summary ===")
    print(f"{'Brand':<12} {'Deliverable flavors':<22} {'Pantry SKUs (standalone)':<25}")
    for brand, n, sku in summary_rows:
        print(f"  {brand:<12} {n:<22} {sku}")

    print("\n=== per-brand deliverable flavors ===")
    for brand in BRANDS:
        flavors_in = sorted(
            f for f, v in out[brand].items() if v["deliverable"]
        )
        print(f"\n{brand} ({len(flavors_in)}):")
        for f in flavors_in:
            matched = ", ".join(out[brand][f]["matched"])
            print(f"  • {f:<18} via [{matched}]")

    print("\n=== high-signal non-deliverable flavors (GAPS to flag) ===")
    # Pull per-city signal ranking to find which gaps matter where
    print("Top 8 flavors per city — those with [✗] for a brand are gap candidates:")
    sys.path.insert(0, str(ROOT / "scripts"))
    from dish_generator import signal_ranking, CITIES
    for city_key, spec in CITIES.items():
        print(f"\n  {spec['city']} ({spec['geo']}):")
        top = signal_ranking(conn, spec["city"], spec["geo"], limit=8) if False else None
        # Re-open conn since main() closed it
        c2 = sqlite3.connect(DB_PATH)
        c2.row_factory = sqlite3.Row
        from dish_generator import signal_ranking
        top = signal_ranking(c2, spec["city"], spec["geo"], limit=8)
        c2.close()
        for s in top:
            flags = []
            for brand in BRANDS:
                deliverable = out[brand].get(s["term"], {}).get("deliverable", False)
                flags.append(f"{brand[0]}{'✓' if deliverable else '✗'}")
            print(f"    {s['term']:<14} score={s['signal_score']:>5}  {' '.join(flags)}")


if __name__ == "__main__":
    main()
