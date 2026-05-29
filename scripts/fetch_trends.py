#!/usr/bin/env python3
"""Fetch Google Trends data at DMA (Designated Market Area) level for the
flavor vocabulary, replacing prior state-level rows so neighborhood-aware
queries (WeHo vs SF Mission vs Williamsburg) get differentiated trend data.

DMAs:
  LA Metro                      US-CA-803  → WeHo
  San Francisco-Oakland-SJ      US-CA-807  → Mission
  New York                      US-NY-501  → Williamsburg

Idempotent: deletes existing trend rows for the targeted DMAs before
inserting fresh. State-level rows (geo='US-CA' / 'US-NY') are also wiped
to avoid two parallel representations.

Usage:
    python scripts/fetch_trends.py
"""
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from pytrends.request import TrendReq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from hermes.db import init_db  # noqa: E402

DMAS = {
    "US-CA-803": "LA Metro",
    "US-CA-807": "San Francisco-Oakland-San Jose",
    "US-NY-501": "New York",
}

TIMEFRAME = "today 12-m"
BATCH_SIZE = 5  # pytrends limit
SLEEP_BETWEEN_BATCHES = 2.0  # seconds, polite spacing


def get_vocabulary(conn) -> list[str]:
    """Pull existing trend terms. Fall back to a curated seed if empty."""
    rows = [r["term"] for r in conn.execute(
        "SELECT DISTINCT term FROM trends ORDER BY term"
    )]
    if rows:
        return rows
    return [
        "harissa", "tahini", "sumac", "za'atar", "shakshuka",
        "kimchi", "bulgogi", "gochujang", "miso", "yuzu",
        "jerk", "scotch bonnet", "plantain", "curry goat", "oxtail",
        "birria", "al pastor", "mole", "elote", "cauliflower",
        "lentils", "pesto", "burrata", "truffle", "nduja",
        "calabrian chili", "tonkotsu", "dashi", "furikake", "karaage",
        "bao bun", "adobo sauce", "crispy chickpeas", "farro", "freekeh",
    ]


def fetch_batch(pytrends, terms: list[str], geo: str):
    """Returns dict {term: {avg_12m, recent_4w, peak, trend}} or {} on failure."""
    pytrends.build_payload(terms, cat=0, timeframe=TIMEFRAME, geo=geo)
    df = pytrends.interest_over_time()
    if df.empty:
        return {}
    if "isPartial" in df.columns:
        df = df.drop(columns=["isPartial"])
    out = {}
    for term in terms:
        if term not in df.columns:
            continue
        series = df[term]
        avg_12m = float(series.mean())
        recent_4w = float(series.tail(4).mean())
        peak = float(series.max())
        # Trend direction: compare last 4 weeks to first 4 weeks
        first_4w = float(series.head(4).mean())
        if recent_4w > first_4w * 1.15:
            trend = "rising"
        elif recent_4w < first_4w * 0.85:
            trend = "falling"
        else:
            trend = "flat"
        out[term] = {
            "avg_12m": round(avg_12m, 1),
            "recent_4w": round(recent_4w, 1),
            "peak": round(peak, 1),
            "trend": trend,
        }
    return out


def main():
    conn = init_db()
    pytrends = TrendReq(hl="en-US", tz=360, retries=3, backoff_factor=2)

    vocab = get_vocabulary(conn)
    print(f"vocabulary: {len(vocab)} terms")
    print(f"DMAs: {list(DMAS)}\n")

    # Wipe both old DMA rows AND state-level rows we're superseding.
    conn.execute(
        "DELETE FROM trends WHERE geo IN ('US-CA','US-NY') OR geo LIKE 'US-CA-%' OR geo LIKE 'US-NY-%'"
    )
    conn.commit()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    grand_total = 0

    for geo, label in DMAS.items():
        print(f"=== {geo} ({label}) ===")
        geo_total = 0
        for i in range(0, len(vocab), BATCH_SIZE):
            batch = vocab[i:i + BATCH_SIZE]
            try:
                results = fetch_batch(pytrends, batch, geo)
            except Exception as e:
                print(f"  batch {i//BATCH_SIZE + 1} failed ({type(e).__name__}: {e}); skipping")
                time.sleep(SLEEP_BETWEEN_BATCHES * 3)
                continue
            for term, v in results.items():
                conn.execute(
                    """INSERT INTO trends (term, geo, timeframe, avg_12m, recent_4w, peak, trend, scraped_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(term, geo, timeframe) DO UPDATE SET
                         avg_12m=excluded.avg_12m, recent_4w=excluded.recent_4w,
                         peak=excluded.peak, trend=excluded.trend, scraped_at=excluded.scraped_at""",
                    (term, geo, TIMEFRAME, v["avg_12m"], v["recent_4w"],
                     v["peak"], v["trend"], now),
                )
            conn.commit()
            geo_total += len(results)
            print(f"  batch {i//BATCH_SIZE + 1}: {len(results)}/{len(batch)} terms")
            time.sleep(SLEEP_BETWEEN_BATCHES)
        print(f"  → {geo_total} terms loaded for {geo}\n")
        grand_total += geo_total

    print(f"--- total: {grand_total} term×DMA rows ---")
    print("\ntop 5 per DMA by avg_12m:")
    for geo in DMAS:
        print(f"\n  {geo}:")
        for r in conn.execute(
            "SELECT term, avg_12m, trend FROM trends WHERE geo=? "
            "ORDER BY avg_12m DESC LIMIT 5", (geo,)
        ):
            print(f"    {r['term']:<16} avg={r['avg_12m']:>5}  {r['trend']}")

    conn.close()


if __name__ == "__main__":
    main()
