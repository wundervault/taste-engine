#!/usr/bin/env python3
"""LLM dish generator.

Reads pantries, existing dishes, and cross-source signal scores from the DB
(not JSON files) so it sees the corrected CAVA/Chipotle/Sweetgreen pantries,
BYO catalog, and trend×review-mentions rankings. Emits 2 dishes per brand
for the chosen city.

Usage:
    python scripts/dish_generator.py weho
    python scripts/dish_generator.py williamsburg
"""
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from hermes.db import init_db  # noqa: E402

DATA = ROOT / "data"

CITIES = {
    "weho": {
        "city": "West Hollywood",
        "geo": "US-CA-803",  # LA Metro DMA
        "context": (
            "WeHo food culture: health-conscious, Mediterranean-leaning, "
            "plant-forward, high openness to global flavors (Korean, Lebanese, "
            "Mexican-fusion). Independent restaurants sampled: Gracias Madre "
            "(plant-based Mexican), Crossroads Kitchen (vegan Mediterranean), "
            "Open Sesame (Lebanese), Moonbowls (Korean bowls)."
        ),
    },
    "williamsburg": {
        "city": "Williamsburg",
        "geo": "US-NY-501",  # New York DMA
        "context": (
            "Williamsburg, Brooklyn food culture: Caribbean + Israeli are the "
            "dominant indie identities; trendy, willing to pay for novelty. "
            "Independent restaurants sampled: Kokomo (Caribbean), K'Far + "
            "Laser Wolf (Israeli), Ensenada (coastal Mexican)."
        ),
    },
    "mission": {
        "city": "Mission District",
        "geo": "US-CA-807",  # San Francisco-Oakland-San Jose DMA
        "context": (
            "Mission District, San Francisco food culture: Mexican/taqueria "
            "institution (La Taqueria, El Farolito) layered with fusion + "
            "high-end (Lazy Bear, Foreign Cinema, Tartine). Strong burrito "
            "identity that puts pressure on Chipotle specifically. Trend-forward "
            "and willing to pay a premium for craft."
        ),
    },
}


# ---- DB readers ----------------------------------------------------------

def pantry_for(conn, brand: str) -> dict:
    """Return {category: [items]} for one brand, only available=1 rows.

    Sweetgreen: prefer the BYO catalog (authoritative SKU list) over the
    parsed-from-dish rows, which double-count and include parser noise like
    `just 6g of sugar` or `avocado oil`.
    """
    if brand == "Sweetgreen":
        cat_filter = "AND category LIKE 'sweetgreen_byo_%'"
    else:
        cat_filter = "AND category != 'dish'"
    out: dict[str, list[str]] = {}
    for r in conn.execute(
        f"SELECT category, item FROM brand_menu_items "
        f"WHERE brand = ? AND available = 1 {cat_filter} "
        f"ORDER BY category, item",
        (brand,),
    ):
        out.setdefault(r["category"], []).append(r["item"])
    return out


def existing_dishes(conn, brand: str) -> list[str]:
    rows = conn.execute(
        "SELECT item FROM brand_menu_items WHERE brand = ? AND category = 'dish' ORDER BY item",
        (brand,),
    ).fetchall()
    return [r["item"] for r in rows]


_CITY_LABELS = {
    "weho":         "West Hollywood",
    "williamsburg": "Williamsburg",
    "mission":      "Mission District",
}


