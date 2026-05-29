#!/usr/bin/env python3
"""Quarterly Brief — turns our snapshot data into a "what changed and what's next"
brief per city. This is the temporal repositioning the dashboard needed:
operators visiting monthly/quarterly need to see deltas, not just current state.

Three components per city:
  1. Movers — top 5 flavors with biggest mention increase + biggest decrease,
     comparing the most recent complete quarter (Q1 2026) vs the prior (Q4 2025)
  2. Maturity transitions — flavors that crossed a stage boundary
     (Rising → Established, Established → Peak, etc) between Q4 and Q1
  3. Next-quarter forecast — for top 3 flavors per city, simple OLS on
     trailing 5 quarters projects Q2 2026 + Q3 2026 with a confidence band

Reads:
    data/hermes.db (flavor_mentions joined with restaurants + reviews)
    data/confidence_scores.json (for current maturity classification)

Writes:
    data/quarterly_brief.json

Usage:
    python scripts/compute_quarterly_brief.py
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from hermes.db import DB_PATH  # noqa: E402
from compute_confidence import classify_maturity  # noqa: E402

DATA = ROOT / "data"

CITIES = ["West Hollywood", "Williamsburg", "Mission District"]

# Reference quarter: most recent COMPLETE quarter
# Today is 2026-05-28 so Q2 2026 is in-progress; Q1 2026 is the most recent complete.
REF_QUARTER = (2026, 1)
PRIOR_QUARTER = (2025, 4)
# Quarters used for forecast fitting (most recent 5 complete quarters)
FORECAST_QUARTERS = [(2024, 4), (2025, 1), (2025, 2), (2025, 3), (2025, 4), (2026, 1)]
# Full history window for trends-tab charts (8 quarters)
HISTORY_QUARTERS = [(2024, 1), (2024, 2), (2024, 3), (2024, 4),
                    (2025, 1), (2025, 2), (2025, 3), (2025, 4), (2026, 1)]


def quarter_label(yq):
    y, q = yq
    return f"{y}-Q{q}"


def quarter_range(yq):
    y, q = yq
    start_month = (q - 1) * 3 + 1
    end_month = start_month + 2
    start = f"{y}-{start_month:02d}-01"
    if end_month == 12:
        end = f"{y}-12-31"
    elif end_month == 3:
        end = f"{y}-03-31"
    elif end_month == 6:
        end = f"{y}-06-30"
    elif end_month == 9:
        end = f"{y}-09-30"
    else:
        end = f"{y}-{end_month:02d}-30"
    return start, end


def mentions_per_flavor(db, city: str, yq) -> dict[str, dict]:
    """Returns {flavor: {competitive, leading, total}} for one quarter."""
    start, end = quarter_range(yq)
    q = """
    SELECT fm.flavor,
           SUM(CASE WHEN r.pool_competitive = 1 THEN 1 ELSE 0 END) AS comp,
           SUM(CASE WHEN r.pool_leading = 1 THEN 1 ELSE 0 END) AS lead_
    FROM flavor_mentions fm
    JOIN reviews rv ON rv.id = fm.review_id
    JOIN restaurants r ON r.id = rv.restaurant_id
    WHERE r.city = ?
      AND rv.review_date IS NOT NULL
      AND rv.review_date >= ?
      AND rv.review_date <= ?
    GROUP BY fm.flavor
    """
    rows = db.execute(q, (city, start, end)).fetchall()
    out = {}
    for r in rows:
        comp = r["comp"] or 0
        lead = r["lead_"] or 0
        out[r["flavor"]] = {
            "competitive": comp,
            "leading": lead,
            "total": comp + lead,
        }
    return out


def compute_movers(ref: dict, prior: dict, n: int = 5):
    """Returns (top_n_up, top_n_down) by total-mention delta."""
    all_flavors = set(ref) | set(prior)
    deltas = []
    for f in all_flavors:
        r_total = ref.get(f, {}).get("total", 0)
        p_total = prior.get(f, {}).get("total", 0)
        delta = r_total - p_total
        if r_total + p_total < 3:
            continue  # filter background noise
        deltas.append({
            "flavor": f, "delta": delta,
            "ref_total": r_total, "prior_total": p_total,
        })
    deltas.sort(key=lambda d: -d["delta"])
    up = [d for d in deltas if d["delta"] > 0][:n]
    down = sorted([d for d in deltas if d["delta"] < 0], key=lambda d: d["delta"])[:n]
    return up, down


def compute_transitions(ref: dict, prior: dict):
    """Returns flavors whose maturity stage changed between prior and ref quarter.
    Uses our standard classify_maturity from compute_confidence."""
    transitions = []
    for f in set(ref) | set(prior):
        r = ref.get(f, {})
        p = prior.get(f, {})
        ref_stage = classify_maturity(r.get("competitive", 0), r.get("leading", 0))
        prior_stage = classify_maturity(p.get("competitive", 0), p.get("leading", 0))
        if ref_stage != prior_stage and (r.get("total", 0) + p.get("total", 0)) >= 4:
            transitions.append({
                "flavor": f,
                "prior_stage": prior_stage,
                "ref_stage": ref_stage,
            })
    return transitions


def forecast_next_quarters(db, city: str, flavor: str) -> dict | None:
    """Fit OLS on trailing 5-6 complete quarters and project Q2 + Q3 2026.
    Returns {history, forecast_q2, forecast_q3, slope, direction, fit_quality}
    or None if too few data points."""
    history = []
    for yq in FORECAST_QUARTERS:
        m = mentions_per_flavor(db, city, yq).get(flavor, {})
        history.append({"quarter": quarter_label(yq), "mentions": m.get("total", 0)})

    counts = [h["mentions"] for h in history]
    if sum(counts) < 5 or len([c for c in counts if c > 0]) < 3:
        return None

    # Simple linear fit: y = a + b*x where x is index 0..n-1
    n = len(counts)
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(counts) / n
    num = sum((xs[i] - mean_x) * (counts[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    if den == 0:
        return None
    slope = num / den
    intercept = mean_y - slope * mean_x

    # Forecast next two quarters
    forecast_q2 = max(0, intercept + slope * n)
    forecast_q3 = max(0, intercept + slope * (n + 1))

    # Crude residual std for confidence band
    residuals = [counts[i] - (intercept + slope * i) for i in range(n)]
    rss = sum(r * r for r in residuals)
    std = (rss / max(n - 2, 1)) ** 0.5

    direction = ("rising" if slope > 0.5 else
                 "falling" if slope < -0.5 else "stable")

    return {
        "flavor": flavor,
        "history": history,
        "forecast_q2_2026": round(forecast_q2, 1),
        "forecast_q3_2026": round(forecast_q3, 1),
        "forecast_band": round(std, 1),
        "slope_per_quarter": round(slope, 2),
        "direction": direction,
    }


def main():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    out = {"_meta": {
        "ref_quarter": quarter_label(REF_QUARTER),
        "prior_quarter": quarter_label(PRIOR_QUARTER),
        "forecast_horizon": ["2026-Q2", "2026-Q3"],
        "note": "Forecasts are simple OLS projections from trailing quarters — directional, not precise. We label them honestly.",
    }}

    for city in CITIES:
        ref = mentions_per_flavor(db, city, REF_QUARTER)
        prior = mentions_per_flavor(db, city, PRIOR_QUARTER)
        up, down = compute_movers(ref, prior, n=5)
        transitions = compute_transitions(ref, prior)

        # Top 3 flavors in ref quarter for forecast
        top3_now = sorted(ref.items(), key=lambda x: -x[1]["total"])[:3]
        forecasts = []
        for flavor, _ in top3_now:
            fc = forecast_next_quarters(db, city, flavor)
            if fc:
                forecasts.append(fc)

        # Full 8-quarter history for top 8 flavors (for the Trends-tab charts)
        top8_now = sorted(ref.items(), key=lambda x: -x[1]["total"])[:8]
        top_flavor_histories = []
        for flavor, _ in top8_now:
            history = []
            for yq in HISTORY_QUARTERS:
                m = mentions_per_flavor(db, city, yq).get(flavor, {})
                history.append({
                    "quarter": quarter_label(yq),
                    "competitive": m.get("competitive", 0),
                    "leading": m.get("leading", 0),
                    "total": m.get("total", 0),
                })
            top_flavor_histories.append({
                "flavor": flavor,
                "history": history,
                "ref_total": ref.get(flavor, {}).get("total", 0),
                "current_stage": classify_maturity(
                    ref.get(flavor, {}).get("competitive", 0),
                    ref.get(flavor, {}).get("leading", 0),
                ),
            })

        out[city] = {
            "movers_up": up,
            "movers_down": down,
            "transitions": transitions,
            "forecasts": forecasts,
            "top_flavor_histories": top_flavor_histories,
        }

    target = DATA / "quarterly_brief.json"
    target.write_text(json.dumps(out, indent=2))
    print(f"Wrote {target}")
    print()

    # Print a hero preview
    print("MISSION DISTRICT Q1 2026 BRIEF preview:")
    m = out["Mission District"]
    print(f"  ▲ Movers up:")
    for x in m["movers_up"][:3]:
        print(f"      {x['flavor']:<15s} +{x['delta']} ({x['prior_total']} → {x['ref_total']})")
    print(f"  ▼ Movers down:")
    for x in m["movers_down"][:3]:
        print(f"      {x['flavor']:<15s} {x['delta']} ({x['prior_total']} → {x['ref_total']})")
    print(f"  ⚡ Transitions:")
    for t in m["transitions"][:5]:
        print(f"      {t['flavor']:<15s} {t['prior_stage']} → {t['ref_stage']}")
    print(f"  🎯 Forecasts:")
    for f in m["forecasts"]:
        print(f"      {f['flavor']:<15s} Q2={f['forecast_q2_2026']:.0f} (±{f['forecast_band']:.0f}) "
              f"Q3={f['forecast_q3_2026']:.0f} · {f['direction']}")


if __name__ == "__main__":
    main()
