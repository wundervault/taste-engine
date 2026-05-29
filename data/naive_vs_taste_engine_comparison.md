# Naive LLM vs Taste Engine — same question, different rigor

**Prompt asked of both systems:**
> "Recommend dishes for Chipotle to launch in the Mission District of San Francisco based on current food trends in that neighborhood."

The naive LLM has access only to its training data and the prompt. Taste Engine has access to 64,372 dated indie reviews across 383 restaurants, brand pantry catalogs with availability flags, newsroom-verified LTO history, DMA-level Google Trends, and a deterministic deliverability + lift + maturity scoring layer.

---

## Naive LLM Output

### Dish 1: "Suadero-Style" Braised Beef Taco Plate
*Slow-braised beef brisket, double-stacked corn tortillas griddled in braising fat, salsa verde cruda, consommé for dipping.*

**Failure modes (annotated against ground truth):**

| Claim | Reality |
|---|---|
| "Slow-braised beef brisket" as ship-now | ⛔ **Hallucinated SKU.** Brisket is NOT in current Chipotle pantry. Chipotle shipped Smoked Brisket as 2021 LTO and Quesabrisket as 2025 Halloween LTO — both currently dormant. Naive LLM has no awareness of this history. |
| "Consommé for dipping" | ⛔ **Operationally infeasible.** Chipotle's assembly-line steam tables do not run a consommé station. This recommendation would require new equipment + multi-day prep cycle (HIGH lift) but is framed as "low operational lift." |
| "Double-stacked corn tortillas griddled in braising fat" | ⛔ **Process hallucination.** Chipotle does not griddle tortillas — they are heated on a clamshell press. No braising fat is plated. |
| "Beef brisket is already in Chipotle's supply chain (close to their barbacoa)" | ⛔ **Factually wrong.** Brisket and barbacoa are distinct cuts with distinct prep. Naive LLM confidently asserts a supply-chain identity that does not exist. |
| "Birria and suadero tacos are the dominant trend on Mission Street (2022–2025)" | ⚠️ **Directionally right, no evidence.** Taste Engine has 135 indie birria mentions across 9 Mission restaurants from dated reviews. Naive LLM asserts the same conclusion without citing a single review. |
| No confidence score, no maturity stage, no rollout portability | ⛔ Recommendation reads as equally confident as any other — no analytical scaffolding. |

### Dish 2: Hongos al Pastor Bowl
*King trumpet + oyster mushrooms in achiote/guajillo/pineapple marinade, cashew crema, chayote-poblano slaw.*

**Failure modes:**

| Claim | Reality |
|---|---|
| "King trumpet + oyster mushrooms" | ⛔ **Hallucinated SKUs.** Chipotle's only plant protein is Sofritas. No specialty mushroom SKU in pantry. |
| "Cashew-based crema" | ⛔ **Hallucinated SKU.** Chipotle has no cashew crema; this is closer to a CAVA or Sweetgreen move than a Chipotle move. |
| "Chayote-poblano slaw base" | ⛔ **Hallucinated SKU.** Chayote is not in Chipotle pantry. |
| "Salsa macha" | ⛔ **Hallucinated SKU.** Chipotle does not stock salsa macha. |
| "Crispy garlic chips" | ⛔ **Hallucinated SKU.** Not in pantry. |
| Pivots to plant-based version of al pastor | ⚠️ **Misses the actual brand decision.** Chipotle announced Feb 10, 2026 relaunch of Chicken Al Pastor (chicken, not mushroom) as their most-celebrated LTO. Naive LLM has no awareness; reinvents the wheel as vegan. |
| "Mushroom-based meat alternatives are trending hard" | ⛔ **Vague trend claim.** No DMA-level evidence. Our pytrends data for "al pastor" shows the actual signal arc. |

### Bottom-line failure pattern

