# Taste Engine — Session Handoff, 2026-05-27 (evening)

**For whoever picks this up next** — could be future-you, future-Claude, or anyone resuming work on Phase 5. Read this first, then `HANDOFF.md` for the canonical project state, then `openclaw-changelog.md` for the chronological decision log.

---

## What state the project is in right now

**End-to-end pipeline is working.** Bright Data → Google Maps reviews → DMA Google Trends → SQLite signal score → Opus dish generation → JSON outputs. Phases 1–4 ✅ complete. Only Phase 5 (demo polish / presentation) remains.

Data snapshot:
- `data/hermes.db` — single SQLite source of truth
- 391 restaurants across 3 cities (WeHo 103 / Williamsburg 150 / Mission 138)
- 4,409 reviews (4,170 from Google Maps + 239 from Yelp baseline)
- 240 brand pantry items (Chipotle 27 / CAVA 53 / Sweetgreen 160)
- 105 trend rows (35 terms × 3 DMAs)
- 591 flavor mentions across 418 reviews / 38 distinct flavors
- 18 generated dish recommendations (6 per city) at `data/{weho,williamsburg,mission}_dish_recommendations_v4.json`

---

## What happened this session (chronological)

1. **Validated Sweetgreen pantry depth.** Parsed 27 dish ingredients into 66 dedup'd SKU rows. Surfaced two v3 hallucinations: "Miso Salmon + Crispy Rice Bowl" and "KBBQ Chicken + Kimchi Crunch" are *already* on Sweetgreen's menu — the v3 dish-gen was renaming existing items as gaps.
2. **Loaded Sweetgreen BYO catalog** from 8 user-emailed screenshots (67 items, 5 categories, including the BYO-only items like miso sesame ginger dressing, nori sesame seasoning, hard boiled egg, bread). Schema migration: added `available` column to `brand_menu_items`.
3. **CAVA pantry rebuilt** from 9 user-emailed BYO screenshots. Found pantry was wrong on 3 axes: 6 missing items (harissa honey chicken, glazed salmon, sides category), 5 naming mismatches, sides category not in loader vocab. Original "CAVA can't deliver harissa" gap claim was completely false — CAVA serves harissa as dip + protein + dressing + side.
4. **Renamed project** `/home/zee/hermes` → `/home/zee/taste-engine`. Python module stays `hermes` (internal). Updated `~/.local/bin/brightdata-mcp` wrapper to new path.
5. **Pitch revised in HANDOFF.md** — dropped "finds the gaps your competitors haven't filled" (hallucination-prone framing) for "surfaces flavors where national trend strength and local review signal both agree." Added explicit "validated against the brand's current menu" bullet.
6. **DMA-level Google Trends refactor.** Replaced state-level (`US-CA`, `US-NY`) with DMA-level (`US-CA-803` LA, `US-CA-807` SF-Oakland, `US-NY-501` NYC). pytrends 4.9.2 required a 1-char source patch (`method_whitelist` → `allowed_methods`) for urllib3 ≥ 2.0 compatibility. Documented in `scripts/fetch_trends.py`.
7. **Signal-ranking SQL bugfix** (load-bearing). v3 query used `LEFT JOIN restaurants ... AND r.city = ?` which does NOT filter `flavor_mentions` — every "city" was getting the GLOBAL mention count. Patched to a city-scoped subquery in `dish_generator.signal_ranking()`. Cauliflower went from "16 everywhere" to actual values: 6 WeHo / 10 WB / 0 Mission.
8. **Added SF Mission District as 3rd target city.**
9. **Yelp search-results sourcing failed** — DataDome blocked every page via BD Scraping Browser (`/search?find_loc=...` pages have stricter anti-bot than `/biz/` pages). Tried HTML caching + parser fixes; still blocked. Switched approaches.
10. **Pivoted to Bright Data Google Maps Fast Maps Search** (dataset `gd_m8ebnr0q2qlklc02fz`) per BD chatbot recommendation. One API trigger per neighborhood center coordinate + zoom level. Returns JSONL stream with name, place_id, rating, review_count, address, categories, **top_reviews (8 each) + reviews_snippets (3 each)**.
11. **AdGuard DNS rule still active** even with AdGuard "protection" toggled off — confirmed via `dig api.brightdata.com` returns 0.0.0.0 vs `dig @1.1.1.1` returns real IPs. Workaround baked into `scripts/fetch_maps_restaurants.py`: resolve via Cloudflare DNS at startup, `curl --resolve` for all BD REST calls.
12. **Fetched + loaded ~3,300 Google Maps reviews** across 3 cities. Flavor extraction now hits 38 distinct flavors (up from 23) including miso, shawarma, birria, matcha, al pastor, mole — all of which had 0 mentions in the Yelp-only set.
13. **Wired ANTHROPIC_API_KEY via wundervault** `vault_entry_inject_env` (after user enabled it in dashboard).
14. **Generated 18 dish recommendations** (Opus 4.7 + prompt caching). First city: cache_write=719 + cache_read=2946. Subsequent cities: pure cache hits (cache_write=0). ~70% input cost savings on re-runs.

