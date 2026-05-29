#!/usr/bin/env python3
"""Classify each Google Maps restaurant by pool membership and write tags
back to the DB.

Two pools — see HANDOFF for rationale:
  pool_competitive : indie + opens by 1pm + price lower-bound < $25
                     ("who is your customer choosing instead at lunch today?")
  pool_leading     : indie + (dinner-only OR price lower-bound >= $25)
                     ("what is the chef-driven scene popularizing for 2 years out?")

Chain restaurants drop from BOTH pools (chains follow trends, they don't drive them).
A mid-priced indie open for lunch can land in pool A only. A $40-entree
chef-driven dinner spot can land in pool B only. They aren't mutually exclusive
in principle but in practice rarely overlap.

Reads:  data/maps_raw/{city}_data.json
Writes: brand_menu_items-style ALTER + UPDATE on `restaurants` table

Usage:
    python scripts/tag_restaurants.py
    python scripts/tag_restaurants.py --dry-run
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from hermes.db import DB_PATH  # noqa: E402

MAPS = ROOT / "data" / "maps_raw"
CITIES = ["weho", "williamsburg", "mission"]
LUNCH_HOURS = {"11 am", "12 pm", "1 pm", "2 pm"}

CHAIN_DENYLIST = {
    # National casual / fast-casual chains
    "cheesecake factory", "olive garden", "applebee", "tgi friday", "red lobster",
    "outback steakhouse", "chili's", "buffalo wild wings", "p.f. chang", "pf chang",
    "panda express", "yoshinoya", "panera", "potbelly", "qdoba",
    # National QSR + diner
    "mcdonald", "burger king", "wendy", "popeye", "kfc", "taco bell",
    "in-n-out", "in n out", "five guys", "shake shack", "raising cane", "carl's jr",
    "jack in the box", "del taco", "el pollo loco", "subway", "jersey mike",
    "quiznos", "jimmy john", "firehouse subs", "arby", "white castle",
    "ihop", "denny's", "dennys", "waffle house", "first watch", "snooze",
    "norms restaurant", "norms ", "du-pars", "du pars", "cracker barrel", "perkins",
    # Coffee + breakfast chains (don't drive lunch flavor trends)
    "starbucks", "peet's coffee", "peets coffee", "philz", "blue bottle",
    "la colombe", "joe coffee", "pret a manger", "le pain quotidien",
    "tim hortons", "dunkin",
    # Fast-casual + our 3 brand chains
    "chipotle", "cava", "sweetgreen", "tender greens", "sweetfin",
    "tocaya", "veggie grill", "mendocino farms", "blaze pizza",
    "&pizza", "and pizza", "mod pizza", "pieology",
    "chopt", "just salad", "fresh&co",
    # Chain pizza + smoothie + Mex-fast-food
    "pizza hut", "domino", "papa john", "little caesars", "round table pizza",
    "jamba juice", "smoothie king", "robeks", "playa bowls",
    "rubio's", "rubios", "baja fresh",
}

NON_COMPETITOR_CATEGORIES = {
    "bakery", "pastry shop", "ice cream shop", "donut shop", "donut restaurant",
    "candy store", "chocolate shop", "coffee shop", "tea house",
    "boba shop", "bubble tea shop", "smoothie shop", "juice shop",
    "pharmacy", "wine bar", "cocktail bar", "night club", "lounge",
}


def normalize_text(s: str) -> str:
    if not s:
        return ""
    return s.replace(" ", " ").replace("–", "-").lower().strip()


def parse_price_lower(business_details: list | None) -> int | None:
    if not business_details:
        return None
    raw = None
    for entry in business_details:
        if isinstance(entry, dict) and entry.get("field_name") == "price_range":
            raw = entry.get("details")
            break
    if not raw:
        return None
    raw_n = normalize_text(raw)
    if re.fullmatch(r"\$+", raw_n):
        # Tier mapping: $ -> 10, $$ -> 20, $$$ -> 40, $$$$ -> 60
        return {"$": 10, "$$": 20, "$$$": 40, "$$$$": 60}.get(raw_n)
    m = re.search(r"(\d+)\s*(?:-|to)\s*(\d+)", raw_n)
    if m: return int(m.group(1))
    m = re.search(r"(\d+)\s*\+", raw_n)
    if m: return int(m.group(1))
    m = re.search(r"\$\s*(\d+)", raw_n)
    if m: return int(m.group(1))
    return None


def opens_before_1pm(open_hours: dict | None) -> bool | None:
    if not open_hours: return None
    for day, hours in open_hours.items():
        if not isinstance(hours, str): continue
        h = normalize_text(hours)
        if "24" in h and ("hour" in h or "open" in h): return True
        if "closed" in h: continue
        m = re.search(r"(\d+)(?::(\d+))?\s*(am|pm)", h)
        if not m: continue
        hour, ampm = int(m.group(1)), m.group(3)
        if ampm == "am": return True
        if hour in (12, 1): return True
    return False


def lunch_busyness(popular_times: list | None) -> float | None:
    if not popular_times: return None
    samples = []
    for day in popular_times:
        if not isinstance(day, dict): continue
        for slot in day.get("time", []) or []:
            hr = normalize_text(slot.get("hour", ""))
            if hr in LUNCH_HOURS:
                try: samples.append(int(slot.get("percent", 0)))
                except (TypeError, ValueError): pass
    if not samples: return None
    return round(sum(samples) / len(samples), 1)


def is_chain(name: str, category: str | None) -> bool:
    n = (name or "").lower()
    if any(c in n for c in CHAIN_DENYLIST): return True
    if category:
        cat_n = category.lower()
        if any(c in cat_n for c in NON_COMPETITOR_CATEGORIES): return True
    return False


def classify_pools(record: dict) -> tuple[bool, bool, dict]:
    """Returns (in_pool_competitive, in_pool_leading, signal_dict)."""
    if record.get("permanently_closed") or record.get("temporarily_closed"):
        return False, False, {"closed": True}

    name = record.get("name") or ""
    cat = record.get("category")
    if is_chain(name, cat):
        return False, False, {"is_chain": True}

    price = parse_price_lower(record.get("business_details"))
    opens_early = opens_before_1pm(record.get("open_hours"))
    busy = lunch_busyness(record.get("popular_times"))

    signals = {
        "price_lower": price,
        "opens_before_1pm": opens_early,
        "lunch_busyness": busy,
        "category": cat,
    }

    # POOL A: competitive lunch set
    competitive = (
        opens_early is True
        and (price is None or price < 25)
    )
    # POOL B: leading-indicator set (chef-driven dinner OR upscale)
    leading = (
        opens_early is False
        or (price is not None and price >= 25)
    )
    return competitive, leading, signals


def ensure_columns(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(restaurants)")}
    schema_adds = [
        ("price_lower",       "INTEGER"),
        ("opens_before_1pm",  "INTEGER"),
        ("lunch_busyness",    "REAL"),
        ("is_chain",          "INTEGER NOT NULL DEFAULT 0"),
        ("pool_competitive",  "INTEGER NOT NULL DEFAULT 0"),
        ("pool_leading",      "INTEGER NOT NULL DEFAULT 0"),
        ("gmaps_category",    "TEXT"),
    ]
    for col, decl in schema_adds:
        if col not in cols:
            conn.execute(f"ALTER TABLE restaurants ADD COLUMN {col} {decl}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if not args.dry_run:
        ensure_columns(conn)

    grand = {"closed": 0, "chain": 0, "comp_only": 0, "leading_only": 0,
             "both": 0, "neither": 0, "total": 0}

    for city in CITIES:
        path = MAPS / f"{city}_data.json"
        if not path.exists():
            print(f"[{city}] no raw data file, skipping"); continue
        records = json.loads(path.read_text())

        stats = {"closed": 0, "chain": 0, "comp_only": 0, "leading_only": 0,
                 "both": 0, "neither": 0, "total": len(records)}
        comp_list, lead_list = [], []

        for rec in records:
            comp, lead, sig = classify_pools(rec)
            if sig.get("closed"): stats["closed"] += 1
            elif sig.get("is_chain"): stats["chain"] += 1
            elif comp and lead:
                stats["both"] += 1
                comp_list.append(rec); lead_list.append(rec)
            elif comp:
                stats["comp_only"] += 1
                comp_list.append(rec)
            elif lead:
                stats["leading_only"] += 1
                lead_list.append(rec)
            else:
                stats["neither"] += 1

            # DB update (skip if dry-run)
            if not args.dry_run:
                gmaps_id = rec.get("place_id")
                if gmaps_id:
                    conn.execute(
                        """UPDATE restaurants SET
                            price_lower = ?,
                            opens_before_1pm = ?,
                            lunch_busyness = ?,
                            is_chain = ?,
                            pool_competitive = ?,
                            pool_leading = ?,
                            gmaps_category = ?
                           WHERE gmaps_id = ?""",
                        (
                            sig.get("price_lower"),
                            1 if sig.get("opens_before_1pm") is True else (0 if sig.get("opens_before_1pm") is False else None),
                            sig.get("lunch_busyness"),
                            1 if sig.get("is_chain") else 0,
                            1 if comp else 0,
                            1 if lead else 0,
                            sig.get("category"),
                            gmaps_id,
                        ),
                    )

        print(f"\n=== {city} ===")
        print(f"  total:           {stats['total']}")
        print(f"  closed:          {stats['closed']}")
        print(f"  chains dropped:  {stats['chain']}")
        print(f"  pool A only:     {stats['comp_only']}  (competitive lunch)")
        print(f"  pool B only:     {stats['leading_only']}  (leading-indicator)")
        print(f"  both pools:      {stats['both']}")
        print(f"  neither:         {stats['neither']}  (closed-ish / no signal)")
        print(f"  → Pool A total:  {stats['comp_only'] + stats['both']}")
        print(f"  → Pool B total:  {stats['leading_only'] + stats['both']}")

        # Sanity: top 6 per pool
        if comp_list:
            comp_top = sorted(comp_list, key=lambda r: r.get("reviews_count", 0), reverse=True)[:6]
            print(f"\n  Pool A — top competitive lunch (by review count):")
            for r in comp_top:
                price = parse_price_lower(r.get("business_details"))
                busy = lunch_busyness(r.get("popular_times"))
                print(f"    {r.get('reviews_count',0):>5}  {r['name'][:38]:<40} [{r.get('category','?')[:25]}] "
                      f"price=${price}  lunch%={busy}")
        if lead_list:
            lead_top = sorted(lead_list, key=lambda r: r.get("reviews_count", 0), reverse=True)[:6]
            print(f"\n  Pool B — top leading-indicator (by review count):")
            for r in lead_top:
                price = parse_price_lower(r.get("business_details"))
                opens = opens_before_1pm(r.get("open_hours"))
                print(f"    {r.get('reviews_count',0):>5}  {r['name'][:38]:<40} [{r.get('category','?')[:25]}] "
                      f"price=${price}  opens_AM={opens}")

        for k in stats:
            if k in grand: grand[k] += stats[k]
            else: grand[k] = grand.get(k, 0) + stats[k]

    if not args.dry_run:
        conn.commit()
        print("\n=== DB updated ===")
        for r in conn.execute("""
            SELECT city,
                   SUM(pool_competitive) AS comp,
                   SUM(pool_leading) AS lead,
                   SUM(is_chain) AS chains
            FROM restaurants
            WHERE gmaps_id IS NOT NULL
            GROUP BY city ORDER BY city
        """):
            print(f"  {r['city']:<22}  pool_competitive={r['comp']:>3}  pool_leading={r['lead']:>3}  chains={r['chains']:>3}")
    conn.close()


if __name__ == "__main__":
    main()
