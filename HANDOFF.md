# Taste Engine — Project Handoff

**As of 2026-05-27 (late session — major data + architecture revision).** Pick up cold: read this top to bottom, then `openclaw-changelog.md` for the chronological technical decision history. `hackathon-scope.md` is preserved for reference but several of its assumptions (Yelp-primary sourcing, state-level trends, gap-claim framing) are out of date.

---

## What we're building (one paragraph)

A neighborhood-aware menu generation system for Chipotle, CAVA, and Sweetgreen. For a target neighborhood, it cross-references **DMA-level Google Trends** with **local Google Maps reviews** to score flavors by both national trend strength and local mention frequency, then surfaces dish concepts the brand could ship using only its current pantry. Every recommendation is validated against the brand's existing menu before being labeled novel — no re-inventing what's already on the line. Hackathon novelty: real cross-source synthesis (not just side-by-side reporting) + pantry-constrained output + honest "this is corroboration, not a p-value" framing.

---

## Current status

**Phase 1 — Brand pantry capture:** ✅ COMPLETE. 240 SKU rows: Chipotle 27, CAVA 53 (rebuilt from 9 user-emailed BYO screenshots), Sweetgreen 160 (27 dishes + 66 parsed pantry + 67 BYO catalog).
**Phase 2 — Signal layer:** ✅ COMPLETE. DMA-level Google Trends (LA 803 / SF-Oakland 807 / NYC 501), 105 trend rows. **64,372 dated reviews** across 383 restaurants — from BD Google Maps Fast Maps Search (top_reviews) + BD Google Maps Reviews dataset (chronological, 365-day + 730-day pulls).
**Phase 3 — Flavor extraction + dual-pool signal scoring:** ✅ COMPLETE. Deterministic keyword extraction against 50-term vocabulary → 1,682+ mentions across 47 distinct flavors. **Cross-source signal score** = `(DMA trend / 100 × 0.5) + (min(local_mentions/30, 1) × 0.5)`, computed PER POOL: competitive (lunch indie) and leading (chef-driven indie), chains excluded from both.
**Phase 4 — Dish generator with anti-hallucination + pantry deliverability:** ✅ Generated 18 dishes (6 per city) via Opus 4.7 + prompt caching. `data/flavor_definitions.json` + `compute_pantry_fit.py` produce a deterministic deliverability matrix so dish-gen knows which (flavor, brand) pairs are honestly possible. Chipotle delivers 3/50 vocab flavors, CAVA 4/50, Sweetgreen 7/50.
**Phase 5 — Demo dashboard + executive overview:** ✅ Streamlit app at `localhost:8501` with 7 tabs. Velocity tab has 4 chronological charts (monthly multi-flavor, quarterly heatmap, cross-city single-flavor, competitive-vs-leading divergence). Overview tab driven by `data/exec_overviews.json` (subagent-generated per-city headlines, pantry-fit grades, opportunities, constraints, recommended actions).
**Phase 6 — Multi-snapshot velocity over time:** Not started (post-demo). Would require append-only `trends_history` + recurring fetches over weeks.

---

## Three target cities

| City | Brand stores | DMA | Restaurants | Dated reviews (2024-01 → 2026-05) | Pool C / L | Dominant signal |
|---|---|---|---|---|---|---|
| **West Hollywood** | Chipotle 8420 Beverly, Sweetgreen 8570 Sunset, CAVA 6200 Sunset | LA Metro 803 | 103 | **20,501** | 46 / 44 | truffle (0.72 C / 0.72 L) |
| **Williamsburg, Brooklyn** | All 3 on N 4th St | NY 501 | 150 | **31,010** | 83 / 53 | jerk (0.92 C / 0.92 L) |
| **Mission District, SF** | All 3 in/near 24th + Mission | SF-Oakland 807 | 138 | **12,861** | 91 / 44 | birria (0.67 C / 0.24 L) |

**Dual-pool methodology**: Every restaurant is tagged `pool_competitive` (indie + opens by 1pm + $-$$, i.e. "what's a customer choosing instead at lunch today") OR `pool_leading` (indie + dinner-only OR $$$+, i.e. "what's the chef-driven scene popularizing for ~2 years out"). Chains dropped from both pools. Signal scores computed per-pool — the gap between them is a structural proxy for trend maturity (Rising / Established / Peak / Steady / Weak).

---

## What got fixed this session (substantive bugs, not cleanup)

