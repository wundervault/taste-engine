#!/usr/bin/env python3
"""Drop reviews that aren't useful for time-binned velocity analysis:
  - review_date IS NULL (Google Maps snippets — no timestamp available)
  - review_date < 2024-01-01 (too old — current Q-on-Q signal only)

Also cascades to flavor_mentions (FK ON DELETE CASCADE).

Idempotent.

Usage:
    python scripts/clean_reviews.py             # apply cleanup
    python scripts/clean_reviews.py --dry-run   # show counts only
"""
import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from hermes.db import DB_PATH  # noqa: E402

CUTOFF = "2024-01-01"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    total_before = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    undated = conn.execute("SELECT COUNT(*) FROM reviews WHERE review_date IS NULL").fetchone()[0]
    pre_cutoff = conn.execute(
        "SELECT COUNT(*) FROM reviews WHERE review_date IS NOT NULL AND review_date < ?",
        (CUTOFF,),
    ).fetchone()[0]
    fm_before = conn.execute("SELECT COUNT(*) FROM flavor_mentions").fetchone()[0]

    print(f"Before cleanup:")
    print(f"  total reviews:        {total_before:,}")
    print(f"  undated (snippets):   {undated:,}  → drop")
    print(f"  dated < {CUTOFF}:  {pre_cutoff:,}  → drop")
    print(f"  flavor_mentions:      {fm_before:,}")

    if args.dry_run:
        print("\n--dry-run: no changes")
        return

    # Delete reviews — flavor_mentions cascades via ON DELETE CASCADE
    deleted_undated = conn.execute(
        "DELETE FROM reviews WHERE review_date IS NULL"
    ).rowcount
    deleted_old = conn.execute(
        "DELETE FROM reviews WHERE review_date IS NOT NULL AND review_date < ?",
        (CUTOFF,),
    ).rowcount
    conn.commit()
    conn.execute("VACUUM")

    total_after = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    fm_after = conn.execute("SELECT COUNT(*) FROM flavor_mentions").fetchone()[0]

    print(f"\nAfter cleanup:")
    print(f"  deleted undated:   {deleted_undated:,}")
    print(f"  deleted pre-{CUTOFF[:4]}:  {deleted_old:,}")
    print(f"  reviews now:       {total_after:,}")
    print(f"  flavor_mentions:   {fm_after:,}  (cascaded)")

    # Year + quarter distribution
    print(f"\nReviews by quarter:")
    for r in conn.execute("""
        SELECT
          substr(review_date, 1, 4) AS yr,
          CASE
            WHEN cast(substr(review_date, 6, 2) AS INTEGER) BETWEEN 1 AND 3 THEN 'Q1'
            WHEN cast(substr(review_date, 6, 2) AS INTEGER) BETWEEN 4 AND 6 THEN 'Q2'
            WHEN cast(substr(review_date, 6, 2) AS INTEGER) BETWEEN 7 AND 9 THEN 'Q3'
            ELSE 'Q4'
          END AS qtr,
          COUNT(*) AS n
        FROM reviews
        WHERE review_date IS NOT NULL
        GROUP BY yr, qtr
        ORDER BY yr, qtr
    """):
        bar = "█" * (r["n"] // 500)
        print(f"  {r['yr']}-{r['qtr']}: {r['n']:>6,}  {bar}")

    conn.close()


if __name__ == "__main__":
    main()