def signal_ranking(conn, city: str, geo: str, limit: int = 12,
                   pool: str | None = None) -> list[dict]:
    """Cross-source signal score: DMA-level Google Trends × city-scoped local review mentions.

    pool:
        None              — count mentions from ALL restaurants in the city
        "competitive"     — restrict to pool_competitive=1 (indie lunch set)
        "leading"         — restrict to pool_leading=1 (indie chef-driven set)

    City-scoping + pool-scoping happen in a subquery so the trends JOIN can
    stay LEFT (keeps trend-only flavors with 0 local mentions visible).

    `city` may be a short key ("mission") or the full label ("Mission District");
    both are accepted — keys are normalized to labels here so the SQL WHERE clause
    matches the restaurants.city column.
    """
    city = _CITY_LABELS.get(city, city)
    pool_filter = ""
    if pool == "competitive":
        pool_filter = "AND r.pool_competitive = 1"
    elif pool == "leading":
        pool_filter = "AND r.pool_leading = 1"
    q = f"""
    SELECT
      t.term,
      t.avg_12m AS trend,
      COALESCE(s.mentions, 0)    AS mentions,
      COALESCE(s.reviews_hit, 0) AS reviews_hit,
      ROUND(
        (t.avg_12m / 100.0) * 0.5
        + (CASE WHEN COALESCE(s.mentions, 0) > 30 THEN 1.0
                ELSE COALESCE(s.mentions, 0) / 30.0 END) * 0.5,
        3
      ) AS signal_score
    FROM trends t
    LEFT JOIN (
      SELECT fm.flavor,
             COUNT(DISTINCT rv.id) AS mentions,
             COUNT(DISTINCT rv.id) AS reviews_hit
      FROM flavor_mentions fm
      JOIN reviews     rv ON rv.id = fm.review_id
      JOIN restaurants r  ON r.id  = rv.restaurant_id
      WHERE r.city = ? {pool_filter}
      GROUP BY fm.flavor
    ) s ON s.flavor = t.term
    WHERE t.geo = ?
    ORDER BY signal_score DESC
    LIMIT ?
    """
    return [dict(r) for r in conn.execute(q, (city, geo, limit))]


def signal_ranking_dual(conn, city: str, geo: str, limit: int = 20) -> list[dict]:
    """Returns top flavors with BOTH pool scores side-by-side, sorted by
    max(competitive_score, leading_score) so leaders surface no matter which
    pool they live in."""
    comp = {r["term"]: r for r in signal_ranking(conn, city, geo, limit=50, pool="competitive")}
    lead = {r["term"]: r for r in signal_ranking(conn, city, geo, limit=50, pool="leading")}
    all_terms = sorted(set(comp) | set(lead))
    out = []
    for term in all_terms:
        c = comp.get(term, {})
        L = lead.get(term, {})
        out.append({
            "term": term,
            "trend": c.get("trend") or L.get("trend"),
            "competitive_score":    c.get("signal_score", 0.0),
            "competitive_mentions": c.get("mentions", 0),
            "competitive_reviews":  c.get("reviews_hit", 0),
            "leading_score":        L.get("signal_score", 0.0),
            "leading_mentions":     L.get("mentions", 0),
            "leading_reviews":      L.get("reviews_hit", 0),
        })
    out.sort(key=lambda r: max(r["competitive_score"] or 0, r["leading_score"] or 0), reverse=True)
    return out[:limit]


# ---- prompt assembly -----------------------------------------------------

def fmt_pantry(brand: str, pantry: dict) -> str:
    lines = [f"### {brand.upper()}"]
    for cat in sorted(pantry):
        # Skip Sweetgreen's parsed `sweetgreen_*` rows in favor of the BYO catalog
        # if both are present — BYO is the live SKU truth.
        items = ", ".join(pantry[cat])
        lines.append(f"  [{cat}] {items}")
    return "\n".join(lines)


def fmt_existing(brand: str, dishes: list[str]) -> str:
    if not dishes:
        return (
            f"{brand}: assembly-line brand — there's no fixed dish list to dedupe "
            f"against. Treat the pantry block above as the full constraint set; "
            f"propose any combination as long as it uses only listed SKUs."
        )
    return f"{brand} ({len(dishes)} current dishes): " + "; ".join(dishes)


def fmt_signal(rows: list[dict]) -> str:
    return "\n".join(
        f"  {r['term']:<14} score={r['signal_score']:.2f}  "
        f"(trend={r['trend']}, local_mentions={r['mentions']} across {r['reviews_hit']} reviews)"
        for r in rows
    )


