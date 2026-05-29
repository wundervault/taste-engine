# Taste Engine — Hackathon Project Scope

## What Are We Building

**Taste Engine** — a real-time menu generation system for fast casual restaurant chains.

Given a brand's existing ingredient list and a target zip code, the system:
1. Scrapes local taste signals (search trends, social buzz, competitor activity) via Bright Data
2. Matches those signals to ingredients already available at the brand
3. Outputs a complete, regionally-specific dish concept: name, description, ingredient list, and a signal strength score

**One-line pitch:** *Chipotle tests 4 LTOs a year. We let them test 40 — using ingredients already in the walk-in, with rollback in 2 weeks.*

**Target demo customers:** Chipotle, CAVA, Sweetgreen
**Target city:** Los Angeles (distinct neighborhoods, all three brands present, high search volume per zip)

**Demo output format:**

```
Dish name: El Mango Chipotle Bowl
Description: Charred chicken, mango-habanero glaze, fresh pico on cilantro-lime rice
Ingredients: chicken, rice, beans, cheese, fresh pico, mango-habanero sauce (existing items)
Local signal: "mango habanero" SERP presence up vs. 90-day baseline in 90012 (Koreatown)
Signal strength: HIGH (search trend + 3 competitor mentions in 30 days)
```

---

## Why This Matters

**The problem:** Fast casual chains innovate menus slowly — chef intuition, quarterly cycles, high cost to test. By the time a trend is confirmed, competitors have already capitalized.

**The gap:** No existing tool aggregates *passive external signals* (search, social, competitor activity) to recommend *operationally trivial* menu tests from *existing ingredients*. Brands have internal POS data; they lack external market intelligence.

**Existing tools this is not:**
- Tastewise — macro food trends, not local, not ingredient-constrained
- Popmenu / Thanx — rearview feedback aggregation
- NPD Group — expensive, lagging, industry-level