1. **Signal-ranking SQL bug** (load-bearing): the v3 query was `LEFT JOIN restaurants ... AND r.city = ?` which does NOT filter `flavor_mentions` — every "city" was getting the GLOBAL mention count. Patched to a city-scoped subquery. Cauliflower went from "16 everywhere" to actual values: 6 WeHo / 10 WB / 0 Mission.
2. **Dish-gen hallucinations caught and prevented**: v3 outputs claimed "Sweetgreen Miso Salmon + Crispy Rice Bowl", "KBBQ Chicken + Kimchi Crunch", and "CAVA needs harissa" as *novel* opportunities — but Sweetgreen already serves miso glazed salmon, has KBBQ dressing + apple kimchi sauce, and CAVA has harissa in four forms (dip, harissa honey chicken protein, hot harissa vinaigrette dressing, side harissa). The new prompt explicitly lists existing dishes as "DO NOT PROPOSE" anchors.
3. **CAVA pantry was wrong on 3 axes**: ~6 missing items (harissa honey chicken, glazed salmon, sides category), 5 naming mismatches (beluga lentils→black lentils, mixed greens→power greens, etc.), and the sides category wasn't in the loader vocabulary at all. Rebuilt from 9 user-emailed BYO screenshots.
4. **State-level Google Trends was too coarse** for SF vs WeHo differentiation (both in US-CA). Refactored to DMA-level — SF-Oakland (807) now shows distinct pesto/farro signal vs LA Metro (803) which shows distinct mole/birria.
5. **Sweetgreen had duplicate pantry rows in the prompt** (`sweetgreen_*` parsed from dishes vs `sweetgreen_byo_*` from screenshots, with noise like `just 6g of sugar`, `puffed millet—naturally sweetened with honey date caramel`, `wild`). Prompt now filters to BYO-only.

---

## Pitch (revised 2026-05-27)

> *"Every existing trend tool tells you what's rising. None tell you what to ship. Taste Engine reads the local food culture of a single neighborhood — restaurant menus, review mentions, neighborhood cuisine mix — surfaces flavors where national trend strength and local review signal both agree, and outputs dish concepts that fit a brand's existing pantry. Every recommendation is validated against the brand's current menu before being labeled novel — no re-inventing what's already on the line."*

Two things this protects vs the original pitch:
1. **"Surfaces flavors where … both agree"** replaces "finds the gaps your competitors haven't filled." v3 outputs labeled existing menu items as novel gaps — the new framing is methodology, not oracle.
2. **"Validated against the brand's current menu"** — explicit pre-flight check is now in the generator's prompt (EXISTING DISHES — DO NOT PROPOSE section).

Use "signal strength score" (low/mid/high) backed by national Trends value × local review mention frequency. Honest: with ~30 reviews per indie restaurant, this is signal *corroboration*, not statistical significance. Production-scale would want 300+ reviews per zip, 12 months of date bins, and multiple zips per market.

---

## Data pipeline (current)

Five sources, four scripts, one SQLite DB.

| Source | Role | Script | Output |
|---|---|---|---|
| **DMA Google Trends** (pytrends) | National signal per flavor per DMA (LA 803, SF-Oakland 807, NYC 501) | `scripts/fetch_trends.py` | `trends` table — 105 rows (35 terms × 3 DMAs) |
| **Google Maps Fast Maps Search** (BD dataset `gd_m8ebnr0q2qlklc02fz`) | Restaurant discovery + embedded reviews per neighborhood | `scripts/fetch_maps_restaurants.py` + `scripts/load_maps_to_db.py` | `restaurants` (383 new) + `reviews` (4,170 gmaps) |
| **Yelp /biz/ pages** (BD Scraping Browser) | Original 8 restaurants' reviews; retained for historical comparison | `scripts/yelp_scraper_v2.py` | `reviews` (239 yelp, source='yelp') |
| **Brand JSON files + BYO screenshots** | Pantry capture for each of the 3 brands | `scripts/load_to_db.py` + `extract_sweetgreen_pantry.py` + `load_sweetgreen_byo.py` | `brand_menu_items` — 240 rows |
| **Curated flavor vocabulary** (50 terms = 35 trend-derived + 15 hand-curated) | Keyword-matching dictionary for flavor extraction | `scripts/extract_flavors.py` | `flavor_mentions` — 591 mentions across 418 reviews |

Single DB file: `data/hermes.db` (SQLite). Schema in `src/hermes/db.py`. The Python package is still named `hermes` internally; only the project folder was renamed.

### Dish generator (Phase 4 complete)

`scripts/dish_generator.py` reads pantry + existing dishes + signal-ranked flavors from DB. Uses **Opus 4.7 with prompt caching** (81% of prompt is static across cities + re-runs, ~70% input savings on cache hits).

```bash
set -a && source .env && set +a
.venv/bin/python scripts/dish_generator.py weho
.venv/bin/python scripts/dish_generator.py williamsburg
.venv/bin/python scripts/dish_generator.py mission
```

