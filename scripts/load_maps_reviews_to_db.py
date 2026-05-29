#!/usr/bin/env python3
"""Load chronological reviews from BD's Google Maps Reviews dataset
(`gd_luzfs1dn2oa0teb81`) into the reviews table.

Reads:   data/maps_reviews_raw/reviews_*.json (newest by default)
Writes:  reviews (source='gmaps', dedup via text_hash UNIQUE constraint)

The Fast Maps Search top-8 / snippets data already in DB stays put — text_hash
dedup catches overlapping reviews (same text from same restaurant won't double).
Most rows from this loader will be NEW (the chronological feed surfaces reviews
Google didn't pick for top-8).

Usage:
    python scripts/load_maps_reviews_to_db.py                 # newest file
    python scripts/load_maps_reviews_to_db.py path/to/file.json
"""
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from hermes.db import DB_PATH  # noqa: E402

REVIEWS_DIR = ROOT / "data" / "maps_reviews_raw"
NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def parse_iso_date(iso: str | None) -> str | None:
    """ISO 8601 (with optional milliseconds + Z) → YYYY-MM-DD."""
    if not iso or not isinstance(iso, str):
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", iso)
    return m.group(1) if m else None


def parse_rating(v) -> int | None:
    if v is None: return None
    try: return int(v)
    except (TypeError, ValueError): pass
    try: return int(float(v))
    except (TypeError, ValueError): return None


def pick_input_file() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    candidates = sorted(REVIEWS_DIR.glob("reviews_*.json"), reverse=True)
    if not candidates:
        sys.exit("no reviews_*.json found in data/maps_reviews_raw/")
    return candidates[0]


def main():
    path = pick_input_file()
    print(f"loading: {path}")
    records = json.loads(path.read_text())
    if isinstance(records, dict):
        records = records.get("data") or records.get("results") or []
    print(f"  {len(records)} review records to process")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Pre-load place_id → restaurant_id map
    pid_to_rid = {
        r["gmaps_id"]: r["id"]
        for r in conn.execute("SELECT id, gmaps_id FROM restaurants WHERE gmaps_id IS NOT NULL")
    }
    print(f"  {len(pid_to_rid)} restaurants in DB with gmaps_id")

    inserted = duplicates = no_restaurant = no_text = no_pid = 0
    by_year: dict[str, int] = {}

    for r in records:
        if not isinstance(r, dict): continue
        pid = r.get("place_id")
        if not pid:
            no_pid += 1; continue
        rid = pid_to_rid.get(pid)
        if not rid:
            no_restaurant += 1; continue
        text = (r.get("review") or "").strip()
        if not text or len(text) < 10:
            no_text += 1; continue
        date = parse_iso_date(r.get("review_date"))
        rating = parse_rating(r.get("review_rating"))
        h = sha1(text)
        cur = conn.execute(
            """INSERT OR IGNORE INTO reviews
               (restaurant_id, source, review_date, rating, text, text_hash, scraped_at)
               VALUES (?, 'gmaps', ?, ?, ?, ?, ?)""",
            (rid, date, rating, text, h, NOW),
        )
        if cur.rowcount:
            inserted += 1
            if date: by_year[date[:4]] = by_year.get(date[:4], 0) + 1
        else:
            duplicates += 1

    conn.commit()
    print(f"\ninserted:        {inserted}")
    print(f"duplicates:      {duplicates}")
    print(f"no restaurant:   {no_restaurant}  (place_id not in DB — pre-tag the restaurants first)")
    print(f"no/short text:   {no_text}")
    print(f"missing pid:     {no_pid}")
    if by_year:
        print(f"\ninserted by year:")
        for y in sorted(by_year): print(f"  {y}: {by_year[y]}")

    # Overall reviews state
    total = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    gmaps_total = conn.execute("SELECT COUNT(*) FROM reviews WHERE source='gmaps'").fetchone()[0]
    dated = conn.execute("SELECT COUNT(*) FROM reviews WHERE review_date IS NOT NULL").fetchone()[0]
    print(f"\ntotal reviews now: {total}  (gmaps={gmaps_total}, dated={dated})")
    conn.close()


if __name__ == "__main__":
    main()
