# Dashboard Design Audit — 2026-05-28

Source: general-purpose subagent run with playwright against http://localhost:8501.
Screenshots: /tmp/dashboard_audit/ (1440×900 desktop) and /tmp/dashboard_audit/tall/ (768×900 narrow).
Audience target: hackathon judges, 5-min comprehension, scroll not navigate, scan not read.

Core insight: **visual hierarchy is inverted — decoration (pills, repeated pillar headers, full-color legends) lands before the load-bearing element** (dish name, validation chart, "8 vs 0" number).

---

## P0 — demo-blocking

### Cut hero card to 3 badges + a single subtitle line
- Current: TOP RECOMMENDATION + brand pill + Confidence 76 + ESTABLISHED + Lift LOW + National + PROVEN 2023/2024/2026 + then dish name + tagline + evidence line + proven line. Eye lands on the badge row, not the dish.
- Proposed: Dish name first (Georgia 1.6rem), brand small caps below it. One row beneath: Confidence 76/100 · Established · Proven 3×. Move Lift/National + evidence counts into a single 1-line caption ("Low lift · national rollout · 58 indie mentions").
- Why: Judge needs to read the recommendation, not decode badges.
- Effort: S

### Collapse 9 tabs to 5
- Current: Overview, Validation, Velocity, Signals, Pantry, Dishes, vs Naive LLM, Gaps, Compare. Narrow viewport truncates and Compare wraps to a new line on desktop too.
- Proposed: Overview (hero + pillars + maturity + top dishes) · Evidence (Validation + Velocity) · Methodology (Signals + Pantry + Gaps) · Dishes (full v6 cards) · vs Naive LLM. Compare becomes an expander inside Overview.
- Why: Judges scroll one tab. 9 tabs implies a fragmented story.
- Effort: M

### Fix Validation chart — inline LTO labels collide
- Current: "Chipotle Chicken Al Pastor 2023", "(2024)", "2026" overprint each other on the right edge and overlap the line itself.
- Proposed: Replace inline text with short rule labels ("2023 launch", "2024 return", "2026 relaunch") rotated 90° above the chart area, plus a real Altair legend below. Pad right margin 60px.
- Why: The single most important "proof" chart is currently illegible.
- Effort: S

---

## P1 — high-impact

### Velocity Top 8 line chart → small multiples or top-3 highlight
- Current: 8-10 same-weight colored lines; impossible to follow any single flavor.
- Proposed: Either (a) faceted small multiples (8 mini sparklines, each labeled), or (b) highlight top 3 flavors saturated and grey out the rest with a "show all" toggle.
- Why: A judge can't extract a finding from spaghetti.
- Effort: M

### Strip dish cards from 6+ badges to "primary 3 + meta row"
- Current: Each card shows SHIP_NOW + brand + Confidence + Established + Lift LOW + National + PROVEN + cuisine + flavor + comp — 9+ pills before the tagline.
- Proposed: Primary row = brand chip + confidence number + maturity. Meta row in small grey caption = "Low lift · national · proven 3× · comp verified". Drop outline pills or move to expander.
- Why: Directly addresses user complaint #1; cards become scannable.
- Effort: M

### Add badge legend / key, once, near the top of Dishes tab
- Current: No legend explains what "Confidence 76", "Established", "Low+National", or "PROVEN" mean.
- Proposed: One-line key strip above the dish grid. Collapse-able after first read.
- Why: Judges are not analysts; current pills are decorative until explained.
- Effort: S

### Remove the 3-pillar header from every tab; Overview only
- Current: Pillar 1/2/3 cards repeat at the top of every tab, costing ~200px above the fold.
- Proposed: Render on Overview only. On other tabs, a single thin "Pillar 2: Trend Maturity" breadcrumb keeps context.
- Why: Reclaims fold space; repeated pillars are wallpaper.
- Effort: S

---

## P2 — worth doing if time

### Gaps table → brand-colored cells / heatmap
- Current: Wide table with green checks and red X's; eye has to count.
- Proposed: Heatmap-style cells (filled brand color = deliver, hollow = gap). Highlight rows where 2+ brands have a gap (that's the headline).
- Why: Surfaces the "where is the opportunity" answer without a table read.
- Effort: M

### Color system: one purpose per color
- Current: Olive #3D5A40 = HIGH confidence AND "Strong fit" pantry AND "Very strong" signal strength. Brick #A33A1F = both Chipotle brand AND LOW confidence. Brand color and meaning color collide.
- Proposed: Lock brand colors for brand chips only. Use a separate diverging scale (slate/amber/rust) for confidence/maturity/strength.
- Why: Same color meaning two things is the most common dashboard-comprehension failure.
- Effort: M

### vs Naive LLM headline: lead with "8 vs 0" + 2-column diff
- Current: "8 vs 0 hallucination classes" rendered inline as paragraph text; outputs stacked rather than side-by-side.
- Proposed: Giant "8 vs 0" stat row at top (st.metric × 2). Below: two st.columns showing Naive (red strikethrough on the 9 fabricated SKUs) vs Taste Engine (hero card) literally side-by-side at desktop width.
- Why: Single most quotable moment in the pitch; should hit the eye in 1 second.
- Effort: M

### Sidebar: drop the prose blurb, keep BD scale block
- Current: "Neighborhood-aware culinary intelligence / Turning local taste signals..." takes 4 lines before the city radio.
- Proposed: Logo + city picker first. Push tagline to caption under page title. Keep BD scale stats (64,372 reviews etc.) — those are credibility.
- Why: City switching is the demo lever; surface above fold.
- Effort: S

---

## Suggested execution order

1. **P0 trio first** (hero card, tab collapse, Validation chart) — unblocks 5-min pitch arc
2. **P1 sweep** — dish card simplification, badge legend, pillar header reduction, Velocity chart fix
3. **P2 if time** — heatmap Gaps, color system, "8 vs 0" hero layout, sidebar trim
