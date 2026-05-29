#!/usr/bin/env python3
"""Build a per-city data snapshot that's self-contained enough for an exec-overview
generator to read without DB access.

Output: data/overview_snapshot.json — one entry per city with:
  - top_signals (cross-source ranked, with competitive + leading scores)
  - quarterly_velocity (top 10 flavors × quarter mention counts)
  - pantry_fit per brand (deliverable / non-deliverable counts + top gaps)
  - existing_dishes per brand (so summary can flag novelty)
  - dataset_stats (restaurants, reviews, date span)

Run after every data refresh. Subagent reads this file to write the prose overview.

Usage:
    python scripts/snapshot_for_overview.py
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from hermes.db import DB_PATH  # noqa: E402
from dish_tools import signal_ranking_dual\nfrom dish_generator_agent import CITY_KEY_TO_LABEL as CITIES  # noqa: E402

DATA = ROOT / "data"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    fit = json.loads((DATA / "pantry_fit.json").read_text()) if (DATA / "pantry_fit.json").exists() else {}

    snapshot = {"cities": {}}

    for city_key, spec in CITIES.items():
        city_name = spec["city"]
        c = {
            "key": city_key,
            "name": city_name,
            "geo": spec["geo"],
            "context": spec["context"],
        }

        # dataset stats
        r = conn.execute(
            """SELECT
                 COUNT(DISTINCT r.id) AS restaurants,
                 SUM(r.pool_competitive) AS pool_competitive,
                 SUM(r.pool_leading) AS pool_leading,
                 (SELECT COUNT(*) FROM reviews rv JOIN restaurants rs ON rs.id = rv.restaurant_id
                  WHERE rs.city = ? AND rv.review_date IS NOT NULL) AS reviews_dated,
                 (SELECT MIN(rv.review_date) FROM reviews rv JOIN restaurants rs ON rs.id = rv.restaurant_id
                  WHERE rs.city = ? AND rv.review_date IS NOT NULL) AS earliest,
                 (SELECT MAX(rv.review_date) FROM reviews rv JOIN restaurants rs ON rs.id = rv.restaurant_id
                  WHERE rs.city = ? AND rv.review_date IS NOT NULL) AS latest
               FROM restaurants r WHERE r.city = ?""",
            (city_name, city_name, city_name, city_name),
        ).fetchone()
        c["dataset"] = dict(r)

        # cross-source signals (dual pool)
        dual = signal_ranking_dual(conn, city_name, spec["geo"], limit=15)
        c["top_signals"] = []
        for s in dual:
            comp = s["competitive_score"] or 0
            lead = s["leading_score"] or 0
            if comp >= 0.30 and lead >= 0.30: traj = "peak"
            elif lead - comp >= 0.10: traj = "rising"
            elif comp - lead >= 0.10: traj = "established"
            elif comp >= 0.25 or lead >= 0.25: traj = "steady"
            else: traj = "weak"
            c["top_signals"].append({
                "term": s["term"],
                "trend_dma": s["trend"],
                "competitive_score": round(comp, 3),
                "competitive_mentions": s["competitive_mentions"],
                "leading_score": round(lead, 3),
                "leading_mentions": s["leading_mentions"],
                "trajectory": traj,
            })

        # quarterly velocity for top 10 flavors
        top10 = [s["term"] for s in dual[:10]]
        c["quarterly_velocity"] = {}
        for term in top10:
            rows = conn.execute(
                """SELECT
                     substr(rv.review_date,1,4) || '-Q' ||
                       CASE
                         WHEN cast(substr(rv.review_date,6,2) AS INTEGER) BETWEEN 1 AND 3 THEN '1'
                         WHEN cast(substr(rv.review_date,6,2) AS INTEGER) BETWEEN 4 AND 6 THEN '2'
                         WHEN cast(substr(rv.review_date,6,2) AS INTEGER) BETWEEN 7 AND 9 THEN '3'
                         ELSE '4' END AS quarter,
                     SUM(fm.count) AS n
                   FROM flavor_mentions fm
                   JOIN reviews rv ON rv.id = fm.review_id
                   JOIN restaurants r ON r.id = rv.restaurant_id
                   WHERE r.city = ? AND fm.flavor = ? AND rv.review_date IS NOT NULL
                   GROUP BY quarter ORDER BY quarter""",
                (city_name, term),
            ).fetchall()
            c["quarterly_velocity"][term] = {r["quarter"]: r["n"] for r in rows}

        # pantry fit per brand
        c["pantry_fit"] = {}
        for brand in ("Chipotle", "CAVA", "Sweetgreen"):
            brand_fit = fit.get(brand, {})
            deliverable = [f for f, v in brand_fit.items() if v["deliverable"]]
            # Top signals this brand CAN'T deliver
            top_gaps = []
            for s in dual[:10]:
                v = brand_fit.get(s["term"], {})
                if not v.get("deliverable", False):
                    max_s = max(s["competitive_score"] or 0, s["leading_score"] or 0)
                    if max_s >= 0.30:
                        top_gaps.append({
                            "term": s["term"],
                            "max_score": round(max_s, 3),
                            "missing_skus": v.get("missing", []),
                        })
            # Top signals this brand CAN deliver
            top_strengths = []
            for s in dual[:15]:
                v = brand_fit.get(s["term"], {})
                if v.get("deliverable", False):
                    max_s = max(s["competitive_score"] or 0, s["leading_score"] or 0)
                    if max_s >= 0.20:
                        top_strengths.append({
                            "term": s["term"],
                            "max_score": round(max_s, 3),
                            "via": v.get("matched", []),
                        })
            c["pantry_fit"][brand] = {
                "deliverable_count": len(deliverable),
                "deliverable_total_vocab": 50,
                "top_strengths": top_strengths[:5],
                "top_gaps": top_gaps[:5],
            }

        # ── PER-BRAND PICKS — deterministic selection of ship-now + gap-fill ──
        # Look up to top 30 signals (not just top 8) so every brand gets a ship-now
        # pick even when their pantry doesn't align with the very top flavors.
        deep_signals = signal_ranking_dual(conn, city_name, spec["geo"], limit=30)
        c["brand_actions"] = {}
        for brand in ("Chipotle", "CAVA", "Sweetgreen"):
            brand_fit = fit.get(brand, {})

            # Ship-now: highest-scoring flavor in this city that the brand CAN deliver
            ship_now = None
            for s in deep_signals:
                if brand_fit.get(s["term"], {}).get("deliverable", False):
                    max_s = max(s["competitive_score"] or 0, s["leading_score"] or 0)
                    ship_now = {
                        "flavor": s["term"],
                        "max_score": round(max_s, 3),
                        "competitive_score": s["competitive_score"],
                        "leading_score": s["leading_score"],
                        "pantry_match": brand_fit[s["term"]]["matched"],
                        "signal_rank": deep_signals.index(s) + 1,
                    }
                    break

            # Gap-fill: highest-signal flavor in top 8 that brand CANNOT deliver,
            # prefer ones with shortest missing-SKU list (easiest to fill).
            gap_candidates = []
            for s in deep_signals[:8]:
                v = brand_fit.get(s["term"], {})
                if not v.get("deliverable", False):
                    missing = v.get("missing", [])
                    max_s = max(s["competitive_score"] or 0, s["leading_score"] or 0)
                    gap_candidates.append({
                        "flavor": s["term"],
                        "max_score": round(max_s, 3),
                        "competitive_score": s["competitive_score"],
                        "leading_score": s["leading_score"],
                        "missing_skus": missing,
                        "missing_count": len(missing),
                        "signal_rank": deep_signals.index(s) + 1,
                    })
            # Sort: lowest missing_count first (easiest), then by score desc
            gap_candidates.sort(key=lambda x: (x["missing_count"], -x["max_score"]))
            gap_fill = gap_candidates[0] if gap_candidates else None

            c["brand_actions"][brand] = {
                "ship_now": ship_now,
                "gap_fill": gap_fill,
            }

        snapshot["cities"][city_key] = c

    # brand existing-dish summaries (city-independent)
    snapshot["brand_existing_dishes"] = {}
    for brand in ("Chipotle", "CAVA", "Sweetgreen"):
        rows = conn.execute(
            "SELECT item FROM brand_menu_items WHERE brand = ? AND category = 'dish'",
            (brand,),
        ).fetchall()
        snapshot["brand_existing_dishes"][brand] = [r["item"] for r in rows]

    out = DATA / "overview_snapshot.json"
    out.write_text(json.dumps(snapshot, indent=2))
    print(f"→ {out}")
    print(f"  cities: {list(snapshot['cities'])}")
    for ck, c in snapshot["cities"].items():
        ds = c["dataset"]
        print(f"  {ck}: {ds['restaurants']} restaurants, {ds['reviews_dated']:,} dated reviews "
              f"({ds['earliest']} → {ds['latest']}); pools C={ds['pool_competitive']} L={ds['pool_leading']}")
    conn.close()


if __name__ == "__main__":
    main()
