#!/usr/bin/env python3
"""Load Google Maps restaurants + reviews from data/maps_raw/*_data.json
into the DB. Reviews are inserted with source='gmaps' alongside any prior
Yelp data — they don't collide because the dedup key is (restaurant_id,
source, text_hash).

Restaurants are matched by gmaps_id (place_id). If a Yelp-only row already
exists for the same physical restaurant, it stays as a separate row — we
don't attempt fuzzy name-matching across sources.

Idempotent: re-runs skip duplicate reviews via the text_hash UNIQUE constraint.

Usage:
    python scripts/load_maps_to_db.py            # all 3 cities
    python scripts/load_maps_to_db.py weho
"""
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from hermes.db import init_db  # noqa: E402

MAPS = ROOT / "data" / "maps_raw"

CITY_MAP = {
    "weho":         "West Hollywood",
    "williamsburg": "Williamsburg",
    "mission":      "Mission District",
}

MIN_REVIEW_COUNT = 100   # drop businesses with very thin signal
NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def parse_date(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        # ISO 8601 with Z or +HH:MM — keep just the date part
        return iso[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", iso) else None
    except Exception:
        return None


def parse_zip(addr: str | None) -> str | None:
    if not addr:
        return None
    m = re.search(r"\b(\d{5})(?:-\d{4})?\b", addr)
    return m.group(1) if m else None


def clean_cuisine(category: str | None) -> str | None:
    """'Modern European restaurant' -> 'Modern European'."""
    if not category:
        return None
    return re.sub(r"\s*restaurant\s*$", "", category, flags=re.I).strip() or category


def ensure_columns(conn):
    """Migrate restaurants table to also hold gmaps rating + review_count."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(restaurants)")}
    if "gmaps_rating" not in cols:
        conn.execute("ALTER TABLE restaurants ADD COLUMN gmaps_rating REAL")
    if "gmaps_review_count" not in cols:
        conn.execute("ALTER TABLE restaurants ADD COLUMN gmaps_review_count INTEGER")


def upsert_restaurant(conn, *, name, city, zip_, cuisine, gmaps_id, rating, review_count):
    row = conn.execute(
        "SELECT id FROM restaurants WHERE gmaps_id = ?", (gmaps_id,)
    ).fetchone()
    if row:
        # Update metadata; keep name/city intact in case of manual fixes
        conn.execute(
            "UPDATE restaurants SET gmaps_rating = ?, gmaps_review_count = ?, "
            "zip = COALESCE(zip, ?), cuisine = COALESCE(cuisine, ?) WHERE id = ?",
            (rating, review_count, zip_, cuisine, row["id"]),
        )
        return row["id"], False
    cur = conn.execute(
        """INSERT INTO restaurants (name, city, zip, cuisine, gmaps_id,
                                    gmaps_rating, gmaps_review_count)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (name, city, zip_, cuisine, gmaps_id, rating, review_count),
    )
    return cur.lastrowid, True


def insert_review(conn, *, restaurant_id, source, date, rating, text):
    text = (text or "").strip()
    if not text or len(text) < 20:
        return False
    h = sha1(text)
    cur = conn.execute(
        """INSERT OR IGNORE INTO reviews
           (restaurant_id, source, review_date, rating, text, text_hash, scraped_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (restaurant_id, source, date, rating, text, h, NOW),
    )
    return cur.rowcount == 1


def load_city(conn, city_key: str):
    path = MAPS / f"{city_key}_data.json"
    if not path.exists():
        print(f"  skip — no file at {path}")
        return
    city = CITY_MAP[city_key]
    records = json.loads(path.read_text())
    print(f"\n=== {city_key} ({city}) — {len(records)} raw records ===")

    new_restaurants = 0
    updated_restaurants = 0
    skipped_closed = 0
    skipped_thin = 0
    inserted_reviews = 0
    duplicate_reviews = 0

    for rec in records:
        if rec.get("permanently_closed"):
            skipped_closed += 1
            continue
        rc = rec.get("reviews_count") or 0
        if rc < MIN_REVIEW_COUNT:
            skipped_thin += 1
            continue

        rid, is_new = upsert_restaurant(
            conn,
            name=rec.get("name") or "(unknown)",
            city=city,
            zip_=parse_zip(rec.get("address")),
            cuisine=clean_cuisine(rec.get("category")),
            gmaps_id=rec["place_id"],
            rating=rec.get("rating"),
            review_count=rc,
        )
        if is_new:
            new_restaurants += 1
        else:
            updated_restaurants += 1

        # Top reviews — these have date + rating + content
        for tr in rec.get("top_reviews") or []:
            if insert_review(
                conn,
                restaurant_id=rid,
                source="gmaps",
                date=parse_date(tr.get("review_date")),
                rating=tr.get("rating"),
                text=tr.get("content"),
            ):
                inserted_reviews += 1
            else:
                duplicate_reviews += 1

        # Snippets — no date/rating, still load text for flavor extraction
        for sn in rec.get("reviews_snippets") or []:
            content = sn.get("content") if isinstance(sn, dict) else None
            if not content:
                continue
            if insert_review(
                conn,
                restaurant_id=rid,
                source="gmaps",
                date=None,
                rating=None,
                text=content,
            ):
                inserted_reviews += 1
            else:
                duplicate_reviews += 1

    conn.commit()
    print(f"  restaurants:  new={new_restaurants}  updated={updated_restaurants}")
    print(f"  skipped:      closed={skipped_closed}  thin(<{MIN_REVIEW_COUNT})={skipped_thin}")
    print(f"  reviews:      inserted={inserted_reviews}  duplicates={duplicate_reviews}")


def summary(conn):
    print("\n=== overall DB state ===")
    for r in conn.execute("""
        SELECT city, COUNT(*) AS n,
               SUM(CASE WHEN gmaps_id IS NOT NULL THEN 1 ELSE 0 END) AS from_gmaps,
               SUM(CASE WHEN yelp_slug IS NOT NULL THEN 1 ELSE 0 END) AS from_yelp
        FROM restaurants GROUP BY city ORDER BY city
    """):
        print(f"  {r['city']:<22} restaurants={r['n']:>4}  (gmaps={r['from_gmaps']}  yelp={r['from_yelp']})")
    for r in conn.execute("""
        SELECT source, COUNT(*) AS n FROM reviews GROUP BY source
    """):
        print(f"  reviews source={r['source']:<6} {r['n']}")


def main():
    targets = [sys.argv[1]] if len(sys.argv) > 1 else list(CITY_MAP)
    conn = init_db()
    try:
        ensure_columns(conn)
        for ck in targets:
            if ck not in CITY_MAP:
                print(f"unknown city: {ck}")
                continue
            load_city(conn, ck)
        summary(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
