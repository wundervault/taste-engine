# Taste Engine — 5-Minute Submission Video Script

**Target runtime:** 4:45 (15-second buffer under 5:00 cap)
**Word count:** ~770 words at 160 wpm
**Tone:** Calm authority, not enthusiastic pitch. Operator-to-operator.

---

## 0:00 — 0:30 · Cold open + what it is *(75 words)*

> **[VISUAL: title card "Taste Engine — Neighborhood-aware culinary intelligence" over a still of the Mission District sidebar]**

Fast-casual brands run their menu planning on a quarterly cadence. Chipotle shortened its limited-time offer cycle. CAVA does four seasonal menus a year. The bottleneck isn't creativity — it's *intelligence*. Knowing which flavors a specific neighborhood is mainstreaming, which the chef-driven scene is still discovering, and what's shifted since last quarter. That's what Taste Engine does — every visit shows what changed and what's coming.

---

## 0:30 — 1:00 · Bright Data dependency *(70 words)*

> **[VISUAL: dashboard sidebar zoomed — Powered by Bright Data, 64,372 reviews, 383 restaurants, 3 metro markets, 24 months]**

It's built on four Bright Data products. The Google Maps Reviews Dataset pulled 64,000 dated indie restaurant reviews across three neighborhoods. Fast Maps Search discovered the restaurants. SERP API verified chain LTO history against press releases. Scraping Browser handled the protected targets. This dataset doesn't exist without Bright Data infrastructure. CSV exports and standard scraping can't reach this scale or freshness.

---

## 1:00 — 2:00 · Act 1 — Trend Maturity Spread *(160 words)*

> **[VISUAL: Maturity landscape scatter for Mission, then transition to comparison view across 3 cities]**

Here's our second pillar in action. Every indie restaurant gets tagged into one of two pools: lunch competitors and chef-driven dinner spots. The split between them is structural. When a flavor is heavy in the competitive pool but absent from the leading pool, it's *Established* — mainstream taqueria culture, safe to ship. When it's the reverse — heavy in leading, light in competitive — that's *Rising* — chef-driven, premature for a chain.

Watch one flavor across three neighborhoods.

> **[VISUAL: three-card comparison]**

Mission al pastor: 58 competitive mentions across 14 indie restaurants, one leading. That's Established. Mainstream.

Williamsburg al pastor: 3 competitive, 8 leading. Rising. Chef-driven. Too early.

West Hollywood: Established but thin.

Same flavor, three cities, three maturity stages, three different strategic calls. No other system reads this distinction. National Google Trends can't see it.

---

## 2:00 — 3:00 · Act 2 — Validation, then the call *(160 words)*

> **[VISUAL: Mission Overview — Maturity Landscape scatter with al pastor in Established quadrant]**

Here's how we know the system works. Between Q4 2025 and Q1 2026, al pastor crossed from Weak to Established in Mission indie reviews. Then on January 27th, Chipotle announced the al pastor relaunch — for February 10. The maturity classifier flipped weeks before the brand made the call.

That's the validation.

> **[VISUAL: hero gap-fill card on Overview — Mission Mole + Chipotle]**

This is the next call. Mole is the strongest mover in Mission this quarter — plus seven mentions in Q1, joining 132 total reviews across 12 indie restaurants. None of the chains can deliver mole from current pantry today. Chipotle should add it as a single shelf-stable stock unit and test Mission first.

Confidence 68. Peak maturity — mainstream mole culture, not chef-driven hype. Low operational lift, national rollout. Californios verified as the comp. The system caught al pastor's shift. It's catching mole's now.

---

## 3:00 — 3:45 · Act 3 — Quarterly cadence + forecast *(110 words)*

> **[VISUAL: Trends tab — Quarterly Brief header, 8-quarter trajectory chart, forecast small multiples]**

Brands run quarterly cycles, so does this. Every visit shows what shifted since last quarter: mole +7 mentions, al pastor +6, pesto +5. Maturity transitions get flagged with the strategic call attached.

> **[VISUAL: forecast bars with confidence band]**

And the system projects forward. Mission birria is forecast to rise to roughly twenty-seven mentions in Q2, plus or minus two. The roadmap surfaces multi-snapshot Trends velocity and cross-city diffusion predictions as the platform accrues longitudinal data — that's the v2 we're explicit about.

This is the difference between a one-shot analysis and a subscription.

---

## 3:45 — 4:15 · Naive LLM vs Taste Engine *(75 words)*

> **[VISUAL: side-by-side, naive output left, Taste Engine right, hallucinated SKUs highlighted red]**

We asked Claude the same question with no engine context. It hallucinated nine SKUs across two dishes. Confidently asserted "brisket is in Chipotle's current pantry" — false. Reinvented al pastor as a vegan mushroom dish, completely missing the actual brand decision happening right now.

The LLM is a reasoning layer. The innovation is the pipeline that constrains it, anchors it in evidence, and validates it against pantry, history, and maturity stage.

---

## 4:15 — 4:45 · Why this is hard + close *(70 words)*

> **[VISUAL: title card "8 hallucination classes caught deterministically. Naive LLM: 0."]**

Behind all of this: we catch eight distinct LLM hallucination classes in code before any output ships — pantry, duplication, LTO blind spot, cuisine incoherence, dish-name overpromise, ambiguous terms, fabricated restaurants, invented numbers. Naive prompting catches zero.

> **[VISUAL: closing title card with logo + tagline]**

Taste Engine. Neighborhood-aware culinary intelligence. Built on Bright Data.

---

# Production notes

## Pacing checks
- **0:00–1:00 = 145 words** (steady, ~145 wpm — slower setup)
- **1:00–3:00 = 320 words** (Acts 1 + 2, ~160 wpm)
- **3:00–3:45 = 110 words** (Act 3, slower for the temporal pitch)
- **3:45–4:45 = 145 words** (Naive vs Engine + close, slower for landing punch lines)

## Required dashboard captures
1. Title sidebar (0:00)
2. Sidebar zoomed — Powered by Bright Data, scale numbers (0:30)
3. Mission Maturity Landscape scatter (1:00)
4. Cross-city al pastor mention breakdown (1:20) — *flip the city dropdown serially while recording*
5. Mission Overview — al pastor in Established quadrant of maturity scatter (2:00)
6. Mission Overview — hero GAP-FILL MOLE callout card with badges + evidence (2:30)
7. Trends tab — Quarterly Brief header + movers panel (3:00)
8. Trends tab — forecast small multiples with confidence band (3:25)
9. vs Naive LLM tab — "8 vs 0" metric row + side-by-side (3:45)
10. Closing title card (4:30)

## Required pre-recording prep
- Confirm Mission is the selected city in the sidebar
- Confirm Recommendations tab shows the Mole hero gap-fill card at the top of Overview
- Build naive-vs-taste-engine static asset (currently markdown — needs visual treatment for video)
- Time-check by reading script aloud once before recording

## Honest delivery notes
- Don't oversell. The naive comparison is the strongest moment — let it breathe.
- Pause after "Three different maturity stages, three different strategic calls" (1:55) for ~1 second.
- Pause after "weeks before the brand made the call" (2:25) — let the validation moment land.
- "Taste Engine would have flagged this in Q4 2025" — drop this line; we already make the point earlier and crisper.
- "8 hallucination classes caught deterministically. Naive LLM: 0" (4:35) — say it flat, not dramatic.

## Words to NOT say
- "AI-powered" (we're explicitly de-emphasizing this)
- "Game-changing" / "revolutionary" / hype language
- "ChatGPT" (we ran Claude in the comparison — accurate)
- Any number we can't show on screen