def load_cuisine_data():
    """Load family map + compatibility matrix for prompt-time co-flavor filtering."""
    fam_path = ROOT / "data" / "flavor_cuisine_families.json"
    compat_path = ROOT / "data" / "cuisine_compatibility.json"
    if not fam_path.exists() or not compat_path.exists():
        return {}, {}
    families = json.loads(fam_path.read_text())["families"]
    compat = json.loads(compat_path.read_text())["scores"]
    return families, compat


def compatible_vocab_for(anchor: str, vocab_terms: list[str],
                         families: dict, compat: dict,
                         min_score: float = 0.7) -> list[tuple[str, float]]:
    """Return (vocab, score) for vocab flavors that pair with anchor at ≥ min_score."""
    if not families or anchor not in families:
        return []
    anchor_fams = [families[anchor]["primary"]] + (families[anchor].get("secondary") or [])
    out = []
    for vocab in vocab_terms:
        if vocab == anchor or vocab not in families:
            continue
        v_fams = [families[vocab]["primary"]] + (families[vocab].get("secondary") or [])
        best = -1.0
        for a in anchor_fams:
            for v in v_fams:
                key1 = f"{a}|{v}"
                key2 = f"{v}|{a}"
                s = max(compat.get(key1, 0), compat.get(key2, 0))
                if a == v:
                    s = max(s, 1.0)
                if s > best:
                    best = s
        if best >= min_score:
            out.append((vocab, round(best, 2)))
    out.sort(key=lambda x: -x[1])
    return out


def load_lto_history() -> dict:
    """Newsroom-verified LTO history per brand. Used to surface proven-execution
    framing — when a brand has shipped a flavor before, the relaunch story is
    stronger than a cold-start recommendation."""
    path = ROOT / "data" / "brand_lto_history.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def fmt_lto(brand: str, lto_entries: list[dict]) -> str:
    if not lto_entries:
        return f"### {brand.upper()}\n  (no documented LTO history in our catalog)"
    lines = [f"### {brand.upper()}"]
    for entry in lto_entries:
        years = ", ".join(str(y) for y in entry.get("shipped_years", []))
        status = entry.get("current_status", "?")
        in_pantry = "yes" if entry.get("still_in_pantry") else "dormant"
        tags = ", ".join(entry.get("flavor_tags") or [])
        lines.append(
            f"  • {entry['item_name']} — shipped {years} ({status}, pantry {in_pantry})"
            + (f"  [tags: {tags}]" if tags else "")
        )
    return "\n".join(lines)


