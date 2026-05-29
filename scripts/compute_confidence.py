#!/usr/bin/env python3
"""Composite confidence score (0-100) per (city, flavor, brand) recommendation.

Combines six independently-measured components with explicit weights:

  Trend       20%   DMA Google Trends 12-month average (0-100 from pytrends)
  Local       30%   Indie review mention density across both pools
  Maturity    15%   Trend maturity stage (Rising/Established > Peak/Weak)
  Feasibility 20%   Pantry deliverability + missing-SKU count
  Recency     10%   Share of mentions in last 12 months
  LTO bonus    5%   Brand has shipped this flavor before (proven execution)

Weights sum to 1.0. Each component is 0-100. Final score is rounded.

Reads:
    data/pantry_fit.json
    data/brand_lto_history.json
    data/hermes.db

Writes:
    data/confidence_scores.json — { city: { flavor: { brand: {score, components, ...} } } }

Usage:
    python scripts/compute_confidence.py
"""
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from hermes.db import DB_PATH  # noqa: E402

DATA = ROOT / "data"
BRANDS = ["Chipotle", "CAVA", "Sweetgreen"]
CITIES = {
    "West Hollywood": "US-CA-803",
    "Williamsburg": "US-NY-501",
    "Mission District": "US-CA-807",
}

WEIGHTS = {
    "trend": 0.20,
    "local": 0.30,
    "maturity": 0.15,
    "feasibility": 0.20,
    "recency": 0.10,
    "lto": 0.05,
}

LOCAL_SATURATION = 60      # mentions at which local score saturates to 100
MATURITY_LABELS = {
    "Rising": 95,          # early-mover advantage
    "Established": 85,     # safe shipping window
    "Steady": 65,
    "Peak": 40,            # fad-tail risk
    "Weak": 20,
}


def classify_maturity(comp_mentions: int, lead_mentions: int) -> str:
    """Plain-language maturity stage from competitive vs leading pool spread."""
    total = comp_mentions + lead_mentions
    if total < 5:
        return "Weak"
    if comp_mentions == 0 and lead_mentions == 0:
        return "Weak"
    # Concentration: leading pool >> competitive → chef-driven, not crossed over
    if lead_mentions > comp_mentions * 1.5:
        return "Rising"
    # Concentration: competitive >> leading → mainstream adoption
    if comp_mentions > lead_mentions * 3:
        return "Established"
    # Both pools heavy → saturated, fad-tail risk
    if comp_mentions > 40 and lead_mentions > 20:
        return "Peak"
    return "Steady"


def score_trend(dma_trend: float) -> float:
    return max(0, min(100, dma_trend))


def score_local(comp: int, lead: int) -> float:
    """Use the stronger of the two pools; saturate at LOCAL_SATURATION."""
    strongest = max(comp, lead)
    return min(100, (strongest / LOCAL_SATURATION) * 100)


def score_maturity(stage: str) -> float:
    return MATURITY_LABELS.get(stage, 50)


def score_feasibility(fit: dict) -> float:
    if fit is None:
        return 0
    if fit.get("deliverable"):
        return 100
    missing = len(fit.get("missing", []))
    if missing == 0:
        return 100
    if missing == 1:
        return 60
    if missing == 2:
        return 40
    return 20


def score_recency(recent_mentions: int, total_mentions: int) -> float:
    if total_mentions == 0:
        return 0
    return (recent_mentions / total_mentions) * 100


def score_lto(brand: str, flavor: str, lto: dict) -> float:
    """LTO match bonus. Active LTO = 100, dormant = 80, no history = 0."""
    brand_key = brand.lower()
    entries = lto.get(brand_key, [])
    for entry in entries:
        if flavor in (entry.get("flavor_tags") or []):
            if entry.get("current_status") == "active":
                return 100
            if entry.get("current_status") == "dormant" and entry.get("still_in_pantry"):
                return 80
            if entry.get("current_status") == "dormant":
                return 60
    return 0