**This is different:** Real-time, zip-level, ingredient-constrained, self-funded tests with rollback in 2 weeks.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      BRIGHT DATA LAYER                      │
│                                                              │
│  SERP API ────────→ Local taste signals per zip              │
│  Web Scraper API → Brand ingredient lists                    │
│  Web Unlocker ──── Bot-protected sites                        │
│  Scraping Browser → JS-heavy or CAPTCHA-protected pages     │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    MATCHING ENGINE (LLM)                     │
│                                                              │
│  Prompt: "Mango-habanero is trending in 90012.              │
│           Chipotle's ingredients: [list].                   │
│           Generate a dish using only these ingredients      │
│           that matches the local taste signal.              │
│           Name it, describe it, estimate demand."           │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                        OUTPUT CARD                          │
│                                                              │
│  Dish name                                                   │
│  Description                                                  │
│  Ingredient list (constrained to brand inventory)            │
│  Local signal evidence                                        │
│  Demand estimate with confidence band                        │
└─────────────────────────────────────────────────────────────┘
```

---

## Build Phases

### Phase 1 — Data Harvesting (foundation)

**Goal:** Get structured ingredient lists for all three brands.

- Scrape Chipotle.com nutrition/ingredient page
- Scrape CAVA.com menu page
- Scrape Sweetgreen.com ingredient list
- Output: JSON files per brand with canonical ingredient pools

**Verification:** Human review of scraped items against source pages.

---

### Phase 2 — Signal Layer

**Goal:** Establish the Bright Data integration and extract real taste signals.

- Set up Bright Data SERP API
- Run test queries for known flavor signals in target LA zips
  - 90012 (Koreatown): gochujang, kimchi, citrus
  - 90023 (East LA): birria, al pastor, adobo
  - 90291 (Venice): harissa, tahini, Mediterranean herbs
- Verify structured output: keyword, volume delta, geo-tag

**Verification:** Cross-reference with Google Trends for known accuracy.

---

### Phase 3 — Dish Generation

**Goal:** Get the LLM to output clean dish cards from ingredient + signal inputs.

- Build structured prompt template
- Feed Chipotle ingredients + 90012 signals → generate dish
- Feed CAVA ingredients + 90291 signals → generate dish
- Feed Sweetgreen ingredients + 90023 signals → generate dish
- Human review of quality: names are compelling, ingredients are accurate, descriptions are believable

**Verification:** Manual judgment pass, no automated scoring.

---

### Phase 4 — Signal Strength Score

**Goal:** Give judges a defensible number tied to observable data — not a fake revenue projection.

- Score = function of (search trend delta + competitor mentions + social buzz count)
- Bucket into LOW / MID / HIGH
- Show the underlying evidence strings on the card (no hidden math)

**Note:** Originally scoped as a demand estimate (orders/week). Dropped because we have no POS or LTO baseline data — a precise-looking number we can't defend will hurt the demo more than help. Signal strength is the honest version.

---

### Phase 5 — Presentation / Demo Polish

**Goal:** Clean, impressive demo for hackathon judging.

- **2 dish cards with depth, not 3 rushed.** Pick the two brand/zip pairs where the signal data came back cleanest.
- Show the full pipeline: signal → ingredient constraint → generated dish → signal strength
- Optional (only if time): interactive element — pick a new zip, regenerate

---

## Out of Scope for the Hackathon (Future Work)

These are real product concerns but **do not belong in a 10-minute demo**. Listed here so judges can see we've thought past the MVP without us trying to build any of it:

- Ingredient seasonality / supplier constraints
- Prep complexity / kitchen execution feasibility
- Competitor LTO proximity flags
- One-page brand brief for menu committees
- Franchisee collaboration / voting on generated dishes

---

## Hackathon Deliverable

A live demo showing:
1. Bright Data SERP returning real taste signals for a target LA zip
2. A branded ingredient list loaded for one of the three chains
3. An LLM-generated dish card matching the signals to the ingredients
4. A signal strength score backed by observable evidence (search delta + competitor mentions)

**Time budget:** ~8 minutes of a 10-minute presentation. Leave 2 minutes for Q&A.

---

## Evolving Project Notes

This project is a living spec. As we build and validate assumptions against real data, we expect the concept to shift — some areas will prove more valuable than expected, others will need to be cut, and new opportunities will surface that we haven't anticipated.

**Things we should revisit as we go:**

- **Scope creep vs. demo clarity** — The spec includes Phase 4 (demand estimation) and a "what you might be missing" section with 5 items. Not all of these belong in the hackathon demo. As we build, we should ruthlessly cut anything that doesn't land in the final presentation. A tight demo of 2 dishes beats a mediocre demo of 5.

- **Zip code granularity** — LA is the current target, but if real signal data comes back noisy or thin for the chosen zips, we should be willing to pivot to a different city or use a broader regional proxy rather than force bad data into the demo.

- **Demand estimation realism** — Phase 4 is the most speculative. If the demand estimation ends up being too hand-wavy for judges to trust, it may be better to replace it with a "signal strength score" instead — still directional, but grounded in observable search volume rather than projected revenue.

- **Brand ingredient accuracy** — Scraped ingredient lists need human verification. If a brand pushes a site change or the scrape returns incomplete data, we may need to fall back to manually compiled lists for the demo and note this as a data quality caveat.

- **LLM output quality** — Current spec treats the LLM generation as a black box. As we test prompts, we may find the model consistently produces weak names, implausible ingredient combinations, or inconsistent output formats. That feedback should reshape the prompt template, not be worked around.

- **Competitive landscape** — The "what exists" section was researched quickly. If we find an existing tool that does exactly this, it's a signal to either pivot the angle or acknowledge it and explain why our approach (Bright Data-native, real-time, ingredient-constrained) is meaningfully different.

**Principle:** Ship the demo, not the product. What we show judges should be undeniable — real data, real output, clear chain of evidence. Everything else is future work.

**MVP reminder:** This is a hackathon build, not a v1 product. If something on this page slows down getting to a working end-to-end demo, cut it.

---

## POC Plan — West Hollywood (updated 2026-05-27)

**Target neighborhood:** West Hollywood / Sunset Strip corridor
**Why WeHo:** All three target brands confirmed active with recent review volume — Sweetgreen (8570 Sunset Blvd), CAVA (6200 Sunset Blvd, Hollywood side), Chipotle (8420 Beverly Blvd). Strong, distinct food culture (health-conscious, Mediterranean-leaning) makes the flavor-gap story compelling.

**Zip codes:** 90046 (West Hollywood), 90028 (Hollywood/CAVA side)

---

### POC Step 1 — Brand menu + ingredient data (save to disk, reuse every run)

Fetch and save each brand's full menu and ingredient list. These files are the pantry constraint for the dish generator — nothing runs without them.

| Brand | Approach | Output file |
|---|---|---|
| Sweetgreen | Plain curl on `order.sweetgreen.com/sunset-strip/menu` — static HTML, no JS needed | `data/sweetgreen_menu.json` |
| CAVA | Web Unlocker retry on `cava.com/menu` (Cloudflare-protected) | `data/cava_menu.json` |
| Chipotle | Try `chipotle.com/order`; fall back to their mobile JSON API if JS-gated | `data/chipotle_menu.json` |

Output schema per brand:
```json
[{ "dish_name": "", "category": "", "ingredients": [] }]
```

Files live in `/home/zee/hermes/data/` and are treated as ground truth until manually invalidated.

---

### POC Step 2 — WeHo taste signals (3 sources, run in parallel)

**Yelp reviews** (validated tech — Browser API)
- 8–10 local restaurants near 8570 Sunset Blvd covering WeHo cuisine mix: Mediterranean, health-forward, Korean-fusion, Mexican, farm-to-table
- ~10 pages of reviews per restaurant via Scraping Browser
- Output: `data/yelp_weho_reviews.json` — timestamped, rated review text

**Google Maps reviews** (same Browser API approach, untested)
- Same restaurant list, Maps review pages
- Different user base = different vocabulary, adds signal depth
- Output: `data/maps_weho_reviews.json`

**Google Trends via pytrends** (free, no scraping)
- LA DMA, last 12 months, food/ingredient/cuisine category queries
- Metro-level velocity signal to weight the zip-level review findings
- Output: `data/trends_la_dma.json`

---

### POC Step 3 — LLM synthesis → dish recommendations

Single Claude API call with all signal data + all three brand menus as context.

Logic:
1. Extract top flavor/ingredient mentions from Yelp + Maps reviews (frequency + recency weighted)
2. Cross-reference with Google Trends to confirm metro-level velocity
3. For each brand: identify the gap — what's trending locally that isn't on their menu and can be assembled from existing ingredients
4. Output 2 named dish concepts per brand

Output format per dish:
```
[Brand] — "[Dish Name]"
  Ingredients (from their pantry): ...
  Flavor signal: ...
  Supporting evidence: X mentions in Yelp reviews, Google Trends up Y% MoM in LA DMA
  Confidence: LOW / MID / HIGH
```

**Total deliverable:** 6 dish cards (2 per brand), fully sourced.

---

### POC execution order

1. Step 1 first — pantry data is the hard dependency
2. Step 2 in parallel once Step 1 is underway
3. Step 3 last — runs only after both inputs are on disk