def build_prompt(city_key: str) -> tuple[str, str]:
    """Returns (static_prefix, dynamic_suffix).

    Static prefix = role + pantries + existing dishes + task spec.
    Cacheable across cities + re-runs.

    Dynamic suffix = per-city deliverable-flavor and gap-fill candidates per brand.
    The model can only propose ship-now dishes for flavors a brand can HONESTLY
    deliver from current pantry; gap-fill recommendations name the missing SKUs.
    """
    spec = CITIES[city_key]
    pantry_fit_path = ROOT / "data" / "pantry_fit.json"
    pantry_fit = json.loads(pantry_fit_path.read_text()) if pantry_fit_path.exists() else {}

    conn = init_db()
    try:
        brands = ["Chipotle", "CAVA", "Sweetgreen"]
        pantries = {b: pantry_for(conn, b) for b in brands}
        existing = {b: existing_dishes(conn, b) for b in brands}
        # Pull a deep ranking so each brand has enough flavors to choose from
        # even when its pantry doesn't intersect the top of the list
        signals = signal_ranking(conn, spec["city"], spec["geo"], limit=40)
    finally:
        conn.close()

    pantry_blocks = "\n\n".join(fmt_pantry(b, pantries[b]) for b in brands)
    existing_blocks = "\n".join(fmt_existing(b, existing[b]) for b in brands)

    # LTO history per brand — surfaces proven execution as a relaunch lever
    lto_history = load_lto_history()
    lto_blocks = "\n\n".join(
        fmt_lto(b, lto_history.get(b.lower(), [])) for b in brands
    )

    # Cuisine coherence map per anchor candidate per brand
    families, compat = load_cuisine_data()
    vocab_terms = list(families.keys()) if families else []

    # Per-brand deliverable + gap-fill blocks for the dynamic suffix
    per_brand_blocks = []
    for brand in brands:
        brand_fit = pantry_fit.get(brand, {})
        deliverable_signals = [
            s for s in signals
            if brand_fit.get(s["term"], {}).get("deliverable", False)
        ]
        # Top 8 gap candidates (undeliverable but high-signal), sorted by score,
        # filtered to those with ≤3 missing SKUs (operationally feasible)
        gap_signals = [
            (s, brand_fit.get(s["term"], {}))
            for s in signals[:15]
            if not brand_fit.get(s["term"], {}).get("deliverable", False)
            and 0 < len(brand_fit.get(s["term"], {}).get("missing", [])) <= 3
        ]

        # For each deliverable anchor, surface its top compatible co-flavors
        # so the LLM doesn't fuse incompatible cuisines (miso-pesto problem).
        def _coflav_str(anchor: str) -> str:
            pairs = compatible_vocab_for(anchor, vocab_terms, families, compat,
                                         min_score=0.7)
            if not pairs:
                return ""
            top = ", ".join(f"{p[0]}" for p in pairs[:6])
            return f"\n      compatible co-flavors: {top}"

        deliverable_lines = "\n".join(
            f"    {s['term']:<14} score={s['signal_score']:.2f}  "
            f"(pantry match: {', '.join(brand_fit.get(s['term'], {}).get('matched', []))})"
            + (_coflav_str(s['term']) if families else "")
            for s in deliverable_signals[:10]
        )
        if not deliverable_lines:
            deliverable_lines = "    (no high-signal flavors in current pantry — pick the closest match from rank 11-40)"

        gap_lines = "\n".join(
            f"    {s['term']:<14} score={s['signal_score']:.2f}  "
            f"missing SKUs: {', '.join(v.get('missing', []))}"
            for s, v in gap_signals[:5]
        )
        if not gap_lines:
            gap_lines = "    (no easy gap fills — every high-signal flavor needs many SKUs)"

        per_brand_blocks.append(
            f"### {brand}\n"
            f"  Ship-now (use ONLY these flavors — already in pantry):\n{deliverable_lines}\n"
            f"  Gap-fill candidates (high-signal, need SKU additions):\n{gap_lines}"
        )
    per_brand_section = "\n\n".join(per_brand_blocks)

    static_prefix = f"""You are a menu innovation consultant for fast-casual restaurant chains.

For each brand, produce TWO recommendations:
  • ONE **ship-now dish** that uses only flavors a brand can honestly deliver from
    its current pantry (the pre-computed deliverable list — DON'T pick a flavor
    not on that list, even if it's high-signal).
  • ONE **gap-fill recommendation** that names a high-signal flavor the brand
    cannot currently deliver, the specific SKUs it would need to add, and what
    dish/menu line that opens up.

No flavor-gesturing. If the dish is named "Mole Bowl" the pantry must contain
mole-the-sauce. If you can't deliver a flavor honestly from pantry, route it to
the gap-fill — don't dress a non-mole salsa as mole.

**Cuisine coherence is mandatory.** When you build a ship-now dish, the anchor
signal_term has a cuisine family (e.g., miso = Japanese, pesto = Italian).
Every other named flavor ingredient in the dish must come from a COMPATIBLE
cuisine family. The per-brand candidate list below shows "compatible co-flavors"
under each anchor — these are the ONLY flavor terms you may pair with that
anchor. Combining incompatible families (e.g., miso + pesto, harissa + gochujang)
is a hallucination class we explicitly catch and reject. If you genuinely
believe a cross-cuisine fusion is warranted (e.g., al pastor + kimchi via the
established KBBQ-taco lineage), you MUST add a "cross_family_justification"
field naming the recognized fusion tradition. No justification → no cross-family
combination.

**Dish-name truthfulness is mandatory.** Every flavor or ingredient word in
your dish name must point to a HEADLINE ingredient that appears in the
ingredients list AS A SEPARATELY-SERVED ELEMENT.

  - A flavor mentioned only in a parenthetical marinade or LTO note is NOT
    a headline element. Example: "chicken al pastor (LTO — pineapple, achiote,
    chipotle)" delivers chicken al pastor as a headline. Pineapple, achiote,
    and chipotle are BACKGROUND — they cannot appear in the dish name.
    "Al Pastor Pineapple Taco" is therefore misleading. Acceptable names are
    "Al Pastor Taco Plate" (drops the marinade-only word) with the tagline
    crediting "achiote-pineapple marinated chicken".

  - If you substitute one ingredient for another (e.g., sweet potato as a
    kabocha stand-in because the pantry has no kabocha), the dish name must
    reflect what is actually in the bowl. NOT "Miso Kabocha Crunch Bowl" with
    sweet potato ingredients — either "Miso Sweet Potato Crunch Bowl" or
    drop the substitute from the name.

  - Texture words like "Crunch", "Crispy" promise a crispy or crunchy
    ingredient on the plate. If there's no crispy element, drop the word.

  - Tagline can credit marinades, prep methods, and inspirations honestly
    ("achiote-pineapple marinated", "inspired by"). The dish_name cannot.

---

## BRAND PANTRIES (live, available SKUs only)

{pantry_blocks}

---

## EXISTING DISHES — DO NOT PROPOSE THESE OR NEAR-DUPLICATES

If your proposed dish is described by an existing dish's name/ingredients, it is NOT
a new recommendation — it is the existing dish. Pick something genuinely different.

{existing_blocks}

---

## BRAND LTO HISTORY (newsroom-verified)

These are LIMITED-TIME ITEMS the brand has shipped before. They are a strategic
asset: supply chain was sourced once, operations team has shipped it, recipe
exists. A relaunch in the right neighborhood at the right time is dramatically
cheaper than a cold-start gap fill. If a flavor in the per-brand candidate list
intersects this history, you MUST reference the LTO in the recommendation's
prose (signal_rank_note for ship_now, dish_potential or operational_lift for
gap_fill). Frame it as "relaunch timing intelligence", not net-new innovation.

{lto_blocks}

---

## YOUR TASK

For each of the 3 brands, output exactly 2 recommendations:

**(a) ship_now** — a dish concept built from current pantry:
  1. Name + tagline that honestly reflect the flavor (no gesturing).
  2. Ingredients drawn ONLY from the brand's pantry block above.
  3. signal_term MUST be from that brand's "Ship-now" list provided below.
  4. Acknowledge if the flavor is lower-ranked in this neighborhood
     (signal_rank_note field) — honest beats overstated.

**(b) gap_fill** — an addressable opportunity:
  1. Pick from the brand's "Gap-fill candidates" list below.
  2. Name the missing_skus exactly as listed.
  3. Describe what dish/menu line opens up after the SKU additions.

Output a JSON array of 6 objects (2 per brand). Each object uses one of these shapes:

ship_now shape:
{{
  "type": "ship_now",
  "brand": "Chipotle | CAVA | Sweetgreen",
  "dish_name": "...",
  "tagline": "...",
  "ingredients": ["only", "from", "the", "pantry"],
  "signal_term": "<exact term from this brand's ship-now list>",
  "signal_score": "<exact score from the list>",
  "signal_rank_note": "honest one-sentence note on where this ranks in the city",
  "novelty_check": "why this is distinct from EXISTING DISHES",
  "confidence": "HIGH | MID | LOW",
  "confidence_reason": "evidence — mention count, local context"
}}

gap_fill shape:
{{
  "type": "gap_fill",
  "brand": "Chipotle | CAVA | Sweetgreen",
  "target_flavor": "<exact term from this brand's gap-fill list>",
  "signal_score": "<exact score>",
  "missing_skus": ["copy", "exactly", "from", "the", "list"],
  "dish_potential": "what dish/menu line ships after the SKU additions",
  "operational_lift": "single sentence on prep complexity / line-friendliness",
  "comp_context": "what nearby indie restaurant is winning this flavor today (use local context)"
}}

Return only the JSON array, no markdown."""

    dynamic_suffix = f"""---

## LOCAL CONTEXT — {spec["city"]} ({spec["geo"]})

{spec["context"]}

---

## PER-BRAND DELIVERABLE + GAP-FILL CANDIDATES — {spec["city"]}

Scores are 0-1 cross-source signals (DMA Google Trends × local indie review mentions).
≥0.55 = strong signal; 0.35-0.55 = notable; 0.20-0.35 = emerging.

{per_brand_section}

---

Generate the 6 recommendations now."""

    return static_prefix, dynamic_suffix