---

## What's open (Phase 5 — demo polish)

Open items from the punch list, ranked:

**Demo-critical:**
- **No human-readable presentation layer** for the 18 dishes. JSON outputs exist but no slide deck, no HTML viewer, no markdown summary. A judge can't read JSON.
- **No comparison vs v3 outputs** to demonstrate the improvement. Showing "we caught the miso-salmon hallucination" requires putting v3 next to v4 side-by-side.
- **Demo script / narrative** doesn't exist. Need a 3-minute story: setup → cross-city contrast → one hero recommendation → defensible methodology framing.

**Nice-to-have:**
- **Chipotle Lifestyle Bowls + CAVA Signature Bowls** still missing from DB. Both chains have featured/signature combinations on their marketing sites — without them, dish-gen for those brands can't dedupe. Less critical now that the recommendations are clearly novel (Chipotle "Birria Dipping Tacos" isn't a Lifestyle Bowl, etc.) but cleaner if added.
- **Review recency not weighted.** Reviews span multiple years; recent reviews should count more for "current local taste." Quick patch: `review_date > '2024-01-01'` in signal_ranking.
- **Flavor extraction recall holes.** 27 vocab terms got 0 hits in the Yelp-only set; with Google Maps reviews many of those now have signal, but paraphrase misses ("red pepper paste" vs "harissa") still exist. LLM extraction pass could lift recall ~20-30%.
- **No git / no README.** Project lives in `/home/zee/taste-engine` with no version control. For hackathon submission you'd want at least an initial commit and a one-page README.

**Lower priority:**
- Sweetgreen has dup pantry representations in DB (parsed-from-dishes `sweetgreen_*` vs BYO `sweetgreen_byo_*`). Prompt only uses BYO now, but anyone querying the DB will see both.
- CAVA/Chipotle have no `available` data (only Sweetgreen BYO captured availability).

---

## How to resume (cold start)

```bash
cd /home/zee/taste-engine
set -a && source .env && set +a   # loads ANTHROPIC_API_KEY + BD tokens

# Regenerate everything from current data (no scraping):
.venv/bin/python scripts/load_to_db.py                # brand pantries
.venv/bin/python scripts/extract_sweetgreen_pantry.py # parsed pantry
.venv/bin/python scripts/load_sweetgreen_byo.py       # BYO catalog
.venv/bin/python scripts/load_maps_to_db.py           # gmaps restaurants + reviews
.venv/bin/python scripts/extract_flavors.py           # 591 mentions
.venv/bin/python scripts/dish_generator.py weho
.venv/bin/python scripts/dish_generator.py williamsburg
.venv/bin/python scripts/dish_generator.py mission

# To re-scrape (uses BD credits):
.venv/bin/python scripts/fetch_trends.py             # ~3 min, free (pytrends)
.venv/bin/python scripts/fetch_maps_restaurants.py   # ~5-10 min, ~$3-9 BD
```

