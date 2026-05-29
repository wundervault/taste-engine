# Taste Engine

**Neighborhood-aware culinary intelligence.** Turning local taste signals into operational menu strategy for fast-casual brands.

Built for the **Bright Data Hackathon · May 2026**.

🍽️ Live: **[tasteengine.wundervault.com](https://tasteengine.wundervault.com)**

---

## What it is

Taste Engine is a **quarterly intelligence brief for brand strategy teams.** It reads the local food culture of a single neighborhood — restaurant menus, review mentions, indie cuisine mix — and outputs menu recommendations grounded in each brand's actual pantry (active ingredient inventory), operational lift capacity, and limited-time offer (LTO) history. Every visit shows what changed since last quarter and a directional forecast for the next.

Demo coverage: **Chipotle, CAVA, Sweetgreen** across **West Hollywood, Williamsburg (Brooklyn), Mission District (San Francisco)**.

Output is two recommendation types per brand per neighborhood:
- **Ship Now** — dishes the brand can execute immediately from current SKUs
- **Gap Fill** — high-signal flavors that need specific SKU additions, with named operational lift

Every recommendation passes a deterministic deliverability check, a cuisine-coherence check, and a brand-identity check before being labeled novel. Every card carries a composite confidence score (0–100), maturity stage, lift tier, rollout portability tag, and — where applicable — a "PROVEN" badge linking to newsroom verification.

## The headline metric

**Eight distinct LLM hallucination classes caught deterministically, in code, before the model ships output. Naive LLM prompting catches zero.**

| # | Class | Caught by |
|---|---|---|
| 1 | Pantry hallucination | `scripts/compute_pantry_fit.py` |
| 2 | Menu duplication | DO-NOT-PROPOSE prompt block |
| 3 | LTO blind spot | `data/brand_lto_history.json` |
| 4 | Cuisine incoherence (miso × pesto) | `scripts/audit_dish_coherence.py` |
| 5 | Dish-name overpromise (al pastor pineapple) | `scripts/audit_dish_name_truthfulness.py` |
| 6 | Ambiguous-term false positives | `scripts/extract_flavors.py` |
| 7 | Fabricated comp restaurants | `scripts/audit_comp_restaurants.py` |
| 8 | Numerical claim invention | `scripts/audit_numerical_claims.py` |

The pitch line: *The LLM is a reasoning layer. The innovation is the pipeline that constrains, anchors, and evidences it.*

## Three pillars

1. **Signal Fusion** — National Google Trends × local indie restaurant reviews, weighted honestly. Disagreements between signals are flagged, not hidden.
2. **Trend Maturity Spread** — indie restaurants tagged into competitive (lunch-open, $-$$) vs leading (chef-driven, dinner-only / $$$+) pools. The split between pools maps to lifecycle stage: Rising → Established → Peak.
3. **Innovation Feasibility Intelligence** — deterministic deliverability check + lift classifier + rollout portability + LTO history. Brand cuisine identity filters off-brand flavors at the data layer so off-cuisine recommendations can't surface.

## Bright Data dependency

| Product | Role | Volume |
|---|---|---|
| Google Maps Reviews Dataset | Chronological dated indie reviews | 64,372 reviews across 383 restaurants |
| Fast Maps Search | Restaurant discovery + top_reviews | 383 restaurants + 4,170 embedded reviews |
| SERP API | LTO press-release verification | 20 newsroom-verified LTO entries |
| Scraping Browser | Yelp baseline + protected targets | 239 reviews |

Total spend during build: ~$170. The chronological review dataset alone (~$80) is the largest single source of analytic value.

This project is not reproducible from CSV exports or single-script scraping. CSV can't reach this scale or freshness. Bright Data is the dependency.

## Architecture

```
Bright Data ─→ SQLite (hermes.db) ─→ deliverability matrix
                                       cuisine coherence
                                       brand cuisine identity
                                       LTO history
                                       confidence scoring (0–100)
                                       lift + portability
                                                │
                                                ▼
                                   Claude Opus 4.7 (tool-use agent)
                                   — no factual claim without a tool call —
                                                │
                                                ▼
                                   v6 enriched recommendation cards
                                                │
                                                ▼
                                   Streamlit dashboard
                                   6 tabs · 18 cards · quarterly brief
```

The full architecture diagram, validation story (al pastor crossing Weak → Established weeks before Chipotle's Feb 2026 relaunch), and engineering narrative are in the hackathon submission packet — not committed here.

## Run locally

```bash
git clone https://github.com/wundervault/taste-engine.git
cd taste-engine

# Python 3.12+
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# Secrets — copy .env.example and add your own keys
cp .env.example .env
# edit .env to add ANTHROPIC_API_KEY + Bright Data tokens

# Launch the dashboard (uses the bundled data/hermes.db — no Bright Data spend needed)
.venv/bin/streamlit run app.py --server.headless true
# → http://localhost:8501
```

## Regenerate from scratch

If `data/hermes.db` is intact, no Bright Data spend is needed to run the dashboard. To rebuild the data pipeline:

```bash
# Load brand pantries (free — local JSON sources)
python scripts/load_to_db.py
python scripts/extract_sweetgreen_pantry.py
python scripts/load_sweetgreen_byo.py

# Pull Bright Data (costs credits)
python scripts/fetch_maps_restaurants.py
python scripts/fetch_maps_reviews.py --all
python scripts/load_maps_to_db.py
python scripts/load_maps_reviews_to_db.py
python scripts/clean_reviews.py
python scripts/tag_restaurants.py

# Build the constraint stack
python scripts/extract_flavors.py
python scripts/compute_pantry_fit.py
python scripts/tag_pantry_families.py
python scripts/tag_sku_presentation.py
python scripts/compute_confidence.py
python scripts/compute_lift.py
python scripts/compute_quarterly_brief.py

# Regenerate dish recommendations via the tool-use agent
python scripts/dish_generator_agent.py mission
python scripts/dish_generator_agent.py williamsburg
python scripts/dish_generator_agent.py weho

# Enrich + audit
python scripts/enrich_recommendations.py
python scripts/audit_dish_coherence.py
python scripts/audit_dish_name_truthfulness.py
python scripts/audit_comp_restaurants.py
python scripts/audit_numerical_claims.py
python scripts/test_no_off_brand_displays.py  # structural test
```

## Stack

- **Data:** SQLite, Python 3.12, Bright Data JSONL pulls
- **LLM:** Claude Opus 4.7 with tool-use (function calling)
- **Dashboard:** Streamlit + Altair
- **Trend data:** pytrends (with a urllib3≥2.0 source patch)
- **Vault:** Wundervault for ANTHROPIC_API_KEY + Bright Data tokens

## License

MIT — see [LICENSE](LICENSE).