# ---- runner --------------------------------------------------------------

def main():
    city_key = (sys.argv[1] if len(sys.argv) > 1 else "weho").lower()
    if city_key not in CITIES:
        sys.exit(f"unknown city: {city_key}. choices: {list(CITIES)}")

    static_prefix, dynamic_suffix = build_prompt(city_key)
    print(f"generating dishes for {CITIES[city_key]['city']}...\n")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=4000,
        messages=[{
            "role": "user",
            "content": [
                # Static block — pantries + existing dishes + task spec. Cached
                # so repeat runs (e.g. iterating across cities or tweaking the
                # dynamic suffix) hit the 5-min cache and pay ~10% of input cost.
                {"type": "text", "text": static_prefix,
                 "cache_control": {"type": "ephemeral"}},
                # Dynamic block — per-city context + signal ranking.
                {"type": "text", "text": dynamic_suffix},
            ],
        }],
    )
    raw = resp.content[0].text.strip()

    try:
        dishes = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        dishes = json.loads(m.group(0)) if m else []

    # v7 = LTO-aware prompt (post-2026-05-28). v5 preserved for naive-comparison
    # documentation; v6 is the enriched-by-script version.
    out_path = DATA / f"{city_key}_dish_recommendations_v7.json"
    out_path.write_text(json.dumps(dishes, indent=2))

    print("=" * 60)
    for d in dishes:
        rec_type = d.get("type", "ship_now")
        if rec_type == "ship_now":
            print(f"\n[SHIP-NOW] {d['brand'].upper()} — \"{d.get('dish_name','?')}\"")
            print(f"  {d.get('tagline','')}")
            print(f"  ingredients: {', '.join(d.get('ingredients',[]))}")
            print(f"  signal:      {d.get('signal_term')} ({d.get('signal_score')})")
            print(f"  rank note:   {d.get('signal_rank_note','')}")
            print(f"  novelty:     {d.get('novelty_check','')}")
            print(f"  confidence:  {d.get('confidence')} — {d.get('confidence_reason','')}")
        elif rec_type == "gap_fill":
            print(f"\n[GAP-FILL] {d['brand'].upper()} — add: {d.get('target_flavor','?')}")
            print(f"  signal:        {d.get('target_flavor')} ({d.get('signal_score')})")
            print(f"  missing SKUs:  {', '.join(d.get('missing_skus',[]))}")
            print(f"  dish potential: {d.get('dish_potential','')}")
            print(f"  op lift:       {d.get('operational_lift','')}")
            print(f"  comp context:  {d.get('comp_context','')}")
        else:
            print(f"\n[?] {d}")

    print(f"\nsaved → {out_path}")
    u = resp.usage
    cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
    print(f"tokens: input={u.input_tokens} output={u.output_tokens} "
          f"cache_read={cache_read} cache_write={cache_write}")


if __name__ == "__main__":
    main()