If `curl --resolve` errors with "no IPs returned": AdGuard rule for *.brightdata.com is still active. Either disable the rule in AdGuard settings or check `dig @1.1.1.1 api.brightdata.com` works.

---

## Files added or significantly changed this session

**New scripts:**
- `scripts/fetch_trends.py` — DMA-level pytrends fetcher
- `scripts/fetch_maps_restaurants.py` — BD Google Maps trigger + poll, with curl --resolve workaround
- `scripts/load_maps_to_db.py` — JSONL → restaurants + reviews loader
- `scripts/extract_sweetgreen_pantry.py` — parse SG dishes into normalized SKU rows
- `scripts/load_sweetgreen_byo.py` — load 67-item BYO catalog
- `scripts/source_restaurants.py` — Yelp search-results scraper (DEPRECATED, kept for reference; replaced by Maps)

**Modified scripts:**
- `scripts/dish_generator.py` — full rewrite: DB-backed inputs, EXISTING_DISHES_DO_NOT_PROPOSE section, signal_ranking bugfix, Opus 4.7 + prompt caching, DMA geo codes
- `scripts/load_to_db.py` — added `sides`, `vessels`, `dips_spreads` to BUCKET_CATEGORIES; brand-scoped DELETE
- `scripts/extract_flavors.py` — vocabulary derived from trends.term + EXTRA_VOCAB

**Schema migrations:**
- `brand_menu_items` got `available` column (Sweetgreen BYO availability)
- `restaurants` got `gmaps_rating` and `gmaps_review_count` columns

**Data files added:**
- `data/maps_raw/{weho,williamsburg,mission}_data.json` — full Google Maps records per city
- `data/maps_raw/{weho,williamsburg,mission}_trigger.json` — BD snapshot_ids
- `data/cava_byo/IMG_317[3-9,82].jpeg` — 9 hand-curated BYO screenshots
- `data/sweetgreen_byo/IMG_316[5-9],317[0-2].png` — 8 hand-curated BYO screenshots
- `data/{weho,williamsburg,mission}_dish_recommendations_v4.json` — 18 final dish outputs
- `data/restaurant_candidates_raw.json` — partial Yelp sourcing output (deprecated path)
- `data/yelp_search_html_cache/` — Yelp search HTML cache (deprecated path; can delete)

**Modified data files:**
- `data/cava_menu.json` — rebuilt from BYO screenshots (corrected naming + added missing items + sides category)
- `data/hermes.db` — schema + content fully rebuilt

**Docs updated:**
- `HANDOFF.md` — rewrote ~half the file; removed stale Phase 1/2 OPEN sections; restructured around current state
- `openclaw-changelog.md` (at `/home/zee/openclaw-changelog.md`) — 3 new entries chronicling project rename, DMA refactor, Maps pivot, Phase 4 completion

---

## What to NOT do

- Don't reach for Yelp search-results sourcing again — confirmed dead end via DataDome + BD chatbot guidance.
- Don't switch back to state-level trends — SF and WeHo would lose differentiation.
- Don't try to merge the Yelp + Google Maps restaurant rows by name. They live as separate rows on purpose (different `id`, different `source` for their reviews). Merging would risk corruption with no real upside since `flavor_mentions` joins via review_id.
- Don't disable the `available=1` filter in `pantry_for()` — that's the path for live stock signals if/when CAVA/Chipotle BYO data is added.
- Don't rename the Python module from `hermes` to `taste_engine`. Folder is renamed but the internal package name is not user-facing.

---

## Credentials state

`/home/zee/taste-engine/.env`:
- `API_TOKEN` — Bright Data account-level (SERP, Web Unlocker, Dataset triggers)
- `BROWSER_WS` — Scraping Browser CDP endpoint
- `ANTHROPIC_API_KEY` — injected via wundervault `vault_entry_inject_env` from entry `claude api` (id `-nYMzblaLPyA96E6c24vRzINOBg0ol-v`)

Vault entries that matter:
- `claude api` — Claude API key
- `serp_api1` — source of BD `API_TOKEN`