Output: `data/<city>_dish_recommendations_v4.json` — 6 dishes per city (2 per brand), with `signal_term`, `signal_score`, `novelty_check`, `confidence`. Token usage on first WeHo run: cache_write=719, cache_read=2946. Subsequent cities: cache_write=0 (full cache hit).

---

## What's ruled out (don't reconsider without new info)

| Approach | Why ruled out |
|---|---|
| BD SERP API for taste signal | Returns commerce-intent product pages, not local menus. Tried earlier in project — wrong tool. |
| Yelp search-results pages via BD Scraping Browser | DataDome aggressively blocks `/search?find_loc=...` pages (~1.7KB challenge HTML returned). Yelp `/biz/<slug>` pages work but require known slugs. Replaced by Google Maps Fast Maps Search which doesn't need pre-known IDs. |
| BD Yelp Dataset for sourcing | Per BD support chatbot 2026-05-27: filtering by neighborhood not confirmed in docs; would need a Custom Dataset support request. Slow turnaround; not viable for hackathon. |
| State-level Google Trends | SF and WeHo both in US-CA → identical trend rows, no cross-city differentiation. Replaced by DMA-level. |
| Reddit / TikTok / Instagram | Geo-filtering at zip/neighborhood level unreliable. |
| pytrends out-of-box | Broken on urllib3 ≥ 2.0 (`method_whitelist` removed). One-line source patch in `.venv/lib/python3.12/site-packages/pytrends/request.py` — `method_whitelist` → `allowed_methods`. |

---

## Environment + credentials

All secrets in `/home/zee/taste-engine/.env`:
- `API_TOKEN` — Bright Data account-level token (works for SERP, Web Unlocker, Dataset trigger)
- `BROWSER_WS` — Scraping Browser CDP endpoint (full credential with customer ID + zone + password)
- **`ANTHROPIC_API_KEY` — NOT YET SET. Required to run `dish_generator.py`.**

Vault entries (server-side, never echoed):
- `serp_api1` — original SERP zone secret, source of `API_TOKEN` value

**AdGuard quirk:** local AdGuard sinkholes `*.brightdata.com` to `0.0.0.0` at the **DNS rule level** — the "disable protection" toggle does NOT lift the rule. Confirmed via `dig api.brightdata.com` (returns 0.0.0.0) vs `dig @1.1.1.1 api.brightdata.com` (returns real IPs). Workaround baked into `fetch_maps_restaurants.py`: resolves IP via Cloudflare DNS at startup, uses `curl --resolve` for all BD REST calls. Permanent fix needs an AdGuard rule edit (`@@||brightdata.com^$important`) which requires sudo. Scraping Browser uses `brd.superproxy.io` and is NOT affected.

---

## Demo deliverable

A live demo showing:
1. Bright Data Google Maps returning real restaurant + review signal for a target neighborhood
2. Pantry blocks loaded from DB for one of the three chains (with availability flags)
3. Cross-source signal-strength ranking surfacing flavors per neighborhood (e.g. truffle dominates WeHo, jerk dominates Williamsburg, birria dominates Mission)
4. Opus-4.7-generated dish cards validated against existing menu (no hallucinated novel items)

---

## Files of record

- `HANDOFF.md` — this file (current state, what's blocking, how to resume)
- `hackathon-scope.md` — original spec; some assumptions out of date (Yelp-primary, state-level trends, gap framing). Kept for reference.
- `openclaw-changelog.md` (at `/home/zee/openclaw-changelog.md`) — full timestamped technical decision log
- `data/hermes.db` — single source of truth, SQLite
- `data/maps_raw/{weho,williamsburg,mission}_data.json` — raw JSONL→JSON conversions from Google Maps fetches
- `data/cava_byo/IMG_317*.jpeg`, `data/sweetgreen_byo/IMG_316*.png` — user-emailed BYO screenshots, hand-transcribed into JSON + loaders
- `scripts/` — full pipeline, numbered roughly in dependency order: `fetch_trends.py`, `fetch_maps_restaurants.py`, `load_to_db.py`, `load_maps_to_db.py`, `extract_sweetgreen_pantry.py`, `load_sweetgreen_byo.py`, `extract_flavors.py`, `dish_generator.py`
- `HANDOFF.md` — this file
- `/tmp/yelp_probe.py`, `/tmp/yelp_extract.py`, `/tmp/yelp_reviews.json` — Stage 1 working artifacts (move to `hermes/scripts/` before they expire from /tmp)
- `/tmp/brand_*.html` — Phase 1 probe responses (delete after extractors built)