- **9 hallucinated SKUs across 2 dishes.** Real pantries have ~30-50 active SKUs; recommending 9 net-new items is not "low lift."
- **No anchor in brand history.** Naive LLM doesn't know Chipotle is launching Chicken Al Pastor in Mission *right now* (Feb 10, 2026 — verified via newsroom). It reinvents and misses the strategic opportunity.
- **Stereotype-level neighborhood detail.** "The Mission skews younger/progressive" is a 2018-era cliché — does not differentiate from any other US progressive neighborhood.
- **False confidence.** Every claim presented as equally certain. No probabilistic framing.
- **Confuses similarity for equivalence.** Asserts brisket ≈ barbacoa supply chain. This is the class of error that ships ops-impossible dishes.

---

## Taste Engine Output (same prompt, processed through pipeline)

### Top recommendation (auto-surfaced)

**Al Pastor Pineapple Crunch Taco Plate** — Chipotle, ship now.

| Layer | Value |
|---|---|
| **Composite confidence** | **76 / 100** (top of 450 brand × flavor × city combinations across our coverage) |
| **Maturity stage** | **Established** — competitive pool 58 mentions / leading pool 1 mention = mainstream taqueria culture, not chef-driven hype |
| **Operational lift** | **Low + National rollout** — Chipotle already delivers al pastor (chicken, achiote, pineapple, adobo in current pantry); only missing SKU is achiote (already shipped 2023, 2024) |
| **Proven execution** | **PROVEN — shipped 2023, 2024, 2026.** Chipotle's Chicken Al Pastor LTO is verified via two newsroom URLs (March 14, 2023 global launch announcement; January 27, 2026 relaunch announcement). |
| **Evidence** | 58 competitive indie mentions across 14 restaurants · 34 mentions in last 12 months · DMA Trends 17/100 (national baseline) |
| **Strategic call** | Mission is the densest indie al pastor culture in our 3-city coverage. Williamsburg al pastor is Rising (chef-driven, not yet mainstream); WeHo is Established but thin. **Recommendation: prioritize Mission stores for the Feb 2026 relaunch rollout. Williamsburg as a watch market, not a launch market.** |

### Second recommendation (Mission, Chipotle)

**Gap Fill: Mole** — confidence 68, Peak maturity, High lift.

| Layer | Value |
|---|---|
| **Missing SKU** | mole paste (shelf-stable, sourceable through standard QSR distributors → National portability) |
| **Operational lift** | Mole ships as a heat-and-hold sauce in the existing steam-table well — no new station required. Real lift is sourcing a credible mole, not adopting it. |
| **Comp context** | Nopalito and La Oaxaqueña on Mission St are the local benchmarks. La Oaxaqueña's mole negro is the reference quality bar. |
| **Maturity caveat** | Mole is at Peak in Mission (both pools heavy) — fad-tail risk is real. Recommendation flagged as a slower-burn opportunity, not a rush ship. |

### Why this output is structurally different

- **Zero hallucinated SKUs.** Every ingredient passes the deliverability check before the LLM names it.
- **Evidence-cited.** Each recommendation is anchored to specific competitive + leading mention counts that judges (or operators) can re-verify in the DB.
- **Maturity-aware.** Mole's "Peak" label warns of fad-tail risk; al pastor's "Established" label confirms safe shipping window. The naive LLM does not distinguish.
- **Brand-history-aware.** Surfaces the Feb 2026 Chicken Al Pastor relaunch as a launchpad — the actual strategic opportunity. Naive LLM reinvents the wheel.
- **Honest uncertainty.** Composite confidence score 0-100 with 6-component breakdown — nothing above 80 in our coverage, reflecting appropriate analytical humility.
- **Actionable rollout intelligence.** Mission as launch market, Williamsburg as watch market — based on cross-city maturity comparison the naive LLM cannot perform.

---

## The pitch line

**The LLM is a reasoning layer. The innovation is the pipeline that constrains, anchors, and evidences it.** Same model, same question — one is speculation, the other is intelligence. Bright Data infrastructure (Google Maps Reviews Dataset, Fast Maps Search, SERP API, Scraping Browser) is what makes the difference reproducible at scale.