def fetch_mention_counts(db, city: str, flavor: str):
    q = """
    SELECT
        SUM(CASE WHEN r.pool_competitive = 1 THEN 1 ELSE 0 END) AS comp,
        SUM(CASE WHEN r.pool_leading = 1 THEN 1 ELSE 0 END) AS lead_,
        COUNT(*) AS total
    FROM flavor_mentions fm
    JOIN reviews rv ON rv.id = fm.review_id
    JOIN restaurants r ON r.id = rv.restaurant_id
    WHERE r.city = ? AND fm.flavor = ?
    """
    row = db.execute(q, (city, flavor)).fetchone()
    return (row["comp"] or 0, row["lead_"] or 0, row["total"] or 0)


def fetch_recent_mentions(db, city: str, flavor: str, cutoff: str):
    q = """
    SELECT COUNT(*) AS n
    FROM flavor_mentions fm
    JOIN reviews rv ON rv.id = fm.review_id
    JOIN restaurants r ON r.id = rv.restaurant_id
    WHERE r.city = ? AND fm.flavor = ? AND rv.review_date >= ?
    """
    row = db.execute(q, (city, flavor, cutoff)).fetchone()
    return row["n"] or 0


def fetch_trend(db, flavor: str, geo: str) -> float:
    row = db.execute(
        "SELECT avg_12m FROM trends WHERE term = ? AND geo = ?",
        (flavor, geo),
    ).fetchone()
    return row["avg_12m"] if row else 0


def compute(db, city: str, geo: str, flavor: str, brand: str,
            pantry_fit: dict, lto: dict, recency_cutoff: str) -> dict:
    comp, lead, total = fetch_mention_counts(db, city, flavor)
    recent = fetch_recent_mentions(db, city, flavor, recency_cutoff)
    dma_trend = fetch_trend(db, flavor, geo)
    stage = classify_maturity(comp, lead)
    fit = pantry_fit.get(brand, {}).get(flavor)

    components = {
        "trend": round(score_trend(dma_trend), 1),
        "local": round(score_local(comp, lead), 1),
        "maturity": round(score_maturity(stage), 1),
        "feasibility": round(score_feasibility(fit), 1),
        "recency": round(score_recency(recent, total), 1),
        "lto": round(score_lto(brand, flavor, lto), 1),
    }

    score = sum(components[k] * WEIGHTS[k] for k in WEIGHTS)

    return {
        "score": round(score),
        "components": components,
        "weights": WEIGHTS,
        "maturity_stage": stage,
        "evidence": {
            "competitive_mentions": comp,
            "leading_mentions": lead,
            "total_mentions": total,
            "recent_12mo_mentions": recent,
            "dma_trend": dma_trend,
            "deliverable": fit.get("deliverable") if fit else False,
            "missing_skus": (fit.get("missing") if fit else []) or [],
        },
    }


def main():
    pantry_fit = json.loads((DATA / "pantry_fit.json").read_text())
    lto = json.loads((DATA / "brand_lto_history.json").read_text())
    flavor_defs = json.loads((DATA / "flavor_definitions.json").read_text())
    flavors = [k for k in flavor_defs if not k.startswith("_")]

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    cutoff = (date.today() - timedelta(days=365)).isoformat()

    out = {}
    for city, geo in CITIES.items():
        out[city] = {}
        for flavor in flavors:
            out[city][flavor] = {}
            for brand in BRANDS:
                out[city][flavor][brand] = compute(
                    db, city, geo, flavor, brand, pantry_fit, lto, cutoff
                )

    target = DATA / "confidence_scores.json"
    target.write_text(json.dumps(out, indent=2))

    # Print hero verification
    print(f"Wrote {target}")
    print()
    print("Hero check — al pastor across cities, Chipotle brand:")
    for city in CITIES:
        rec = out[city]["al pastor"]["Chipotle"]
        ev = rec["evidence"]
        print(f"  {city:20s} score={rec['score']:>3}  stage={rec['maturity_stage']:<12} "
              f"comp={ev['competitive_mentions']:>3} lead={ev['leading_mentions']:>3} "
              f"trend={ev['dma_trend']:>4.1f} lto={rec['components']['lto']:>4}")


if __name__ == "__main__":
    main()
