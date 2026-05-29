#!/usr/bin/env python3
"""Filter the raw Google Maps neighborhood pulls down to lunch-relevant
fast-casual competitors. We want what a Chipotle/CAVA/Sweetgreen customer
might actually choose INSTEAD of fast-casual lunch — not dinner destinations
or fine dining.

Filter rules (cumulative):
  1. Drop permanently_closed or temporarily_closed
  2. Drop high price (lower-bound >= $30, or tier '$$$' / '$$$$')
  3. Drop dinner-only (no weekday opens before 1pm)
  4. Optional: drop low lunch occupancy (popular_times exists AND avg
     11am–2pm busyness < threshold)

Reads:  data/maps_raw/{city}_data.json
Writes: data/maps_raw/{city}_data_lunch.json    (kept records, parsed)
        data/maps_raw/{city}_filter_report.json (per-record reasoning)

Stats are printed to stdout. Re-run is idempotent.

Usage:
    python scripts/filter_lunch_competitors.py
    python scripts/filter_lunch_competitors.py --min-lunch-busyness 20
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "maps_raw"

CITIES = ["weho", "williamsburg", "mission"]
LUNCH_HOURS = {"11 am", "12 pm", "1 pm", "2 pm"}
PRICE_MAX_LOWER = 25       # accept up to $20-$25 range
PRICE_TIER_DROP = {"$$$", "$$$$"}

# Chains are trend FOLLOWERS, not leaders. Drop the obvious nationals so the
# review pool is indie food culture (where new flavors actually emerge).
# Name-substring match, lowercased.
CHAIN_DENYLIST = {
    # National casual / fast-casual chains
    "cheesecake factory", "olive garden", "applebee", "tgi friday", "red lobster",
    "outback steakhouse", "chili's", "buffalo wild wings", "p.f. chang", "pf chang",
    "panda express", "yoshinoya", "panera", "potbelly", "qdoba",
    # National QSR
    "mcdonald", "burger king", "wendy", "popeye", "kfc", "taco bell",
    "in-n-out", "in n out", "five guys", "shake shack", "raising cane", "carl's jr",
    "jack in the box", "del taco", "el pollo loco", "subway", "jersey mike",
    "quiznos", "jimmy john", "firehouse subs", "arby", "white castle",
    # Breakfast / diner chains
    "ihop", "denny's", "dennys", "waffle house", "first watch", "snooze",
    "norms restaurant", "norms ", "du-pars", "du pars", "duparrs",
    "cracker barrel", "perkins",
    # Coffee chains (don't drive lunch trends)
    "starbucks", "peet's coffee", "peets coffee", "philz", "blue bottle",
    "la colombe", "joe coffee", "pret a manger", "le pain quotidien",
    "tim hortons", "dunkin",
    # Other fast-casual chains (our 3 brand + competitors)
    "chipotle", "cava", "sweetgreen", "tender greens", "sweetfin",
    "tocaya", "veggie grill", "mendocino farms", "blaze pizza",
    "&pizza", "and pizza", "&izza", "mod pizza", "pieology",
    "chopt", "just salad", "fresh&co", "cava grill",
    # Chain pizza
    "pizza hut", "domino", "papa john", "little caesars", "round table pizza",
    # Smoothie / juice
    "jamba juice", "smoothie king", "robeks", "playa bowls",
    # Mexican fast-food chains
    "rubio's", "rubios", "baja fresh",
}

# Categories that aren't real lunch-substitute competitors regardless of price
NON_COMPETITOR_CATEGORIES = {
    "bakery", "pastry shop", "ice cream shop", "donut shop", "donut restaurant",
    "candy store", "chocolate shop", "coffee shop", "tea house", "cafe ",
    "boba shop", "bubble tea shop", "smoothie shop", "juice shop",
    "pharmacy", "wine bar", "cocktail bar", "sports bar", "bar & grill",
    "night club", "lounge",
}


def normalize_text(s: str) -> str:
    if not s:
        return ""
    # Narrow no-break space (U+202F) + en-dash (U+2013) → standard whitespace + '-'
    return s.replace(" ", " ").replace("–", "-").lower().strip()


def parse_price(business_details: list | None) -> tuple[str | None, int | None]:
    """Returns (tier_string, lower_dollar_bound). Either may be None."""
    if not business_details:
        return None, None
    raw = None
    for entry in business_details:
        if isinstance(entry, dict) and entry.get("field_name") == "price_range":
            raw = entry.get("details")
            break
    if not raw:
        return None, None
    raw_n = normalize_text(raw)
    # Pure tier: "$", "$$", "$$$", "$$$$"
    if re.fullmatch(r"\$+", raw_n):
        return raw_n, None
    # Ranges like "$10-20", "10-20 $", "$100+", "us$20-30"
    m = re.search(r"(\d+)\s*(?:-|to|–)\s*(\d+)", raw_n)
    if m:
        return None, int(m.group(1))
    m = re.search(r"(\d+)\s*\+", raw_n)
    if m:
        return None, int(m.group(1))
    m = re.search(r"\$\s*(\d+)", raw_n)
    if m:
        return None, int(m.group(1))
    return raw_n, None


def opens_before_1pm(open_hours: dict | None) -> bool | None:
    """Returns True if any weekday opens at noon or earlier, False if dinner-only,
    None if no hours data."""
    if not open_hours:
        return None
    for day, hours in open_hours.items():
        if not isinstance(hours, str):
            continue
        h = normalize_text(hours)
        if any(w in h for w in ("closed", "24 hours", "open 24")):
            if "24" in h:
                return True
            continue
        # Get opening time = first hour mention in the string
        m = re.search(r"(\d+)(?::(\d+))?\s*(am|pm)", h)
        if not m:
            continue
        hour = int(m.group(1))
        ampm = m.group(3)
        if ampm == "am":
            return True
        # pm — opens between 12 and 12:59 counts; 1pm sharp also fine
        if hour == 12 or hour == 1:
            return True
    return False


def lunch_busyness(popular_times: list | None) -> float | None:
    """Returns avg percent busyness during 11am-2pm across days. None if no data."""
    if not popular_times:
        return None
    samples = []
    for day in popular_times:
        if not isinstance(day, dict):
            continue
        for slot in day.get("time", []) or []:
            hr = normalize_text(slot.get("hour", ""))
            if hr in LUNCH_HOURS:
                try:
                    samples.append(int(slot.get("percent", 0)))
                except (TypeError, ValueError):
                    pass
    if not samples:
        return 0.0
    return round(sum(samples) / len(samples), 1)


def classify(record: dict, min_lunch_busyness: float) -> tuple[bool, str, dict]:
    """Returns (keep, reason_if_dropped, signal_dict)."""
    sig = {
        "name": record.get("name"),
        "category": record.get("category"),
        "price_tier": None,
        "price_lower": None,
        "opens_before_1pm": None,
        "lunch_busyness": None,
    }
    if record.get("permanently_closed") or record.get("temporarily_closed"):
        return False, "closed", sig

    tier, lower = parse_price(record.get("business_details"))
    sig["price_tier"] = tier
    sig["price_lower"] = lower

    if tier in PRICE_TIER_DROP:
        return False, f"high_price_tier:{tier}", sig
    if lower is not None and lower >= 30:
        return False, f"high_price_lower:{lower}", sig

    opens_early = opens_before_1pm(record.get("open_hours"))
    sig["opens_before_1pm"] = opens_early
    if opens_early is False:
        return False, "dinner_only", sig

    busy = lunch_busyness(record.get("popular_times"))
    sig["lunch_busyness"] = busy
    if busy is not None and busy < min_lunch_busyness:
        return False, f"low_lunch_busyness:{busy}", sig

    return True, "", sig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-lunch-busyness", type=float, default=0.0,
                    help="Drop if popular_times shows lunch busyness < this percent (default 0 = off)")
    args = ap.parse_args()

    print(f"Filter rules:")
    print(f"  - drop closed / temporarily closed")
    print(f"  - drop high price (tier $$$/$$$$ OR lower bound >= $30)")
    print(f"  - drop dinner-only (no weekday opens by 1pm)")
    print(f"  - drop low lunch busyness (< {args.min_lunch_busyness}%) — only if popular_times present\n")

    for city in CITIES:
        in_path = DATA / f"{city}_data.json"
        if not in_path.exists():
            print(f"[{city}] no raw data file, skipping")
            continue
        records = json.loads(in_path.read_text())
        kept = []
        report = []
        reason_counts = {}

        for r in records:
            keep, reason, sig = classify(r, args.min_lunch_busyness)
            report.append({**sig, "kept": keep, "reason": reason})
            if keep:
                kept.append(r)
            else:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1

        (DATA / f"{city}_data_lunch.json").write_text(json.dumps(kept, indent=2))
        (DATA / f"{city}_filter_report.json").write_text(json.dumps(report, indent=2))

        print(f"\n=== {city} ===")
        print(f"  total:  {len(records)}")
        print(f"  kept:   {len(kept)}")
        print(f"  dropped: {len(records) - len(kept)}")
        for reason, n in sorted(reason_counts.items(), key=lambda x: -x[1]):
            print(f"    {reason:<30}  {n}")

        # Top 8 kept restaurants by review count, for sanity
        kept_sorted = sorted(kept, key=lambda r: r.get("reviews_count", 0), reverse=True)[:8]
        print(f"\n  Top 8 kept (by review count):")
        for r in kept_sorted:
            tier, lower = parse_price(r.get("business_details"))
            opens = opens_before_1pm(r.get("open_hours"))
            busy = lunch_busyness(r.get("popular_times"))
            print(f"    {r.get('reviews_count',0):>5}  {r['name'][:40]:<42} "
                  f"[{r.get('category','?')[:25]:<25}] "
                  f"tier={tier or 'lo='+str(lower) if lower else tier} "
                  f"opens_AM={opens} lunch%={busy}")


if __name__ == "__main__":
    main()
