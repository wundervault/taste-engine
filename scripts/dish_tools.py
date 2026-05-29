#!/usr/bin/env python3
"""Tools for the dish-generation agent.

Each function wraps existing project data (DB, pantry_fit.json, lto history,
cuisine compatibility, confidence scores, etc.) and exposes it via a
JSON-serializable return. The Anthropic tool-use API calls these functions
on the LLM's behalf — the LLM cannot assert facts that aren't returned by
one of these tools.

Architectural principle: every audit we built becomes a tool the LLM consumes
prospectively. The model reasons; the tools provide ground truth.

Tool registry at the bottom maps tool names → callables.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from hermes.db import init_db  # noqa: E402

DATA = ROOT / "data"

# ---------------------------------------------------------------------------
# Cached data loaders
# ---------------------------------------------------------------------------

_PANTRY_FIT = None
_LTO_HISTORY = None
_FLAVOR_FAMILIES = None
_CUISINE_COMPAT = None
_SKU_FAMILIES = None
_SKU_PRESENTATION = None
_CONFIDENCE = None
_LIFT = None
_BRAND_CUISINE = None


def _load_pantry_fit():
    global _PANTRY_FIT
    if _PANTRY_FIT is None:
        _PANTRY_FIT = json.loads((DATA / "pantry_fit.json").read_text())
    return _PANTRY_FIT


def _load_lto():
    global _LTO_HISTORY
    if _LTO_HISTORY is None:
        raw = json.loads((DATA / "brand_lto_history.json").read_text())
        _LTO_HISTORY = {k: v for k, v in raw.items() if not k.startswith("_")}
    return _LTO_HISTORY


def _load_flavor_families():
    global _FLAVOR_FAMILIES
    if _FLAVOR_FAMILIES is None:
        _FLAVOR_FAMILIES = json.loads((DATA / "flavor_cuisine_families.json").read_text())["families"]
    return _FLAVOR_FAMILIES


def _load_cuisine_compat():
    global _CUISINE_COMPAT
    if _CUISINE_COMPAT is None:
        _CUISINE_COMPAT = json.loads((DATA / "cuisine_compatibility.json").read_text())["scores"]
    return _CUISINE_COMPAT


def _load_sku_families():
    global _SKU_FAMILIES
    if _SKU_FAMILIES is None:
        _SKU_FAMILIES = json.loads((DATA / "pantry_sku_families.json").read_text())["skus"]
    return _SKU_FAMILIES


def _load_sku_presentation():
    global _SKU_PRESENTATION
    if _SKU_PRESENTATION is None:
        _SKU_PRESENTATION = json.loads((DATA / "pantry_sku_presentation.json").read_text())["skus"]
    return _SKU_PRESENTATION


def _load_confidence():
    global _CONFIDENCE
    if _CONFIDENCE is None:
        _CONFIDENCE = json.loads((DATA / "confidence_scores.json").read_text())
    return _CONFIDENCE


def _load_lift():
    global _LIFT
    if _LIFT is None:
        _LIFT = json.loads((DATA / "operational_lift.json").read_text())
    return _LIFT


def _load_brand_cuisine():
    global _BRAND_CUISINE
    if _BRAND_CUISINE is None:
        _BRAND_CUISINE = json.loads(
            (DATA / "brand_cuisine_identity.json").read_text())
    return _BRAND_CUISINE


CITY_KEY_TO_LABEL = {
    "weho": "West Hollywood",
    "williamsburg": "Williamsburg",
    "mission": "Mission District",
}
CITY_LABEL_TO_GEO = {
    "West Hollywood": "US-CA-803",
    "Williamsburg": "US-NY-501",
    "Mission District": "US-CA-807",
}


def _resolve_city(city: str) -> str:
    """Accept either key or label, return canonical label."""
    if city in CITY_KEY_TO_LABEL:
        return CITY_KEY_TO_LABEL[city]
    return city


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def get_brand_pantry(brand: str) -> dict:
    """Returns the brand's current available SKUs grouped by category.
    Use this when you need to know what the brand actually stocks."""
    conn = init_db()
    try:
        if brand == "Sweetgreen":
            cat_filter = "AND category LIKE 'sweetgreen_byo_%'"
        else:
            cat_filter = "AND category != 'dish'"
        out: dict = {}
        for r in conn.execute(
            f"SELECT category, item FROM brand_menu_items "
            f"WHERE brand = ? AND available = 1 {cat_filter} "
            f"ORDER BY category, item",
            (brand,),
        ):
            out.setdefault(r["category"], []).append(r["item"])
        return {"brand": brand, "pantry": out, "total_skus": sum(len(v) for v in out.values())}
    finally:
        conn.close()


def get_brand_existing_dishes(brand: str) -> dict:
    """Returns the brand's current menu dishes. New dish proposals must NOT
    duplicate these."""
    conn = init_db()
    try:
        rows = conn.execute(
            "SELECT item FROM brand_menu_items WHERE brand = ? AND category = 'dish' ORDER BY item",
            (brand,),
        ).fetchall()
        dishes = [r["item"] for r in rows]
        return {"brand": brand, "existing_dishes": dishes,
                "note": "If your proposed dish matches any of these by name or ingredient profile, propose something else."}
    finally:
        conn.close()


def get_brand_lto_history(brand: str) -> dict:
    """Returns all newsroom-verified LTO history for this brand. Use this when
    you want to claim relaunch timing intelligence or proven execution."""
    lto = _load_lto()
    entries = lto.get(brand.lower(), [])
    return {"brand": brand, "lto_entries": entries,
            "note": "Each entry has flavor_tags, shipped_years, current_status, and sources. Cite source URLs when referencing an LTO."}


def get_signal_ranking(city: str, top_n: int = 15) -> dict:
    """Returns the top N flavors by cross-source signal score for this city.
    The signal score combines DMA Google Trends + local indie review mentions."""
    city_label = _resolve_city(city)
    geo = CITY_LABEL_TO_GEO.get(city_label)
    conn = init_db()
    try:
        q = """
        SELECT t.term, t.avg_12m AS trend,
               COALESCE(s.mentions, 0) AS mentions,
               COALESCE(s.reviews_hit, 0) AS reviews_hit,
               ROUND((t.avg_12m / 100.0) * 0.5 +
                     (CASE WHEN COALESCE(s.mentions, 0) > 30 THEN 1.0
                           ELSE COALESCE(s.mentions, 0) / 30.0 END) * 0.5, 3) AS signal_score
        FROM trends t
        LEFT JOIN (
          SELECT fm.flavor, SUM(fm.count) AS mentions, COUNT(DISTINCT fm.review_id) AS reviews_hit
          FROM flavor_mentions fm JOIN reviews rv ON rv.id = fm.review_id
          JOIN restaurants r ON r.id = rv.restaurant_id
          WHERE r.city = ? GROUP BY fm.flavor
        ) s ON s.flavor = t.term
        WHERE t.geo = ?
        ORDER BY signal_score DESC LIMIT ?
        """
        rows = [dict(r) for r in conn.execute(q, (city_label, geo, top_n))]
        return {"city": city_label, "rankings": rows}
    finally:
        conn.close()


def get_pantry_fit(brand: str, flavor: str) -> dict:
    """Returns whether the brand can deliver this flavor from current pantry,
    and if not, which SKUs are missing. Ship-now dishes must be deliverable."""
    pf = _load_pantry_fit()
    fit = pf.get(brand, {}).get(flavor)
    if fit is None:
        return {"brand": brand, "flavor": flavor, "known": False,
                "note": "Flavor not in our deliverability matrix. Treat as undeliverable."}
    return {"brand": brand, "flavor": flavor, "known": True, **fit}


def get_pairing_score(flavor_a: str, flavor_b: str) -> dict:
    """Returns cuisine compatibility between two flavors. Score >= 0.7 = allowed
    default. 0.5-0.7 = allowed with cross_family_justification. < 0.5 = blocked.

    Use this BEFORE proposing a dish that combines two flavors from
    potentially-different cuisines (e.g., miso + pesto, harissa + gochujang)."""
    families = _load_flavor_families()
    compat = _load_cuisine_compat()

    fams_a_spec = families.get(flavor_a)
    fams_b_spec = families.get(flavor_b)
    if not fams_a_spec or not fams_b_spec:
        return {"flavor_a": flavor_a, "flavor_b": flavor_b,
                "score": None, "known": False,
                "note": "One or both flavors not in cuisine family map."}

    fams_a = [fams_a_spec["primary"]] + (fams_a_spec.get("secondary") or [])
    fams_b = [fams_b_spec["primary"]] + (fams_b_spec.get("secondary") or [])

    best = -1.0
    best_pair = ("?", "?")
    for a in fams_a:
        for b in fams_b:
            if a == b:
                s = 1.0
            else:
                s = max(compat.get(f"{a}|{b}", 0), compat.get(f"{b}|{a}", 0))
            if s > best:
                best = s
                best_pair = (a, b)

    verdict = ("allowed" if best >= 0.7
               else "requires_cross_family_justification" if best >= 0.5
               else "blocked")
    return {
        "flavor_a": flavor_a, "flavor_b": flavor_b,
        "score": round(best, 2), "family_pair": best_pair,
        "verdict": verdict,
        "known": True,
    }


def verify_restaurant_real(city: str, name: str) -> dict:
    """Checks whether a named restaurant exists in our review dataset for this
    city. Returns verified=true if matched, with the canonical DB name."""
    city_label = _resolve_city(city)
    conn = init_db()
    try:
        rows = conn.execute("SELECT name FROM restaurants WHERE city = ?",
                           (city_label,)).fetchall()
        lower = name.lower().strip()
        for r in rows:
            rl = r["name"].lower()
            if lower == rl or re.search(rf"\b{re.escape(lower)}\b", rl):
                return {"city": city_label, "name_queried": name,
                        "verified": True, "db_name": r["name"]}
        return {"city": city_label, "name_queried": name,
                "verified": False,
                "note": "Not in our scrape. Could be a real famous spot we didn't sample, or could be fabricated. Treat as 'external_reference' if you cite it, not as 'verified comp'."}
    finally:
        conn.close()


def get_evidence_counts(city: str, flavor: str) -> dict:
    """Returns mention counts for this flavor in this city's indie restaurants,
    split by competitive/leading pool. Use this when quoting numbers."""
    city_label = _resolve_city(city)
    conn = init_db()
    try:
        q = """
        SELECT
          SUM(CASE WHEN r.pool_competitive = 1 THEN 1 ELSE 0 END) AS comp,
          SUM(CASE WHEN r.pool_leading = 1 THEN 1 ELSE 0 END) AS lead_,
          COUNT(DISTINCT CASE WHEN r.pool_competitive = 1 THEN r.id END) AS comp_rest,
          COUNT(DISTINCT CASE WHEN r.pool_leading = 1 THEN r.id END) AS lead_rest
        FROM flavor_mentions fm JOIN reviews rv ON rv.id = fm.review_id
        JOIN restaurants r ON r.id = rv.restaurant_id
        WHERE r.city = ? AND fm.flavor = ?
        """
        row = conn.execute(q, (city_label, flavor)).fetchone()
        return {
            "city": city_label, "flavor": flavor,
            "competitive_mentions": row["comp"] or 0,
            "leading_mentions": row["lead_"] or 0,
            "competitive_restaurant_count": row["comp_rest"] or 0,
            "leading_restaurant_count": row["lead_rest"] or 0,
        }
    finally:
        conn.close()


def compute_lift_tier(brand: str, flavor: str) -> dict:
    """Returns operational lift tier (low/med/high) + rollout portability +
    breakdown of missing SKUs by ingredient kind. LTO history downgrades lift."""
    lift = _load_lift()
    rec = lift.get(brand, {}).get(flavor)
    if not rec:
        return {"brand": brand, "flavor": flavor, "known": False}
    return {"brand": brand, "flavor": flavor, "known": True, **rec}


def compute_confidence(city: str, flavor: str, brand: str) -> dict:
    """Returns the composite 0-100 confidence score and its 6-component breakdown.
    Use this for honest, code-checked quantitative claims."""
    city_label = _resolve_city(city)
    c = _load_confidence()
    rec = c.get(city_label, {}).get(flavor, {}).get(brand)
    if not rec:
        return {"city": city_label, "flavor": flavor, "brand": brand, "known": False}
    return {"city": city_label, "flavor": flavor, "brand": brand,
            "known": True, **rec}


def check_brand_cuisine_fit(brand: str, flavor: str) -> dict:
    """Check whether a flavor fits the brand's cuisine identity.

    Use this BEFORE picking any flavor as a recommendation anchor. Off-brand
    flavors are NEVER acceptable recommendations — they ask the brand to extend
    beyond its current cuisine identity, which is a strategic repositioning
    decision, not a quick-win menu suggestion.

    A flavor is on-brand if any of its cuisine families (excluding universal
    modifiers like premium_universal) appears in the brand's identity list.
    Brands with flexible=true (Sweetgreen) consider all flavors on-brand.

    Returns:
      {fit: "on_brand"|"off_brand", brand_identity: [...], flavor_families: [...],
       matched_family: str|null, instruction: str}
    """
    brand_cfg = _load_brand_cuisine().get("brands", {}).get(brand)
    families = _load_flavor_families()

    if not brand_cfg:
        return {"fit": "unknown", "brand": brand,
                "instruction": "Brand not in cuisine identity map. Treat as off-brand."}

    flavor_spec = families.get(flavor)
    if not flavor_spec:
        return {"fit": "unknown", "brand": brand, "flavor": flavor,
                "instruction": "Flavor not in cuisine family map. Skip this candidate."}

    flavor_fams = [flavor_spec["primary"]] + (flavor_spec.get("secondary") or [])
    # Strip premium_universal — it's a modifier, not an identity
    cuisine_fams = [f for f in flavor_fams if f != "premium_universal"]

    if brand_cfg.get("flexible"):
        return {"fit": "on_brand", "brand": brand, "flavor": flavor,
                "brand_identity": brand_cfg["identity"],
                "flavor_families": flavor_fams,
                "matched_family": cuisine_fams[0] if cuisine_fams else None,
                "instruction": "Sweetgreen has flexible cuisine identity. All flavors with cuisine families are on-brand."}

    identity = brand_cfg.get("identity", [])
    matched = None
    for f in cuisine_fams:
        if f in identity:
            matched = f
            break

    if matched:
        return {"fit": "on_brand", "brand": brand, "flavor": flavor,
                "brand_identity": identity, "flavor_families": flavor_fams,
                "matched_family": matched,
                "instruction": f"On-brand: {flavor} ({matched}) is within {brand}'s cuisine identity."}

    return {"fit": "off_brand", "brand": brand, "flavor": flavor,
            "brand_identity": identity, "flavor_families": flavor_fams,
            "matched_family": None,
            "instruction": (
                f"OFF-BRAND: {flavor} cuisine families ({', '.join(cuisine_fams)}) "
                f"don't overlap with {brand}'s identity ({', '.join(identity)}). "
                f"DO NOT recommend this flavor for {brand} — it would require the brand "
                f"to extend its cuisine identity, which is a strategic repositioning "
                f"call, not a quick-win recommendation. Look further down the signal "
                f"ranking for an on-brand option instead."
            )}


def check_brand_positioning(brand: str, claim: str) -> dict:
    """Checks whether a claim about brand operations, customer routing, marketing
    positioning, or business behavior is supported by verified sources.

    Use this BEFORE asserting things like:
      - "Brand X routes customers to Y as a stand-in for Z"
      - "Brand X has been criticized for..."
      - "Customers currently expect Z from this brand"
      - "Brand X positions Y as the de facto Z"

    Returns supporting LTO entries or "no_evidence" if unbacked.
    If no_evidence, you MUST either drop the claim or reframe it as opinion
    ("this could position...", "a customer might expect...") rather than fact."""
    lto = _load_lto()
    entries = lto.get(brand.lower(), [])
    claim_lower = claim.lower()
    matched_sources = []

    # Look for any LTO entry whose notes or item_name relate to the claim
    for entry in entries:
        searchable = " ".join([
            entry.get("item_name", "").lower(),
            entry.get("notes", "").lower(),
            " ".join(entry.get("flavor_tags") or []),
        ])
        # Very loose match — any significant claim word appearing in entry
        claim_words = set(w for w in re.findall(r"[a-z]+", claim_lower) if len(w) > 4)
        entry_words = set(re.findall(r"[a-z]+", searchable))
        if claim_words and len(claim_words & entry_words) >= 2:
            matched_sources.append({
                "item_name": entry["item_name"],
                "sources": entry.get("sources", []),
            })

    if matched_sources:
        return {"brand": brand, "claim": claim, "evidence": "supported",
                "matched_entries": matched_sources}
    return {"brand": brand, "claim": claim, "evidence": "no_evidence",
            "instruction": "No verified source supports this claim. You may NOT assert it as fact. Either drop it from the recommendation, or reframe as opinion using language like 'could position', 'a customer might', 'one read of this is'. Do not use 'routes customers to', 'positions as', 'criticized for', 'currently cedes' without evidence."}


def propose_dish(dish: dict, brand: str, city: str, dish_type: str) -> dict:
    """Final submission gate. Runs all deterministic audits on a proposed dish
    and returns pass/fail with specific feedback. Call this ONLY when you have
    a complete dish to submit. If any audit fails, revise the dish and call
    propose_dish again — do not submit a failing dish to the user."""
    # Audits to run:
    #  1. Deliverability (ship_now only)
    #  2. No-duplicate
    #  3. Cuisine coherence
    #  4. Dish-name truthfulness
    # plus light comp-restaurant sanity check
    from check_cuisine_coherence import (
        pair_score, families_for, load_compat, load_families,
        THRESHOLD_ALLOWED, THRESHOLD_JUSTIFICATION,
    )

    errors = []
    warnings = []

    families = load_families()
    compat = load_compat()
    sku_table = _load_sku_presentation()
    sku_families = _load_sku_families()

    anchor = dish.get("signal_term") or dish.get("target_flavor")
    ingredients = dish.get("ingredients", []) or dish.get("missing_skus", [])

    # Deliverability (ship_now only)
    if dish_type == "ship_now":
        pf = _load_pantry_fit().get(brand, {}).get(anchor)
        if pf and not pf.get("deliverable"):
            errors.append({
                "audit": "deliverability",
                "issue": f"Anchor '{anchor}' is not deliverable from {brand}'s pantry. "
                         f"Missing SKUs: {pf.get('missing')}. This should be a gap_fill, not ship_now.",
            })

    # Anti-duplication
    existing = get_brand_existing_dishes(brand)["existing_dishes"]
    dish_name = dish.get("dish_name", "")
    for ed in existing:
        if ed.lower() == dish_name.lower():
            errors.append({"audit": "duplicate",
                          "issue": f"'{dish_name}' duplicates existing dish '{ed}'."})

    # Cuisine coherence — only for ship_now
    if dish_type == "ship_now" and anchor in families:
        anchor_fams = [families[anchor]["primary"]] + (families[anchor].get("secondary") or [])
        for ing in ingredients:
            ing_key = re.sub(r"\([^)]*\)", "", ing).strip().lower()
            spec = sku_families.get(ing_key)
            if not spec:
                for k, v in sku_families.items():
                    if k in ing_key or ing_key in k:
                        spec = v
                        break
            if not spec:
                continue
            ing_fams = [spec["primary"]] + (spec.get("secondary") or [])
            if any(f in ("universal", "plant_forward", "premium_universal", "unclassified")
                   for f in ing_fams):
                continue
            best = -1.0
            for af in anchor_fams:
                for ifam in ing_fams:
                    s = pair_score(af, ifam, compat)
                    if s > best:
                        best = s
            if best < THRESHOLD_JUSTIFICATION and not dish.get("cross_family_justification"):
                errors.append({
                    "audit": "cuisine_coherence",
                    "issue": f"'{ing}' (family: {ing_fams[0]}) is incompatible with anchor "
                             f"'{anchor}' (family: {anchor_fams[0]}) at score {best:.2f}. "
                             f"Either remove this ingredient or provide cross_family_justification.",
                })

    # Dish-name truthfulness (ship_now only)
    if dish_type == "ship_now":
        from audit_dish_name_truthfulness import audit_dish_name
        result = audit_dish_name(dish_name, ingredients, sku_table)
        for v in result.get("violations", []):
            errors.append({
                "audit": "dish_name_truthfulness",
                "issue": v["issue"],
            })

    verdict = "rejected" if errors else "accepted"
    return {
        "verdict": verdict,
        "errors": errors,
        "warnings": warnings,
        "instruction": ("Revise the dish to address every error in the errors list, "
                        "then call propose_dish again. Do not return a rejected dish to the user."
                        if errors else
                        "Dish passed all deterministic audits. You may include it in your final answer."),
    }


# ---------------------------------------------------------------------------
# Tool registry for Anthropic API
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "get_brand_pantry",
        "description": get_brand_pantry.__doc__.strip(),
        "input_schema": {
            "type": "object",
            "properties": {"brand": {"type": "string", "enum": ["Chipotle", "CAVA", "Sweetgreen"]}},
            "required": ["brand"],
        },
    },
    {
        "name": "get_brand_existing_dishes",
        "description": get_brand_existing_dishes.__doc__.strip(),
        "input_schema": {
            "type": "object",
            "properties": {"brand": {"type": "string", "enum": ["Chipotle", "CAVA", "Sweetgreen"]}},
            "required": ["brand"],
        },
    },
    {
        "name": "get_brand_lto_history",
        "description": get_brand_lto_history.__doc__.strip(),
        "input_schema": {
            "type": "object",
            "properties": {"brand": {"type": "string", "enum": ["Chipotle", "CAVA", "Sweetgreen"]}},
            "required": ["brand"],
        },
    },
    {
        "name": "get_signal_ranking",
        "description": get_signal_ranking.__doc__.strip(),
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "Neighborhood name (e.g., 'Mission District', 'Williamsburg', 'West Hollywood')"},
                "top_n": {"type": "integer", "default": 15},
            },
            "required": ["city"],
        },
    },
    {
        "name": "get_pantry_fit",
        "description": get_pantry_fit.__doc__.strip(),
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string", "enum": ["Chipotle", "CAVA", "Sweetgreen"]},
                "flavor": {"type": "string"},
            },
            "required": ["brand", "flavor"],
        },
    },
    {
        "name": "get_pairing_score",
        "description": get_pairing_score.__doc__.strip(),
        "input_schema": {
            "type": "object",
            "properties": {
                "flavor_a": {"type": "string"},
                "flavor_b": {"type": "string"},
            },
            "required": ["flavor_a", "flavor_b"],
        },
    },
    {
        "name": "verify_restaurant_real",
        "description": verify_restaurant_real.__doc__.strip(),
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["city", "name"],
        },
    },
    {
        "name": "get_evidence_counts",
        "description": get_evidence_counts.__doc__.strip(),
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "flavor": {"type": "string"},
            },
            "required": ["city", "flavor"],
        },
    },
    {
        "name": "compute_lift_tier",
        "description": compute_lift_tier.__doc__.strip(),
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string", "enum": ["Chipotle", "CAVA", "Sweetgreen"]},
                "flavor": {"type": "string"},
            },
            "required": ["brand", "flavor"],
        },
    },
    {
        "name": "compute_confidence",
        "description": compute_confidence.__doc__.strip(),
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "flavor": {"type": "string"},
                "brand": {"type": "string", "enum": ["Chipotle", "CAVA", "Sweetgreen"]},
            },
            "required": ["city", "flavor", "brand"],
        },
    },
    {
        "name": "check_brand_cuisine_fit",
        "description": check_brand_cuisine_fit.__doc__.strip(),
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string", "enum": ["Chipotle", "CAVA", "Sweetgreen"]},
                "flavor": {"type": "string"},
            },
            "required": ["brand", "flavor"],
        },
    },
    {
        "name": "check_brand_positioning",
        "description": check_brand_positioning.__doc__.strip(),
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string", "enum": ["Chipotle", "CAVA", "Sweetgreen"]},
                "claim": {"type": "string", "description": "The factual claim about brand operations you want to make"},
            },
            "required": ["brand", "claim"],
        },
    },
    {
        "name": "propose_dish",
        "description": propose_dish.__doc__.strip(),
        "input_schema": {
            "type": "object",
            "properties": {
                "dish": {"type": "object", "description": "Full dish object (ship_now or gap_fill)"},
                "brand": {"type": "string", "enum": ["Chipotle", "CAVA", "Sweetgreen"]},
                "city": {"type": "string"},
                "dish_type": {"type": "string", "enum": ["ship_now", "gap_fill"]},
            },
            "required": ["dish", "brand", "city", "dish_type"],
        },
    },
]


TOOL_REGISTRY = {
    "get_brand_pantry": get_brand_pantry,
    "get_brand_existing_dishes": get_brand_existing_dishes,
    "get_brand_lto_history": get_brand_lto_history,
    "get_signal_ranking": get_signal_ranking,
    "get_pantry_fit": get_pantry_fit,
    "get_pairing_score": get_pairing_score,
    "verify_restaurant_real": verify_restaurant_real,
    "get_evidence_counts": get_evidence_counts,
    "compute_lift_tier": compute_lift_tier,
    "compute_confidence": compute_confidence,
    "check_brand_cuisine_fit": check_brand_cuisine_fit,
    "check_brand_positioning": check_brand_positioning,
    "propose_dish": propose_dish,
}


def dispatch(tool_name: str, tool_input: dict) -> dict:
    """Execute a tool call and return the result as a dict."""
    fn = TOOL_REGISTRY.get(tool_name)
    if not fn:
        return {"error": f"unknown tool: {tool_name}"}
    try:
        return fn(**tool_input)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    # Quick smoke test
    print("Smoke test — get_signal_ranking for Mission, top 5:")
    print(json.dumps(get_signal_ranking("Mission District", top_n=5), indent=2))
    print()
    print("check_brand_positioning unsupported claim:")
    print(json.dumps(check_brand_positioning("Chipotle",
        "routes customers to tomatillo red chili salsa as a stand-in for mole"), indent=2))
    print()
    print("get_pairing_score miso + pesto:")
    print(json.dumps(get_pairing_score("miso", "pesto"), indent=2))
