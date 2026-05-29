"""Taste Engine — local Streamlit report interface.

Launch:
    streamlit run app.py

Reads `data/hermes.db` + `data/{city}_dish_recommendations_v6.json`.
Six tabs: Overview / Recommendations / Trends / vs Naive LLM / Evidence / Methodology.

Color palette designed for the fast-casual aesthetic of Chipotle / CAVA / Sweetgreen —
cream + olive + terracotta for brand chips, slate / tan / rust for meaning indicators.

────────────────────────────────────────────────────────────────────────────
STRUCTURAL CONTRACT — brand × flavor displays
────────────────────────────────────────────────────────────────────────────
Any display surface that pairs a brand with a flavor recommendation
("{brand} should add {flavor}", "{brand} is missing {flavor}",
"{brand}'s gap is {flavor}") MUST source its data through
load_on_brand_signal_ranking(). Off-brand flavors (e.g. truffle for Chipotle,
mole for CAVA) are filtered AT THE DATA LAYER so no render-site code can
accidentally bypass the constraint.

Exempt: informational displays that show full data WITHOUT framing it as a
recommendation (e.g. the Pantry "Cannot deliver" list, the deliverability
heatmap matrix). These show the raw matrix because their purpose is analytical,
not actionable.

Enforced by: scripts/test_no_off_brand_displays.py — run before any deploy.
Source of truth: data/brand_cuisine_identity.json.
"""
import json
import sqlite3
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from hermes.db import init_db  # noqa: E402

DATA = ROOT / "data"

# ───────────────────────── config ─────────────────────────

st.set_page_config(
    page_title="Taste Engine",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

CITIES = {
    "weho":         {"label": "West Hollywood",  "geo": "US-CA-803", "emoji": "🌴"},
    "williamsburg": {"label": "Williamsburg",    "geo": "US-NY-501", "emoji": "🌉"},
    "mission":      {"label": "Mission District","geo": "US-CA-807", "emoji": "🌁"},
}

BRAND_COLORS = {
    "Chipotle":   "#A33A1F",   # brick red — BRAND chips only
    "CAVA":       "#C46B45",   # terracotta — BRAND chips only
    "Sweetgreen": "#5A8B5A",   # leaf green — BRAND chips only
}

# Meaning colors — strictly separate from brand colors to avoid the
# Chipotle-brick / LOW-brick collision the audit flagged.
# These are a slate / tan / rust scale used ONLY for confidence, maturity,
# lift, and strength labels.
MEANING_COLORS = {
    "high":      "#2C5F87",   # deep slate — positive / strong / safe / ship
    "mid":       "#9C7F4A",   # warm tan — neutral / moderate / steady
    "low":       "#A04E2C",   # rust — negative / weak / blocked
    "muted":     "#8B8378",   # gray — background / no signal
}

CONFIDENCE_COLORS = {
    "HIGH": MEANING_COLORS["high"],
    "MID":  MEANING_COLORS["mid"],
    "LOW":  MEANING_COLORS["low"],
}

# Verbal strength labels — non-statistician-friendly mapping. ALL user-facing
# scores are normalized to 0-100 (matching Google Trends and composite confidence).
# Internal pool scores remain 0-1; we multiply by 100 at render time.
STRENGTH_LABELS = [
    (75, "Very strong", "#145A32"),   # dark green
    (55, "Strong",      "#1E8449"),   # green
    (35, "Notable",     "#D4AC0D"),   # amber
    (20, "Emerging",    "#CA6F1E"),   # orange
    (0,  "Background",  "#922B21"),   # dark red
]

# Row background tints for the top-signals heatmap — rgba of the label color
# at low opacity so the text stays readable.
STRENGTH_BG = {
    "Very strong": "rgba(20,90,50,0.10)",
    "Strong":      "rgba(30,132,73,0.08)",
    "Notable":     "rgba(212,172,13,0.08)",
    "Emerging":    "rgba(202,111,30,0.07)",
    "Background":  "rgba(146,43,33,0.07)",
}


def _to_100(score) -> float | None:
    """Normalize a score to the 0-100 user-facing scale. Accepts None, 0-1
    floats (internal pool scores), or already-0-100 ints/floats."""
    if score is None:
        return None
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    # Heuristic: anything ≤ 1.0 is the internal 0-1 scale; multiply.
    # Anything > 1.0 is already on the 0-100 scale; pass through.
    if 0 <= s <= 1.0:
        return s * 100
    return s


def strength_label(score) -> tuple[str, str]:
    """Returns (label, color) for a score. Accepts 0-1 internal or 0-100 user."""
    s = _to_100(score) or 0
    for cutoff, label, color in STRENGTH_LABELS:
        if s >= cutoff:
            return label, color
    return "Background", "#a09683"


def strength_pill(score, show_score: bool = True) -> str:
    """Inline pill HTML showing verbal strength + (optionally) the 0-100 score."""
    label, color = strength_label(score)
    score_100 = _to_100(score)
    extra = (f' <span style="font-family: monospace; font-size: 0.72rem; '
             f'opacity: 0.75;">({score_100:.0f})</span>') if (show_score and score_100 is not None) else ""
    return (
        f'<span style="background: {color}; color: white; padding: 0.12rem 0.55rem; '
        f'border-radius: 999px; font-size: 0.78rem; font-weight: 600; '
        f'letter-spacing: 0.02em;">{label}{extra}</span>'
    )


# Pantry-fit grade labels — less judgmental than A-F.
# These describe pantry/demand alignment, not company performance.
GRADE_LABELS = {
    "A": ("Strong fit",     MEANING_COLORS["high"]),
    "B": ("Good fit",       MEANING_COLORS["high"]),
    "C": ("Partial fit",    MEANING_COLORS["mid"]),
    "D": ("Limited fit",    MEANING_COLORS["mid"]),
    "F": ("No current fit", MEANING_COLORS["low"]),
}


def humanize_scores(text: str) -> str:
    """Replace raw '0.917'-style scores in narrative text with strength labels
    + the 0-100 equivalent. Conservative — only matches the typical 0.XXX float
    pattern that's almost certainly a pool score from our scoring layer."""
    import re
    def repl(m: "re.Match") -> str:
        try:
            s = float(m.group(0))
            if 0.0 <= s <= 1.0:
                label, _color = strength_label(s)
                return f"<strong>{label}</strong> ({int(s*100)}/100)"
        except ValueError:
            pass
        return m.group(0)
    return re.sub(r"\b0\.\d{2,3}\b", repl, text)

# ───────────────────────── custom CSS ─────────────────────────

st.markdown("""
<style>
/* Tighten default Streamlit padding so the report feels less spacy */
.main .block-container { padding-top: 2rem; padding-bottom: 3rem; max-width: 1200px; }

/* Brand + confidence pills */
.pill {
    display: inline-block;
    padding: 0.15rem 0.6rem;
    border-radius: 999px;
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.02em;
    color: white;
    margin-right: 0.3rem;
}
.pill-outline {
    display: inline-block;
    padding: 0.1rem 0.55rem;
    border-radius: 999px;
    font-size: 0.7rem;
    font-weight: 500;
    border: 1px solid #c9bfae;
    background: #fffaf2;
    color: #5a4f3c;
    margin: 0.1rem 0.2rem 0.1rem 0;
}

/* Dish card — equal starting dimensions so a row of two cards starts visually
   aligned. The methodology expander pushes the card taller when opened, which
   is desirable: closed-state is the comparable surface. */
.dish-card {
    background: #fffaf2;
    border: 1px solid #d6cab2;
    border-left-width: 5px;
    border-radius: 8px;
    padding: 1.1rem 1.3rem;
    margin-bottom: 1rem;
    box-shadow: 0 1px 3px rgba(58, 50, 38, 0.06);
    min-height: 360px;
    display: flex;
    flex-direction: column;
}
.dish-card > details {
    margin-top: auto;  /* push the expander to the bottom so two cards align */
}
.dish-name {
    font-family: Georgia, 'Times New Roman', serif;
    font-size: 1.25rem;
    font-weight: 600;
    color: #2A2825;
    margin: 0.15rem 0 0.3rem 0;
}
.dish-tagline {
    color: #5a4f3c;
    font-style: italic;
    margin-bottom: 0.7rem;
    line-height: 1.4;
}
.dish-meta-label {
    text-transform: uppercase;
    font-size: 0.65rem;
    letter-spacing: 0.08em;
    color: #8a7c63;
    font-weight: 600;
    margin-top: 0.6rem;
}
.dish-meta-value {
    font-size: 0.9rem;
    color: #2A2825;
    line-height: 1.4;
}

/* Top metric boxes */
[data-testid="stMetricValue"] { font-family: Georgia, serif; font-weight: 600; }

/* Sidebar */
[data-testid="stSidebar"] { background: #EDE7DA; }

/* Section dividers */
hr { border-color: #d6cab2; }
</style>
""", unsafe_allow_html=True)


# ───────────────────────── data helpers ─────────────────────────

@st.cache_resource
def get_conn() -> sqlite3.Connection:
    # Streamlit re-renders on background threads; default sqlite3.connect rejects
    # cross-thread usage. check_same_thread=False is safe here since we only read.
    from hermes.db import DB_PATH, SCHEMA
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)  # idempotent
    return conn


@st.cache_data(ttl=60)
def load_signal_ranking_dual(city: str, geo: str, limit: int = 25) -> list[dict]:
    """Returns top flavors with BOTH competitive + leading pool scores."""
    conn = get_conn()
    sys.path.insert(0, str(ROOT / "scripts"))
    from dish_generator import signal_ranking_dual
    return signal_ranking_dual(conn, city, geo, limit=limit)


@st.cache_data(ttl=60)
def load_signal_ranking(city: str, geo: str, limit: int = 25) -> list[dict]:
    """Cross-source signal score per city."""
    q = """
    SELECT
      t.term,
      t.avg_12m   AS trend,
      t.recent_4w,
      t.peak,
      t.trend     AS trend_direction,
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
      WHERE r.city = ?
      GROUP BY fm.flavor
    ) s ON s.flavor = t.term
    WHERE t.geo = ?
    ORDER BY signal_score DESC
    LIMIT ?
    """
    conn = get_conn()
    return [dict(r) for r in conn.execute(q, (CITIES[city]["label"], geo, limit))]


@st.cache_data(ttl=60)
def load_pantry(brand: str) -> list[dict]:
    conn = get_conn()
    if brand == "Sweetgreen":
        cat_filter = "AND category LIKE 'sweetgreen_byo_%'"
    else:
        cat_filter = "AND category != 'dish'"
    rows = conn.execute(
        f"SELECT category, item, available, location, ingredients_text "
        f"FROM brand_menu_items "
        f"WHERE brand = ? {cat_filter} "
        f"ORDER BY category, item",
        (brand,),
    ).fetchall()
    return [dict(r) for r in rows]


@st.cache_data(ttl=60)
def load_existing_dishes(brand: str) -> list[str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT item FROM brand_menu_items WHERE brand = ? AND category = 'dish' ORDER BY item",
        (brand,),
    ).fetchall()
    return [r["item"] for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# DURABLE BRAND-CUISINE FILTER — STRUCTURAL CONTRACT
#
# Any display surface that surfaces "brand X should ship flavor Y" or "brand X
# is missing flavor Y" MUST go through load_on_brand_signal_ranking() below,
# not raw load_signal_ranking_dual() with manual iteration. Off-brand flavors
# (e.g. truffle for Chipotle, mole for CAVA) get filtered AT THE DATA LAYER so
# no display code can bypass the constraint by accident.
#
# The agent path is already filtered via dish_tools.check_brand_cuisine_fit
# during recommendation generation. The display path uses the same check.
# Single source of truth: data/brand_cuisine_identity.json.
#
# Test: scripts/test_no_off_brand_displays.py asserts every visible
# (brand × flavor) combination passes the cuisine fit check.
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_on_brand_signal_ranking(city: str, geo: str, brand: str, limit: int = 15) -> list[dict]:
    """Top signals for this city, FILTERED to flavors on-brand for the brand.

    Any code that displays "{brand} can't deliver {flavor}" or "{brand} should
    add {flavor}" must source its flavor list through this function, not raw
    load_signal_ranking_dual. Off-brand flavors are dropped at the data layer.

    We over-pull (limit × 3) before filtering so the returned list still has
    `limit` items in the typical case where a third of top signals are
    off-brand for a given brand.
    """
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "scripts"))
    from dish_tools import check_brand_cuisine_fit
    raw = load_signal_ranking_dual(city, geo, limit=limit * 3)
    on_brand = []
    for s in raw:
        fit = check_brand_cuisine_fit(brand, s["term"])
        if fit.get("fit") == "on_brand":
            on_brand.append(s)
        if len(on_brand) >= limit:
            break
    return on_brand


@st.cache_data(ttl=60)
def load_dishes(city: str) -> list[dict]:
    """Prefer v6 (enriched with confidence/lift/LTO/maturity). Fall back to v5
    then v4 if not yet regenerated for this city."""
    for suffix in ("v6", "v5", "v4"):
        path = DATA / f"{city}_dish_recommendations_{suffix}.json"
        if path.exists():
            return json.loads(path.read_text())
    return []


@st.cache_data(ttl=60)
def load_pantry_fit() -> dict:
    path = DATA / "pantry_fit.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


@st.cache_data(ttl=60)
def load_flavor_definitions() -> dict:
    path = DATA / "flavor_definitions.json"
    if not path.exists():
        return {}
    d = json.loads(path.read_text())
    return {k: v for k, v in d.items() if not k.startswith("_")}


def _city_label(city: str) -> str:
    """Accept either the city KEY ('weho') or LABEL ('West Hollywood').
    DB stores labels in restaurants.city; queries got passed keys and silently
    returned empty rows. This normalizer fixes it in one place."""
    if city in CITIES:
        return CITIES[city]["label"]
    return city


_QUARTER_EXPR = """
  substr(rv.review_date, 1, 4) || '-Q' ||
    CASE
      WHEN cast(substr(rv.review_date, 6, 2) AS INTEGER) BETWEEN 1 AND 3 THEN '1'
      WHEN cast(substr(rv.review_date, 6, 2) AS INTEGER) BETWEEN 4 AND 6 THEN '2'
      WHEN cast(substr(rv.review_date, 6, 2) AS INTEGER) BETWEEN 7 AND 9 THEN '3'
      ELSE '4'
    END
""".strip()


@st.cache_data(ttl=60)
def monthly_mentions(city: str, flavors: list[str] | None = None,
                     pool: str | None = None) -> "pd.DataFrame":
    """Per-flavor per-quarter mention counts for a city. Returns long-form DataFrame
    (one row per flavor × quarter) so it plots cleanly in Altair/Streamlit."""
    import pandas as pd
    conn = get_conn()
    pool_clause = ""
    if pool == "competitive":
        pool_clause = "AND r.pool_competitive = 1"
    elif pool == "leading":
        pool_clause = "AND r.pool_leading = 1"
    flav_clause = ""
    args: list = [_city_label(city)]
    if flavors:
        placeholders = ",".join("?" * len(flavors))
        flav_clause = f"AND fm.flavor IN ({placeholders})"
        args.extend(flavors)
    q = f"""
    SELECT
      fm.flavor,
      {_QUARTER_EXPR} AS quarter,
      COUNT(DISTINCT rv.id) AS mentions
    FROM flavor_mentions fm
    JOIN reviews rv ON rv.id = fm.review_id
    JOIN restaurants r ON r.id = rv.restaurant_id
    WHERE r.city = ? AND rv.review_date IS NOT NULL
      {pool_clause}
      {flav_clause}
    GROUP BY fm.flavor, quarter
    ORDER BY quarter, fm.flavor
    """
    rows = conn.execute(q, args).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


@st.cache_data(ttl=60)
def quarterly_mentions(city: str, top_n: int = 15) -> "pd.DataFrame":
    """Per-flavor per-quarter heatmap data."""
    import pandas as pd
    conn = get_conn()
    q = """
    WITH top_flavors AS (
      SELECT fm.flavor, SUM(fm.count) AS total
      FROM flavor_mentions fm
      JOIN reviews rv ON rv.id = fm.review_id
      JOIN restaurants r ON r.id = rv.restaurant_id
      WHERE r.city = ? AND rv.review_date IS NOT NULL
      GROUP BY fm.flavor
      ORDER BY total DESC
      LIMIT ?
    )
    SELECT
      fm.flavor,
      substr(rv.review_date, 1, 4) || '-Q' ||
        CASE
          WHEN cast(substr(rv.review_date, 6, 2) AS INTEGER) BETWEEN 1 AND 3 THEN '1'
          WHEN cast(substr(rv.review_date, 6, 2) AS INTEGER) BETWEEN 4 AND 6 THEN '2'
          WHEN cast(substr(rv.review_date, 6, 2) AS INTEGER) BETWEEN 7 AND 9 THEN '3'
          ELSE '4'
        END AS quarter,
      SUM(fm.count) AS mentions
    FROM flavor_mentions fm
    JOIN reviews rv ON rv.id = fm.review_id
    JOIN restaurants r ON r.id = rv.restaurant_id
    WHERE r.city = ?
      AND rv.review_date IS NOT NULL
      AND fm.flavor IN (SELECT flavor FROM top_flavors)
    GROUP BY fm.flavor, quarter
    ORDER BY quarter, fm.flavor
    """
    label = _city_label(city)
    rows = conn.execute(q, (label, top_n, label)).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


@st.cache_data(ttl=60)
def flavor_by_city(flavor: str) -> "pd.DataFrame":
    """Cross-city quarterly mention counts for one flavor."""
    import pandas as pd
    conn = get_conn()
    q = f"""
    SELECT
      r.city,
      {_QUARTER_EXPR} AS quarter,
      COUNT(DISTINCT rv.id) AS mentions
    FROM flavor_mentions fm
    JOIN reviews rv ON rv.id = fm.review_id
    JOIN restaurants r ON r.id = rv.restaurant_id
    WHERE fm.flavor = ? AND rv.review_date IS NOT NULL
    GROUP BY r.city, quarter
    ORDER BY quarter, r.city
    """
    rows = conn.execute(q, (flavor,)).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


@st.cache_data(ttl=60)
def load_exec_overviews() -> dict:
    path = DATA / "exec_overviews.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


@st.cache_data(ttl=60)
def top_flavors_in_city(city: str, n: int = 10) -> list[str]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT fm.flavor, SUM(fm.count) AS total
           FROM flavor_mentions fm
           JOIN reviews rv ON rv.id = fm.review_id
           JOIN restaurants r ON r.id = rv.restaurant_id
           WHERE r.city = ? AND rv.review_date IS NOT NULL
           GROUP BY fm.flavor ORDER BY total DESC LIMIT ?""",
        (_city_label(city), n),
    ).fetchall()
    return [r["flavor"] for r in rows]


@st.cache_data(ttl=60)
def db_counts() -> dict:
    conn = get_conn()
    counts = {}
    for r in conn.execute("""
        SELECT city, COUNT(*) AS n_rest,
               (SELECT COUNT(*) FROM reviews rv JOIN restaurants r2 ON r2.id = rv.restaurant_id
                WHERE r2.city = r.city) AS n_reviews
        FROM restaurants r GROUP BY city
    """):
        counts[r["city"]] = {"restaurants": r["n_rest"], "reviews": r["n_reviews"]}
    counts["_totals"] = {
        "restaurants": conn.execute("SELECT COUNT(*) FROM restaurants").fetchone()[0],
        "reviews": conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0],
        "pantry_items": conn.execute("SELECT COUNT(*) FROM brand_menu_items").fetchone()[0],
        "flavor_mentions": conn.execute("SELECT COUNT(*) FROM flavor_mentions").fetchone()[0],
    }
    return counts


# ───────────────────────── render helpers ─────────────────────────

def pill(text: str, color: str) -> str:
    return f'<span class="pill" style="background:{color}">{text}</span>'


def confidence_pill(level: str) -> str:
    return pill(level, CONFIDENCE_COLORS.get(level, "#888"))


def brand_pill(brand: str) -> str:
    return pill(brand, BRAND_COLORS.get(brand, "#888"))


def brand_banner(brand: str, subtitle: str = "") -> str:
    """Full-width brand banner matching the Recommendations tab style."""
    bc = BRAND_COLORS.get(brand, "#888")
    sub = (f'<div style="font-size:0.78rem; opacity:0.85; letter-spacing:0.05em;">'
           f'· {subtitle}</div>') if subtitle else ""
    return (
        f'<div style="background:{bc}; color:white; padding:0.45rem 1.1rem; '
        f'border-radius:6px; margin-bottom:0.5rem; display:flex; align-items:center; gap:0.7rem;">'
        f'<div style="font-family:Georgia, serif; font-size:1.05rem; font-weight:700; '
        f'letter-spacing:0.12em; text-transform:uppercase;">{brand}</div>'
        f'{sub}</div>'
    )


def direction_arrow(d: str | None) -> str:
    return {"rising": "↑", "falling": "↓", "flat": "→"}.get(d, "")


def _coerce_score(v) -> float | None:
    if v is None: return None
    if isinstance(v, (int, float)): return float(v)
    try: return float(v)
    except (TypeError, ValueError): return None


# ── New structured-layer badges (v6 cards) ──────────────────────────────

MATURITY_COLORS = {
    "Rising":      MEANING_COLORS["mid"],   # warm tan — chef-driven, watch
    "Established": MEANING_COLORS["high"],  # slate — safe to ship
    "Steady":      MEANING_COLORS["mid"],
    "Peak":        MEANING_COLORS["low"],   # rust — fad-tail risk
    "Weak":        MEANING_COLORS["muted"],
}

LIFT_COLORS = {
    "low":    MEANING_COLORS["high"],
    "medium": MEANING_COLORS["mid"],
    "high":   MEANING_COLORS["low"],
}


def confidence_score_badge(score) -> str:
    """Numeric 0-100 composite confidence badge — Pillar 1 slate, score conveys level."""
    if score is None:
        return ""
    # Always Pillar 1 color (slate) — confidence is the composite signal strength score.
    # The number itself communicates level; color communicates pillar attribution.
    color = MEANING_COLORS["high"]
    return (
        f'<span style="background:{color}; color:white; padding:0.15rem 0.6rem; '
        f'border-radius:4px; font-size:0.78rem; font-weight:700; letter-spacing:0.04em;" '
        f'title="Pillar 1 — Signal Fusion: composite of Trend 20% / Local 30% / Maturity 15% / Feasibility 20% / Recency 10% / LTO 5%">'
        f'Confidence {int(score)}</span>'
    )


def maturity_badge(stage: str | None) -> str:
    if not stage:
        return ""
    # Always Pillar 2 color (tan) so the badge visually attributes to Pillar 2 — Trend Maturity.
    # Stage quality (Rising / Established / Peak) is conveyed by the text, not the color.
    color = MEANING_COLORS["mid"]
    return (
        f'<span style="background:transparent; color:{color}; border:1px solid {color}; '
        f'padding:0.12rem 0.55rem; border-radius:4px; font-size:0.72rem; '
        f'font-weight:600; letter-spacing:0.05em;" title="Pillar 2 — Trend Maturity: competitive vs leading pool spread">'
        f'{stage.upper()}</span>'
    )


def lift_badge(tier: str | None, portability: str | None) -> str:
    if not tier:
        return ""
    # Always Pillar 3 color (rust) so the badge visually attributes to Pillar 3 — Innovation Feasibility.
    # Tier quality (low / medium / high lift) is conveyed by the text, not the color.
    color = MEANING_COLORS["low"]
    port_text = f" · {portability.upper()}" if portability else ""
    return (
        f'<span style="background:{color}; color:white; padding:0.12rem 0.55rem; '
        f'border-radius:4px; font-size:0.72rem; font-weight:600; letter-spacing:0.04em;" '
        f'title="Pillar 3 — Innovation Feasibility: operational lift tier + rollout portability">'
        f'Lift {tier.upper()}{port_text}</span>'
    )


def lto_proven_badge(lto_proven: dict | None) -> str:
    if not lto_proven:
        return ""
    years = lto_proven.get("years") or []
    year_text = ", ".join(str(y) for y in years[:3])
    return (
        f'<span style="background:#2c3e50; color:#fff; padding:0.12rem 0.55rem; '
        f'border-radius:4px; font-size:0.72rem; font-weight:600; letter-spacing:0.05em;" '
        f'title="Brand has shipped this flavor before — supply chain dormant, not new">'
        f'PROVEN · {year_text}</span>'
    )


@st.cache_data(ttl=60)
def load_comp_audit() -> dict:
    path = DATA / "comp_restaurant_audit.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def comp_audit_for(city_key: str, brand: str, dish_name: str) -> dict | None:
    """Find the audit entry matching this card."""
    audit = load_comp_audit()
    city_audits = audit.get(city_key, [])
    for entry in city_audits:
        if entry["brand"] == brand and entry.get("dish_name") == dish_name:
            return entry["audit"]
    return None


def comp_badge(audit: dict | None) -> str:
    if not audit or audit.get("total_named", 0) == 0:
        return ""
    v = audit["verified_count"]
    e = audit["external_count"]
    color = "#3D5A40" if v > 0 else "#a09683"
    return (
        f'<span style="background:transparent; color:{color}; '
        f'border:1px solid {color}; padding:0.12rem 0.55rem; '
        f'border-radius:4px; font-size:0.72rem; font-weight:600; '
        f'letter-spacing:0.04em;" '
        f'title="Comp restaurants named in card prose, checked against our restaurants table">'
        f'Comps ✓{v} · ext {e}</span>'
    )


def comp_detail(audit: dict | None) -> str:
    if not audit or audit.get("total_named", 0) == 0:
        return ""
    verifs = audit.get("all_verifications", [])
    if not verifs:
        return ""
    lines = []
    for v in verifs:
        if v["status"] == "verified":
            lines.append(
                f'<span style="color:#3D5A40;">✓</span> <strong>{v["name"]}</strong> '
                f'<span style="color:#5a4f3c; font-size:0.78rem;">→ {v["matched_in_db"]}</span>'
            )
        else:
            lines.append(
                f'<span style="color:#a09683;">○</span> <em>{v["name"]}</em> '
                f'<span style="color:#5a4f3c; font-size:0.78rem;">(external reference)</span>'
            )
    body = "<br>".join(lines)
    return (
        f'<div class="dish-meta-label">Comp restaurants</div>'
        f'<div class="dish-meta-value" style="font-size:0.82rem; line-height:1.5;">'
        f'{body}</div>'
    )


def lto_proven_detail(lto: dict | None) -> str:
    if not lto:
        return ""
    sources = lto.get("sources") or []
    source_links = " ".join(
        f'<a href="{s}" target="_blank" style="font-size:0.72rem; color:#5a6e88;">[source {i+1}]</a>'
        for i, s in enumerate(sources[:2])
    )
    return (
        f'<div class="dish-meta-label">Proven execution</div>'
        f'<div class="dish-meta-value" style="font-size:0.82rem;">'
        f'<strong>{lto["item_name"]}</strong> — shipped {", ".join(str(y) for y in lto.get("years",[]))} '
        f'({lto.get("scope","national")}). {source_links}'
        f'</div>'
    )


def _ship_now_meta_caption(d: dict) -> str:
    """One-line gray caption summarizing operational meta: lift · portability ·
    proven · comps. Used in place of the previous 4+ badges."""
    parts = []
    lift = d.get("lift_tier")
    port = d.get("rollout_portability")
    if lift:
        parts.append(f"{lift.capitalize()} lift")
    if port:
        parts.append(port)
    lto = d.get("lto_proven")
    if lto and lto.get("years"):
        years = lto["years"]
        if len(years) == 1:
            parts.append(f"proven {years[0]}")
        else:
            parts.append(f"proven {len(years)}×")
    return " · ".join(parts)


def _long_shot_badge(d: dict) -> str:
    if not d.get("long_shot"):
        return ""
    return (
        f'<span style="background:transparent; color:{MEANING_COLORS["mid"]}; '
        f'border:1px dashed {MEANING_COLORS["mid"]}; padding:0.12rem 0.55rem; '
        f'border-radius:4px; font-size:0.72rem; font-weight:600; letter-spacing:0.04em;" '
        f'title="Lower-volume signal, but this is the strongest flavor we can recommend that stays on-cuisine for this brand.">'
        f'LONG SHOT · ON-CUISINE</span>'
    )


def render_ship_now_card(d: dict):
    color = BRAND_COLORS.get(d["brand"], "#888")
    ings_chips = "".join(f'<span class="pill-outline">{i}</span>' for i in d.get("ingredients", []))
    score = _coerce_score(d.get("signal_score"))
    pill = strength_pill(score, show_score=True) if score is not None else ""
    rank_note = d.get("signal_rank_note", "")

    # v6 structured-layer badges
    conf_score = d.get("confidence_score")
    maturity = d.get("maturity_stage")
    lift_tier = d.get("lift_tier")
    port = d.get("rollout_portability")
    lto = d.get("lto_proven")
    ev = d.get("evidence_counts") or {}

    # Card city is the active sidebar selection
    comp_audit = comp_audit_for(
        city_key,
        d["brand"],
        d.get("dish_name") or f'gap_fill: {d.get("target_flavor")}',
    )
    # NEW: dish name leads; only 3 essential signals as primary badges
    # (confidence number, maturity stage, proven). Everything else moves to
    # a single-line gray meta caption + the methodology expander.
    meta_caption = _ship_now_meta_caption(d)
    primary_badges = " ".join(b for b in (
        confidence_score_badge(conf_score),
        maturity_badge(maturity),
        lto_proven_badge(lto),
        _long_shot_badge(d),
    ) if b)

    evidence_one_liner = ""
    if ev:
        comp_n = ev.get("competitive_mentions", 0)
        rest_n = (comp_audit or {}).get("verified_count", 0) if comp_audit else 0
        evidence_one_liner = (
            f'<div style="font-size:0.85rem; color:#5a4f3c; margin: 0.2rem 0 0.5rem 0;">'
            f'{comp_n} indie mentions · '
            f'{ev.get("recent_12mo_mentions",0)} in last 12 months · '
            f'Google Trends {ev.get("dma_trend",0):.0f}/100'
            f'</div>'
        )

    # Build hidden detail (rank note + novelty + confidence reason + ingredients + comps)
    expand_body = ""
    if d.get("ingredients"):
        expand_body += (
            f'<div class="dish-meta-label">Ingredients (pantry only)</div>'
            f'<div>{ings_chips}</div>'
        )
    if rank_note:
        expand_body += (
            f'<div class="dish-meta-label">Rank in city</div>'
            f'<div class="dish-meta-value" style="font-size:0.85rem; color:#5a4f3c;">{rank_note}</div>'
        )
    if d.get("novelty_check"):
        expand_body += (
            f'<div class="dish-meta-label">Novelty check</div>'
            f'<div class="dish-meta-value">{d.get("novelty_check","")}</div>'
        )
    if d.get("confidence_reason"):
        expand_body += (
            f'<div class="dish-meta-label">Why we recommend this</div>'
            f'<div class="dish-meta-value">{d.get("confidence_reason","")}</div>'
        )
    expand_body += lto_proven_detail(lto)
    expand_body += comp_detail(comp_audit)
    if lift_tier:
        expand_body += (
            f'<div class="dish-meta-label">Operational lift</div>'
            f'<div class="dish-meta-value" style="font-size:0.85rem;">'
            f'{lift_tier.capitalize()} · {port or "national"} rollout'
            f'</div>'
        )

    border_style = "dashed" if d.get("long_shot") else "solid"
    html = (
        f'<div class="dish-card" style="border-left-color: {color}; border-style: {border_style};">'
        # Verdict tag — single chip top-left. Brand is now on the vertical
        # color tab to the left of the card row, so it's no longer repeated here.
        f'<div style="display:flex; align-items:center; gap:0.4rem; margin-bottom:0.5rem;">'
        f'<span style="background:{MEANING_COLORS["high"]}; color:white; padding:0.15rem 0.6rem; '
        f'border-radius:4px; font-size:0.7rem; font-weight:700; letter-spacing:0.06em;">SHIP NOW</span>'
        f'</div>'
        # DISH NAME — the lead
        f'<div class="dish-name" style="font-family: Georgia, serif; font-size: 1.35rem; '
        f'font-weight: 700; line-height: 1.25; margin: 0.3rem 0 0.25rem 0;">{d.get("dish_name","?")}</div>'
        # Tagline
        f'<div style="font-size: 0.93rem; color: #5a4f3c; line-height: 1.45; margin-bottom: 0.55rem;">'
        f'{d.get("tagline","")}</div>'
        # Three primary badges
        + (f'<div style="display:flex; align-items:center; gap:0.35rem; flex-wrap:wrap; margin-bottom:0.4rem;">'
           f'{primary_badges}</div>' if primary_badges else "")
        # One-line evidence
        + evidence_one_liner
        # Meta caption — single gray line
        + (f'<div style="font-size:0.78rem; color:#7a6f5c; letter-spacing:0.01em; margin-bottom:0.6rem;">{meta_caption}</div>'
           if meta_caption else "")
        # Expander stub — Streamlit's st.expander would split, so use details/summary
        + (f'<details style="margin-top:0.4rem;"><summary style="cursor:pointer; font-size:0.82rem; color:{MEANING_COLORS["high"]}; font-weight:600; user-select:none;">Why we recommend this · methodology</summary>'
           f'<div style="margin-top:0.5rem;">{expand_body}</div>'
           f'</details>' if expand_body else "")
        + '</div>'
    )
    if hasattr(st, "html"): st.html(html)
    else: st.markdown(html, unsafe_allow_html=True)


def render_gap_fill_card(d: dict):
    color = BRAND_COLORS.get(d["brand"], "#888")
    # v6 lift breakdown overrides legacy missing_skus list when present
    breakdown = d.get("lift_breakdown") or []
    if breakdown:
        skus_chips = "".join(
            f'<span style="display:inline-block; padding: 0.1rem 0.55rem; border-radius: 999px; '
            f'font-size: 0.7rem; font-weight: 600; border: 1px solid {LIFT_COLORS.get(s.get("lift","medium"),"#8E4A3C")}; '
            f'background: #faf2ec; color: {LIFT_COLORS.get(s.get("lift","medium"),"#8E4A3C")}; '
            f'margin: 0.1rem 0.2rem 0.1rem 0;">+ {s["sku"]} <span style="opacity:0.6">({s["lift"][:3]})</span></span>'
            for s in breakdown
        )
    else:
        skus_chips = "".join(
            f'<span style="display:inline-block; padding: 0.1rem 0.55rem; border-radius: 999px; '
            f'font-size: 0.7rem; font-weight: 600; border: 1px solid #8E4A3C; background: #faf2ec; '
            f'color: #8E4A3C; margin: 0.1rem 0.2rem 0.1rem 0;">+ {s}</span>'
            for s in d.get("missing_skus", [])
        )
    score = _coerce_score(d.get("signal_score"))
    pill = strength_pill(score, show_score=True) if score is not None else ""

    # v6 structured-layer badges
    conf_score = d.get("confidence_score")
    maturity = d.get("maturity_stage")
    lift_tier = d.get("lift_tier")
    port = d.get("rollout_portability")
    lto = d.get("lto_proven")
    ev = d.get("evidence_counts") or {}

    # Card city is the active sidebar selection
    comp_audit = comp_audit_for(
        city_key,
        d["brand"],
        d.get("dish_name") or f'gap_fill: {d.get("target_flavor")}',
    )
    # Same simplification as ship_now: target flavor leads, three primary
    # badges, one-line evidence, gray meta caption, everything else in expand.
    target = d.get("target_flavor", "?")
    primary_badges = " ".join(b for b in (
        confidence_score_badge(conf_score),
        maturity_badge(maturity),
        lift_badge(lift_tier, port),
        _long_shot_badge(d),
    ) if b)

    # Meta caption: missing SKU count + portability + comp summary
    meta_parts = []
    breakdown_or_skus = d.get("lift_breakdown") or d.get("missing_skus") or []
    n_missing = len(breakdown_or_skus)
    if n_missing:
        meta_parts.append(f"{n_missing} new SKU{'s' if n_missing != 1 else ''}")
    if port:
        meta_parts.append(f"{port} rollout")
    if comp_audit and comp_audit.get("verified_count", 0):
        meta_parts.append(f"{comp_audit['verified_count']} comp{'s' if comp_audit['verified_count'] != 1 else ''} verified")
    meta_caption = " · ".join(meta_parts)

    evidence_one_liner = ""
    if ev:
        evidence_one_liner = (
            f'<div style="font-size:0.85rem; color:#5a4f3c; margin: 0.2rem 0 0.5rem 0;">'
            f'{ev.get("competitive_mentions",0)} indie mentions · '
            f'{ev.get("recent_12mo_mentions",0)} in last 12 months · '
            f'Google Trends {ev.get("dma_trend",0):.0f}/100'
            f'</div>'
        )

    expand_body = ""
    if skus_chips:
        expand_body += (
            f'<div class="dish-meta-label">Missing SKUs</div>'
            f'<div>{skus_chips}</div>'
        )
    if d.get("dish_potential"):
        expand_body += (
            f'<div class="dish-meta-label">Dish potential</div>'
            f'<div class="dish-meta-value">{d.get("dish_potential","")}</div>'
        )
    if d.get("operational_lift"):
        expand_body += (
            f'<div class="dish-meta-label">Operational lift</div>'
            f'<div class="dish-meta-value" style="font-size:0.85rem;">{d.get("operational_lift","")}</div>'
        )
    if d.get("comp_context"):
        expand_body += (
            f'<div class="dish-meta-label">Comp restaurants</div>'
            f'<div class="dish-meta-value" style="font-size:0.85rem; color:#5a4f3c;">{d.get("comp_context","")}</div>'
        )
    expand_body += lto_proven_detail(lto)
    expand_body += comp_detail(comp_audit)

    border_style = "dashed" if d.get("long_shot") else "solid"
    html = (
        f'<div class="dish-card" style="border-left-color: {color}; border-style: {border_style}; background: #faf2ec;">'
        # Verdict tag — brand chip removed (now lives on vertical tab to left)
        f'<div style="display:flex; align-items:center; gap:0.4rem; margin-bottom:0.5rem;">'
        f'<span style="background:{MEANING_COLORS["mid"]}; color:white; padding:0.15rem 0.6rem; '
        f'border-radius:4px; font-size:0.7rem; font-weight:700; letter-spacing:0.06em;">GAP FILL</span>'
        f'</div>'
        # Lead with the target flavor as the dish name
        f'<div style="font-family: Georgia, serif; font-size: 1.35rem; font-weight: 700; '
        f'line-height: 1.25; margin: 0.3rem 0 0.25rem 0;">Add: {target}</div>'
        # Optional dish-potential subhead
        + (f'<div style="font-size: 0.93rem; color: #5a4f3c; line-height: 1.45; margin-bottom: 0.55rem;">'
           f'{d.get("dish_potential","")[:200]}</div>' if d.get("dish_potential") else "")
        # Primary badges
        + (f'<div style="display:flex; align-items:center; gap:0.35rem; flex-wrap:wrap; margin-bottom:0.4rem;">'
           f'{primary_badges}</div>' if primary_badges else "")
        + evidence_one_liner
        + (f'<div style="font-size:0.78rem; color:#7a6f5c; letter-spacing:0.01em; margin-bottom:0.6rem;">{meta_caption}</div>'
           if meta_caption else "")
        + (f'<details style="margin-top:0.4rem;"><summary style="cursor:pointer; font-size:0.82rem; color:{MEANING_COLORS["high"]}; font-weight:600; user-select:none;">Why we recommend this · methodology</summary>'
           f'<div style="margin-top:0.5rem;">{expand_body}</div>'
           f'</details>' if expand_body else "")
        + '</div>'
    )
    if hasattr(st, "html"): st.html(html)
    else: st.markdown(html, unsafe_allow_html=True)


def render_dish_card(d: dict):
    """Dispatch on `type` field — v5 outputs have type=ship_now|gap_fill;
    v4 outputs are pre-typed (treat as ship_now)."""
    if d.get("type") == "gap_fill":
        render_gap_fill_card(d)
    else:
        render_ship_now_card(d)


# ───────────────────────── sidebar ─────────────────────────

with st.sidebar:
    st.markdown("# 🌿 Taste Engine")

    # City picker above the fold — judges use this to flip between cities
    city_key = st.radio(
        "**Neighborhood**",
        options=list(CITIES.keys()),
        format_func=lambda k: f"{CITIES[k]['emoji']}  {CITIES[k]['label']}",
        index=2,  # default to Mission (the hero city) instead of WeHo
    )

    st.divider()

    counts = db_counts()
    t = counts["_totals"]
    st.caption("**Powered by Bright Data**")
    st.caption(f"• {t['reviews']:,} dated indie reviews")
    st.caption(f"• {t['restaurants']} restaurants · 3 metro markets · 24 months")
    st.caption(f"• {t['pantry_items']} brand SKUs · {t['flavor_mentions']:,} flavor mentions")

    st.divider()
    st.caption("*Neighborhood-aware culinary intelligence — turning local taste signals into operational menu strategy.*")


# ───────────────────────── header ─────────────────────────

spec = CITIES[city_key]
city_label = spec["label"]
city_emoji = spec["emoji"]
city_counts = counts.get(city_label, {})

col1, col2, col3 = st.columns([4, 1, 1])
with col1:
    st.markdown(f"# {city_emoji}  {city_label}")
    st.caption(f"Metro market `{spec['geo']}` · cross-source neighborhood-level signal · sourced via Bright Data")
with col2:
    st.metric("Restaurants", city_counts.get("restaurants", 0))
with col3:
    st.metric("Reviews", f"{city_counts.get('reviews', 0):,}")

# Three-pillar block — render only on Overview tab now. We define the HTML
# here as a helper that the Overview tab uses; other tabs get nothing.
PILLAR_HTML = (
    '<div style="display:flex; gap:0.6rem; margin-top:0.4rem; margin-bottom:0.6rem;">'
    f'<div style="flex:1; background:#f4ede0; border-left:6px solid {MEANING_COLORS["high"]}; padding:0.4rem 0.7rem;">'
    f'<div style="font-size:0.7rem; font-weight:700; letter-spacing:0.07em; color:{MEANING_COLORS["high"]};">PILLAR 1</div>'
    '<div style="font-size:0.92rem; font-weight:600; color:#2a2a2a;">Signal Fusion</div>'
    '<div style="font-size:0.74rem; color:#5a4f3c;">National Trends × local indie reviews, weighted honestly.</div>'
    '</div>'
    f'<div style="flex:1; background:#f4ede0; border-left:6px solid {MEANING_COLORS["mid"]}; padding:0.4rem 0.7rem;">'
    f'<div style="font-size:0.7rem; font-weight:700; letter-spacing:0.07em; color:{MEANING_COLORS["mid"]};">PILLAR 2</div>'
    '<div style="font-size:0.92rem; font-weight:600; color:#2a2a2a;">Trend Maturity</div>'
    '<div style="font-size:0.74rem; color:#5a4f3c;">Competitive vs leading pool spread → Rising / Established / Peak.</div>'
    '</div>'
    f'<div style="flex:1; background:#f4ede0; border-left:6px solid {MEANING_COLORS["low"]}; padding:0.4rem 0.7rem;">'
    f'<div style="font-size:0.7rem; font-weight:700; letter-spacing:0.07em; color:{MEANING_COLORS["low"]};">PILLAR 3</div>'
    '<div style="font-size:0.92rem; font-weight:600; color:#2a2a2a;">Innovation Feasibility</div>'
    '<div style="font-size:0.74rem; color:#5a4f3c;">Pantry lift tier + rollout portability + LTO history.</div>'
    '</div>'
    '</div>'
)

st.divider()

# ───────────────────────── tabs ─────────────────────────

tab_overview, tab_recs, tab_trends, tab_vs_llm, tab_evidence, tab_methodology = st.tabs([
    "Overview", "Recommendations", "Trends", "vs Naive LLM", "Evidence", "Methodology",
])
# Aliases — each old tab body now executes inside one of the host tabs.
# Order matters: blocks execute in source order, so Validation content
# appears above Velocity content inside the Evidence tab, etc.
tab_dishes = tab_recs
tab_validation = tab_evidence
tab_velocity = tab_evidence
tab_signals = tab_methodology
tab_pantry = tab_methodology
tab_gaps = tab_methodology
tab_compare = tab_methodology

# ── Overview ───────────────────────────────────────────────────────────────
with tab_overview:
    # Three-pillar framing — Overview ONLY (per design audit P1: stop repeating
    # this on every tab; reclaim ~200px above the fold for the other tabs).
    st.markdown(PILLAR_HTML, unsafe_allow_html=True)

    # Glossary — collapsible. First-time visitors get a quick translation for
    # the industry/internal terms we use throughout the dashboard.
    with st.expander("📖 Glossary — industry terms in plain language", expanded=False):
        st.markdown(
            f'<div style="font-size:0.86rem; color:#2A2825; line-height:1.65;">'
            f'<strong>Fast-casual</strong> · mid-tier restaurant chains like Chipotle, CAVA, Sweetgreen — between fast food and full service.<br>'
            f'<strong>LTO (limited-time offer)</strong> · a menu item a brand ships for a fixed window (weeks to months), then retires.<br>'
            f'<strong>SKU (stock unit)</strong> · any single inventory item a brand stocks. A pantry has many SKUs.<br>'
            f'<strong>Pantry</strong> · the brand\'s active ingredient inventory — what they can actually plate today.<br>'
            f'<strong>Competitive pool</strong> · lunch-open indie restaurants ($-$$). What your customer is choosing instead today.<br>'
            f'<strong>Leading pool</strong> · chef-driven indie restaurants (dinner-only or $$$+). What the food scene is popularizing for ~2 years out.<br>'
            f'<strong>Maturity stage</strong> · where a flavor sits in its lifecycle. Rising (chef-driven, early), Established (mainstream, safe to ship), Peak (saturated, fad-tail risk), Steady, Weak.<br>'
            f'<strong>Lift tier</strong> · what it takes to ship a new flavor. Low = seasoning remix; Medium = new prep workflow or shelf-stable SKU; High = refrigerated SKU or new cooking method.<br>'
            f'<strong>Ship now / Gap fill</strong> · ship-now uses current pantry; gap-fill names specific SKUs the brand would need to add.'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Hero callout — strongest single ACTIONABLE recommendation for this city.
    # Prefer gap_fill over ship_now when a flavor is already shipping nationally
    # (avoids the "you're recommending what the brand is already doing" weakness).
    # Mission: per Chipotle newsroom, Chicken Al Pastor relaunched nationally
    # Feb 10, 2026 — so a Mission-specific al pastor recommendation is no longer
    # actionable. Hero pivots to Mole gap_fill: +7 mentions Q1, none of the
    # chains can deliver from pantry, Californios verified as comp.
    HERO_OVERRIDE = {
        "mission": {"brand": "Chipotle", "type": "gap_fill", "target_flavor": "mole"},
        # weho and williamsburg fall back to the default highest-confidence pick
    }
    dishes_for_hero = load_dishes(city_key)
    override = HERO_OVERRIDE.get(city_key)
    hero = None
    if override:
        for d in dishes_for_hero:
            if (d.get("brand") == override["brand"]
                    and d.get("type") == override["type"]
                    and (d.get("target_flavor") == override.get("target_flavor")
                         or d.get("signal_term") == override.get("signal_term"))):
                hero = d
                break
    if hero is None:
        hero_candidates = [d for d in dishes_for_hero
                            if d.get("type") == "ship_now" and d.get("confidence_score")]
        if hero_candidates:
            hero = max(hero_candidates, key=lambda d: d.get("confidence_score") or 0)
    if hero:
        ev = hero.get("evidence_counts") or {}
        lto = hero.get("lto_proven")
        is_gap = hero.get("type") == "gap_fill"
        verdict_text = "GAP-FILL OPPORTUNITY" if is_gap else "TOP RECOMMENDATION"
        verdict_color = MEANING_COLORS["mid"] if is_gap else MEANING_COLORS["high"]
        # Card title: ship_now uses dish_name; gap_fill leads with the missing flavor
        title = hero.get("dish_name") or f'Add {hero.get("target_flavor","?")} to {hero.get("brand")}'
        subtitle = (hero.get("tagline") if not is_gap
                    else (hero.get("dish_potential","")[:240]))

        st.markdown(
            f'<div style="background: linear-gradient(135deg, #f4ede0 0%, #ede5d0 100%); '
            f'border: 1px solid #d4c89a; border-radius: 6px; padding: 1rem 1.3rem; '
            f'margin: 0.5rem 0 1.2rem 0;">'
            f'<div style="display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; margin-bottom: 0.4rem;">'
            f'<span style="background:{verdict_color}; color:white; padding:0.15rem 0.7rem; '
            f'border-radius:4px; font-size:0.72rem; font-weight:700; letter-spacing:0.06em;">{verdict_text}</span>'
            f'{confidence_score_badge(hero.get("confidence_score"))}'
            f'{maturity_badge(hero.get("maturity_stage"))}'
            f'{lift_badge(hero.get("lift_tier"), hero.get("rollout_portability"))}'
            f'{lto_proven_badge(lto)}'
            f'</div>'
            f'<div style="font-size: 1.15rem; font-weight: 700; color: #2A2825; margin: 0.3rem 0 0.2rem 0;">'
            f'{title}'
            f'</div>'
            f'<div style="font-size: 0.92rem; color: #5a4f3c; line-height: 1.4;">{subtitle}</div>'
            + (f'<div style="font-size: 0.82rem; color: #5a4f3c; margin-top: 0.5rem;">'
               f'Evidence: <strong>{ev.get("competitive_mentions",0)}</strong> indie competitive mentions · '
               f'<strong>{ev.get("recent_12mo_mentions",0)}</strong> in last 12 months · '
               f'Google Trends <strong>{ev.get("dma_trend",0):.0f}/100</strong>'
               f'</div>' if ev else "")
            + (f'<div style="font-size: 0.82rem; color: #2c3e50; margin-top: 0.35rem;">'
               f'Proven elsewhere: {lto["item_name"]} shipped {", ".join(str(y) for y in lto.get("years",[]))}.'
               f'</div>' if lto else "")
            + '</div>',
            unsafe_allow_html=True,
        )

    # ── DETERMINISTIC OVERVIEW NARRATIVE ───────────────────────────────────
    # All content below is generated from current data sources (quarterly_brief,
    # confidence_scores, v6 cards, pantry_fit). No LLM-generated prose that
    # bypassed our constraint stack. The previous exec_overviews.json-driven
    # block was a subagent-written prose surface from before cuisine coherence,
    # brand cuisine identity, and 0-100 normalization shipped; it produced
    # cuisine-incoherent recommendations (e.g. "pesto + miso LTO bowl") and is
    # now retired in favor of deterministic content.
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "scripts"))

    # 1. Headline narrative — top mover this quarter + maturity transition
    brief_path = DATA / "quarterly_brief.json"
    qb = {}
    if brief_path.exists():
        qb_all = json.loads(brief_path.read_text())
        qb = qb_all.get(city_label, {})

    movers_up = qb.get("movers_up", [])
    transitions = qb.get("transitions", [])
    forecasts = qb.get("forecasts", [])

    headline_parts = []
    if movers_up:
        m = movers_up[0]
        headline_parts.append(
            f'<strong>{m["flavor"]}</strong> is the quarter\'s top mover '
            f'(+{m["delta"]} mentions, {m["prior_total"]} → {m["ref_total"]})'
        )
    if transitions:
        t = transitions[0]
        headline_parts.append(
            f'<strong>{t["flavor"]}</strong> crossed '
            f'<em>{t["prior_stage"]}</em> → <em>{t["ref_stage"]}</em>'
        )
    if forecasts:
        f = forecasts[0]
        arrow = "↑" if f["direction"] == "rising" else "↓" if f["direction"] == "falling" else "→"
        headline_parts.append(
            f'<strong>{f["flavor"]}</strong> forecast Q2 ~{f["forecast_q2_2026"]:.0f} '
            f'(±{f["forecast_band"]:.0f}) {arrow}'
        )

    if headline_parts:
        st.markdown(
            f'<div style="background:#f0e9d6; border-left:4px solid {MEANING_COLORS["high"]}; '
            f'padding:0.85rem 1.1rem; border-radius:4px; margin:0.6rem 0 1.4rem 0;">'
            f'<div style="text-transform:uppercase; font-size:0.65rem; letter-spacing:0.1em; '
            f'color:#6b6253; margin-bottom:0.35rem;">This quarter · {city_label}</div>'
            f'<div style="font-size:1rem; line-height:1.55; color:#2A2825;">{" · ".join(headline_parts)}.</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # 2. Two-column body: top signals (left) + pantry alignment (right)
    col_l, col_r = st.columns([3, 2])

    with col_l:
        st.markdown("#### Top signals this neighborhood")
        st.caption("Ranked by composite score (Google Trends × indie review density). Verbal strength label translates the 0-100 score for non-statisticians.")
        signals = load_signal_ranking(city_key, spec["geo"], limit=8)
        for s in signals:
            score_100 = int((s["signal_score"] or 0) * 100)
            label, color = strength_label(score_100)
            bg = STRENGTH_BG.get(label, "#fffaf2")
            st.markdown(
                f'<div style="display:flex; align-items:center; justify-content:space-between; '
                f'padding:0.4rem 0.7rem; margin:0.25rem 0; background:{bg}; '
                f'border-radius:4px; border-left:4px solid {color};">'
                f'<div><strong>{s["term"]}</strong>'
                f'<span style="font-size:0.78rem; color:#7a6f5c; margin-left:0.5rem;">'
                f'{s["mentions"]} mentions</span></div>'
                f'<div style="font-family:monospace; font-size:0.85rem; color:{color}; font-weight:600;">'
                f'{score_100}/100 <span style="font-weight:400; opacity:0.75;">{label}</span></div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    with col_r:
        st.markdown("#### Pantry alignment")
        st.caption("Of the top 15 flavors in this neighborhood, how many can each brand deliver from current pantry?")
        # Derive grades deterministically from pantry_fit + top-15 ranking
        pantry_fit = load_pantry_fit()
        top15 = [s["term"] for s in load_signal_ranking(city_key, spec["geo"], limit=15)]
        for brand in ["Chipotle", "CAVA", "Sweetgreen"]:
            bfit = pantry_fit.get(brand, {})
            deliverable_count = sum(
                1 for term in top15 if bfit.get(term, {}).get("deliverable")
            )
            ratio = deliverable_count / max(len(top15), 1)
            if ratio >= 0.4:
                grade_label, g_color = "Strong fit", MEANING_COLORS["high"]
            elif ratio >= 0.25:
                grade_label, g_color = "Good fit", MEANING_COLORS["high"]
            elif ratio >= 0.15:
                grade_label, g_color = "Partial fit", MEANING_COLORS["mid"]
            elif ratio >= 0.07:
                grade_label, g_color = "Limited fit", MEANING_COLORS["mid"]
            else:
                grade_label, g_color = "Narrow fit", MEANING_COLORS["low"]
            bc = BRAND_COLORS.get(brand, "#888")
            st.markdown(
                f'<div style="background:#fffaf2; padding:0.75rem 0.95rem; '
                f'margin:0.45rem 0; border-radius:6px; border:1px solid #d6cab2; '
                f'border-left:4px solid {bc};">'
                f'<div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:0.35rem;">'
                f'<span style="font-weight:600; color:{bc};">{brand}</span>'
                f'<span style="background:{g_color}; color:white; padding:0.15rem 0.65rem; '
                f'border-radius:999px; font-size:0.78rem; font-weight:600;">{grade_label}</span>'
                f'</div>'
                f'<div style="font-size:0.85rem; color:#5a4f3c; line-height:1.5;">'
                f'<strong>{deliverable_count} of {len(top15)}</strong> top signals deliverable '
                f'({int(ratio*100)}%)'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ── Maturity Landscape — log scatter, Weak filtered, top-6 labels staggered ─
    st.divider()
    st.subheader(f"Maturity landscape · {city_label}")
    st.caption(
        "Indie restaurant mention counts by pool. **X = competitive (lunch indies)** — what your customer is "
        "choosing today. **Y = leading (chef-driven indies)** — what the chef scene is popularizing. "
        "Below-noise Weak flavors hidden. Top 6 by total mentions are labeled — hover the dots for the rest."
    )

    try:
        import altair as alt
        import pandas as pd
        conf_data = json.loads((DATA / "confidence_scores.json").read_text())
        rows = []
        for flavor, brands in conf_data.get(city_label, {}).items():
            ev = brands.get("Chipotle", {}).get("evidence", {})
            comp = ev.get("competitive_mentions", 0) or 0
            lead = ev.get("leading_mentions", 0) or 0
            total = comp + lead
            stage = brands.get("Chipotle", {}).get("maturity_stage", "Weak")
            # Drop Weak stage AND tiny totals — these create the bottom-left
            # cluster that crushes legibility
            if stage == "Weak" or total < 5:
                continue
            rows.append({"flavor": flavor,
                         "competitive": max(comp, 1),
                         "leading":     max(lead, 1),
                         "comp_raw":    comp,
                         "lead_raw":    lead,
                         "total":       total,
                         "stage":       stage})
        if rows:
            df = pd.DataFrame(rows)
            STAGE_ORDER = ["Established", "Rising", "Peak", "Steady"]
            stage_colors = {
                "Established": MEANING_COLORS["high"],
                "Rising":      MEANING_COLORS["mid"],
                "Peak":        MEANING_COLORS["low"],
                "Steady":      "#9C7F4A",  # tan
            }

            # Label only top 6 by total mentions. Stagger label dy by index
            # so consecutive labels don't sit on top of each other.
            df = df.sort_values("total", ascending=False).reset_index(drop=True)
            df["should_label"] = df.index < 6
            # Alternate label offsets: even-index up-right, odd-index down-right
            df["label_dx"] = 12
            df["label_dy"] = df.index.map(lambda i: -10 if i % 2 == 0 else 14)

            # Stable position offsets per row — staggered so labels don't overlap.
            # Even-index rows label above-right; odd-index rows label below-right.
            df["label_dx"] = 14
            df["label_dy"] = [(-12 if i % 2 == 0 else 16) for i in range(len(df))]

            base = alt.Chart(df).encode(
                x=alt.X("competitive:Q",
                        scale=alt.Scale(type="log", domain=[1, 200]),
                        axis=alt.Axis(title="Competitive pool mentions (lunch indies)",
                                      values=[1, 3, 10, 30, 100, 200],
                                      labelFontSize=11)),
                y=alt.Y("leading:Q",
                        scale=alt.Scale(type="log", domain=[1, 200]),
                        axis=alt.Axis(title="Leading pool mentions (chef-driven indies)",
                                      values=[1, 3, 10, 30, 100, 200],
                                      labelFontSize=11)),
                color=alt.Color("stage:N",
                                scale=alt.Scale(domain=STAGE_ORDER,
                                                range=[stage_colors[s] for s in STAGE_ORDER]),
                                legend=alt.Legend(title="Maturity stage",
                                                  orient="bottom",
                                                  direction="horizontal",
                                                  titleFontSize=11,
                                                  labelFontSize=11,
                                                  symbolSize=120,
                                                  padding=10,
                                                  offset=22)),
                tooltip=[
                    alt.Tooltip("flavor:N", title="Flavor"),
                    alt.Tooltip("stage:N", title="Stage"),
                    alt.Tooltip("comp_raw:Q", title="Competitive mentions"),
                    alt.Tooltip("lead_raw:Q", title="Leading mentions"),
                    alt.Tooltip("total:Q", title="Total"),
                ],
            )
            dots = base.mark_circle(
                opacity=0.82, stroke="#fffaf2", strokeWidth=1.5,
            ).encode(
                size=alt.Size("total:Q",
                              scale=alt.Scale(type="sqrt", range=[120, 700]),
                              legend=None),
            )
            # Two text layers — one for even-index rows (labels above), one for
            # odd-index rows (labels below). Each uses a constant dx/dy.
            label_above = base.transform_filter(
                "datum.should_label && datum.label_dy < 0"
            ).mark_text(
                align="left", fontSize=11, fontWeight=600, color="#2a2825",
                dx=14, dy=-12,
            ).encode(text="flavor:N")
            label_below = base.transform_filter(
                "datum.should_label && datum.label_dy > 0"
            ).mark_text(
                align="left", fontSize=11, fontWeight=600, color="#2a2825",
                dx=14, dy=16,
            ).encode(text="flavor:N")

            chart = (dots + label_above + label_below).properties(
                height=440,
                padding={"top": 18, "right": 30, "bottom": 70, "left": 20},
            ).configure_view(strokeWidth=0, fill="#fffaf2"
            ).configure_axis(grid=True, gridColor="#e8e0cc", gridOpacity=0.6)
            st.altair_chart(chart, use_container_width=True)
            st.caption("Source: Bright Data Google Maps Reviews Dataset · dual-pool indies only · chains excluded. Both axes log-scaled. Dot size = total mentions.")

            # Strategic call summary — auto-generated from the data
            est_top = df[df["stage"] == "Established"].head(3)["flavor"].tolist()
            rising_top = df[df["stage"] == "Rising"].head(3)["flavor"].tolist()
            peak_top = df[df["stage"] == "Peak"].head(3)["flavor"].tolist()
            strategy_lines = []
            if est_top:
                strategy_lines.append(
                    f"<strong>Ship-now zone (Established):</strong> "
                    f"{', '.join(est_top)} — mainstream demand, low-risk shipping window."
                )
            if rising_top:
                strategy_lines.append(
                    f"<strong>Watch zone (Rising):</strong> "
                    f"{', '.join(rising_top)} — chef-driven, too early for a chain to claim authentically."
                )
            if peak_top:
                strategy_lines.append(
                    f"<strong>Caution (Peak):</strong> "
                    f"{', '.join(peak_top)} — saturated; fad-tail risk if you're late."
                )
            if strategy_lines:
                st.markdown(
                    '<div style="background:#fffaf2; border-left:3px solid '
                    + MEANING_COLORS["high"]
                    + '; padding:0.7rem 1rem; margin-top:0.6rem; font-size:0.88rem; '
                    + 'color:#2A2825; line-height:1.55;">'
                    + "<br>".join(strategy_lines)
                    + '</div>',
                    unsafe_allow_html=True,
                )
    except Exception as e:
        st.caption(f"Maturity lanes unavailable: {e}")


# ── Evidence host tab — header ────────────────────────────────────────────
with tab_evidence:
    st.markdown(
        f'<div style="background:#f4ede0; border-left:6px solid {MEANING_COLORS["high"]}; '
        f'padding:0.6rem 1rem; margin:0.4rem 0 1rem 0;">'
        f'<div style="font-size:0.7rem; font-weight:700; letter-spacing:0.07em; color:{MEANING_COLORS["high"]};">PILLAR 1 — SIGNAL FUSION</div>'
        f'<div style="font-size:0.85rem; color:#5a4f3c;">Where national Trends and local indie review data agree, and where they disagree honestly.</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

# ── Validation ─────────────────────────────────────────────────────────────
with tab_validation:
    import altair as alt
    import pandas as pd

    st.subheader("Validation: does the system actually catch real trends?")

    vpath = DATA / "validation_al_pastor.json"
    if not vpath.exists():
        st.warning("Run `python scripts/fetch_validation_history.py` to generate validation data.")
    else:
        v = json.loads(vpath.read_text())

        st.markdown(
            f'<div style="font-family: Georgia, serif; font-size: 1.15rem; '
            f'line-height: 1.5; color: #2A2825; background: #f4ede0; '
            f'border-left: 3px solid #3D5A40; padding: 0.7rem 1rem; margin: 0.4rem 0 1rem 0;">'
            f'{v["story_headline"]}'
            f'</div>',
            unsafe_allow_html=True,
        )

        # National trend line
        nat_df = pd.DataFrame(v["national_trend_monthly"])
        nat_df["date"] = pd.to_datetime(nat_df["date"])
        # Quarter-aggregated indie data, all cities
        indie_rows = []
        for city, rows in v["indie_review_quarterly"].items():
            for r in rows:
                y, q = r["quarter"].split("-Q")
                qmonth = {"1": "01", "2": "04", "3": "07", "4": "10"}[q]
                indie_rows.append({
                    "city": city,
                    "date": pd.to_datetime(f"{y}-{qmonth}-15"),
                    "total": r["total"],
                })
        indie_df = pd.DataFrame(indie_rows)
        # LTO event markers
        evt_df = pd.DataFrame([{"date": pd.to_datetime(e["date"]),
                                "label": f'{e["brand"]} {e["item"]} ({e["year"]})'}
                              for e in v["chain_lto_events"]])

        # Single overlay chart — national Trends area (left axis) + indie
        # city lines (right axis). Both share the x-axis so quarter ticks
        # align across both datasets.
        x_axis = alt.Axis(
            format="%b '%y",
            tickCount={"interval": "month", "step": 3},
            labelAngle=-30, labelFontSize=11,
            grid=True, gridColor="#e8e0cc", gridOpacity=0.6,
        )

        nat_layer = alt.Chart(nat_df).mark_area(
            color="#5A8B5A", opacity=0.15,
            line={"color": "#3D5A40", "strokeWidth": 1.5},
            interpolate="monotone",
        ).encode(
            x=alt.X("date:T", title=None, axis=x_axis),
            y=alt.Y("interest:Q",
                    title="Google Trends (0–100)",
                    axis=alt.Axis(titleColor="#3D5A40", orient="left")),
            tooltip=[alt.Tooltip("date:T", format="%b %Y"), alt.Tooltip("interest:Q", title="Trend")],
        )

        # LTO event vertical rules — short rotated labels
        evt_df["short_label"] = evt_df.apply(
            lambda r: "Chipotle relaunch"
            if str(r["label"]).endswith("(2026)")
            else ("Chipotle return" if str(r["label"]).endswith("(2024)")
                  else "Chipotle launch"),
            axis=1,
        )
        lto_rules = alt.Chart(evt_df).mark_rule(
            color="#2c3e50", strokeDash=[5, 4], strokeWidth=1.5
        ).encode(x="date:T", tooltip=["label:N"])
        lto_text = alt.Chart(evt_df).mark_text(
            align="left", dx=5, dy=12, fontSize=10, fontWeight=600,
            color="#2c3e50", angle=270, baseline="top",
        ).encode(x="date:T", y=alt.value(8), text="short_label:N")

        indie_layer = (
            alt.Chart(indie_df)
            .mark_line(
                interpolate="monotone", strokeWidth=2.2,
                point=alt.OverlayMarkDef(size=55, filled=True, opacity=0.9),
            )
            .encode(
                x=alt.X("date:T", title=None, axis=x_axis),
                y=alt.Y("total:Q",
                        title="Indie quarterly mentions",
                        axis=alt.Axis(titleColor="#8E4A3C", orient="right"),
                        scale=alt.Scale(zero=True)),
                color=alt.Color("city:N",
                                scale=alt.Scale(
                                    domain=list(v["indie_review_quarterly"].keys()),
                                    range=["#A33A1F", "#C46B45", "#5A8B5A"],
                                ),
                                legend=alt.Legend(title="City", orient="top-right")),
                tooltip=["city:N", alt.Tooltip("date:T", format="%b %Y"), "total:Q"],
            )
        )

        combined = (
            alt.layer(nat_layer, lto_rules, lto_text, indie_layer)
            .resolve_scale(y="independent")
            .properties(height=380,
                        padding={"left": 5, "right": 5, "top": 25, "bottom": 5})
            .configure_view(strokeWidth=0, fill="#fffaf2")
            .configure_axis(grid=True, gridColor="#e8e0cc", gridOpacity=0.6)
        )

        st.altair_chart(combined, use_container_width=True)
        st.caption(
            "**Green area (left axis):** National Google Trends for al pastor (US, monthly 2019–2026). "
            "**Colored lines (right axis):** Quarterly indie al pastor mentions by city — "
            "Bright Data Google Maps Reviews Dataset, dual-pool indies only (chains excluded). "
            "Dashed verticals mark Chipotle Chicken Al Pastor launch / return / relaunch (newsroom-verified)."
        )

        with st.expander("Full validation story"):
            st.markdown(v["story_full"])

        with st.expander("Honest limit"):
            st.markdown(v["honest_limit"])

        with st.expander("Verified LTO sources"):
            for e in v["chain_lto_events"]:
                if e.get("source"):
                    st.markdown(f"- **{e['year']} · {e['brand']} {e['item']}** — [{e['source']}]({e['source']})")


# ── Velocity ───────────────────────────────────────────────────────────────
with tab_velocity:
    import altair as alt
    import pandas as pd

    st.subheader(f"Flavor velocity over time · {city_label}")
    st.caption(
        "Real chronological data from Google Maps reviews, last 12-24 months. "
        "Each chart bins by month or quarter — no smoothing, no algorithmic curation."
    )

    # ── Chart 1: top-flavors quarterly trend ──────────────────────────────
    # Highlight top 3 only; gray the rest. Same data, decodable in 3 seconds.
    st.markdown("#### Quarterly flavor velocity — top 3 highlighted")
    st.caption("Top 3 flavors by total mentions shown in color; the next 5 are grayed for context. Hover to inspect any line.")
    top_flavors = top_flavors_in_city(city_key, n=8)
    if not top_flavors:
        st.warning("No flavor mentions yet for this city.")
    else:
        df = monthly_mentions(city_key, flavors=top_flavors)
        if df.empty:
            st.warning("No time-binned data available.")
        else:
            top3 = top_flavors[:3]
            df["highlight"] = df["flavor"].isin(top3)

            # Bottom layer — grayed-out background flavors
            background = (
                alt.Chart(df[~df["highlight"]])
                .mark_line(strokeWidth=1.2, opacity=0.35, interpolate="monotone")
                .encode(
                    x=alt.X("quarter:O", title="Quarter",
                            sort=sorted(df["quarter"].unique().tolist())),
                    y=alt.Y("mentions:Q", title="Review mentions"),
                    detail="flavor:N",
                    color=alt.value("#a09683"),
                    tooltip=["flavor", "quarter", "mentions"],
                )
            )

            # Top layer — highlighted top 3
            foreground = (
                alt.Chart(df[df["highlight"]])
                .mark_line(point=alt.OverlayMarkDef(size=55, filled=True),
                           strokeWidth=2.5, interpolate="monotone")
                .encode(
                    x=alt.X("quarter:O", sort=sorted(df["quarter"].unique().tolist())),
                    y="mentions:Q",
                    color=alt.Color("flavor:N",
                                    scale=alt.Scale(domain=top3,
                                                    range=[MEANING_COLORS["high"],
                                                           MEANING_COLORS["mid"],
                                                           MEANING_COLORS["low"]]),
                                    legend=alt.Legend(title="Top 3", orient="right")),
                    tooltip=["flavor", "quarter", "mentions"],
                )
            )

            chart = (background + foreground).properties(height=340)
            st.altair_chart(chart, use_container_width=True)
            st.caption(f"Highlighted: {', '.join(top3)}. Grayed background: {', '.join(top_flavors[3:])}.")

    st.divider()

    # ── Chart 2: quarterly heatmap ─────────────────────────────────────────
    st.markdown("#### Top 15 flavors × quarter — mention heatmap")
    st.caption("Color intensity = number of mentions that quarter. White cells = no mentions.")
    qdf = quarterly_mentions(city_key, top_n=15)
    if qdf.empty:
        st.warning("Not enough data for a quarterly heatmap yet.")
    else:
        # Sort flavors by total mention count
        totals = qdf.groupby("flavor")["mentions"].sum().sort_values(ascending=False)
        qdf["flavor"] = pd.Categorical(qdf["flavor"], categories=totals.index.tolist(), ordered=True)
        heat = (
            alt.Chart(qdf)
            .mark_rect(stroke="#fffaf2", strokeWidth=2)
            .encode(
                x=alt.X("quarter:O", title="Quarter", sort="ascending"),
                y=alt.Y("flavor:N", title=None, sort=totals.index.tolist()),
                color=alt.Color("mentions:Q",
                                scale=alt.Scale(scheme="goldorange"),
                                legend=alt.Legend(title="Mentions")),
                tooltip=["flavor", "quarter", "mentions"],
            )
            .properties(height=420)
            .configure_view(strokeWidth=0)
        )
        st.altair_chart(heat, use_container_width=True)

    st.divider()

    # ── Chart 3: single-flavor deep dive across cities ─────────────────────
    st.markdown("#### Single-flavor cross-city trajectory")
    st.caption("Pick a flavor to see how it's trending in each neighborhood.")

    all_flavors_options = (
        top_flavors_in_city("weho", 25)
        + top_flavors_in_city("williamsburg", 25)
        + top_flavors_in_city("mission", 25)
    )
    all_flavors_options = sorted(set(all_flavors_options))
    default_flavor = "jerk" if "jerk" in all_flavors_options else (all_flavors_options[0] if all_flavors_options else "")
    chosen = st.selectbox(
        "Flavor",
        all_flavors_options,
        index=all_flavors_options.index(default_flavor) if default_flavor in all_flavors_options else 0,
    )
    if chosen:
        cdf = flavor_by_city(chosen)
        if cdf.empty:
            st.warning(f"No mentions of '{chosen}' yet.")
        else:
            city_color_map = {
                "West Hollywood":  "#A33A1F",   # brick
                "Williamsburg":    "#3D5A40",   # olive
                "Mission District":"#C46B45",   # terracotta
            }
            q_order = sorted(cdf["quarter"].unique().tolist())
            chart = (
                alt.Chart(cdf)
                .mark_line(point=alt.OverlayMarkDef(size=60, filled=True),
                           interpolate="monotone")
                .encode(
                    x=alt.X("quarter:O", title="Quarter", sort=q_order),
                    y=alt.Y("mentions:Q", title="Review mentions"),
                    color=alt.Color(
                        "city:N",
                        scale=alt.Scale(
                            domain=list(city_color_map.keys()),
                            range=list(city_color_map.values()),
                        ),
                        legend=alt.Legend(title="City", orient="top"),
                    ),
                    tooltip=["city", "quarter", "mentions"],
                )
                .properties(height=340, title=f"\"{chosen}\" — quarterly mentions by city")
                .configure_axis(grid=True, gridColor="#e8e0cc", gridOpacity=0.5)
                .configure_view(strokeWidth=0)
                .configure_title(fontSize=14, color="#2A2825", anchor="start")
            )
            st.altair_chart(chart, use_container_width=True)

    st.divider()

    # ── Chart 4: competitive vs leading pool divergence (trend maturity) ──
    st.markdown(f"#### Competitive vs leading pool — `{chosen}` in {city_label}")
    st.caption(
        "**Competitive pool** = indie lunch competitors ($-$$, opens by 1pm).  \n"
        "**Leading pool** = indie chef-driven ($$$ or dinner-only).  \n"
        "If the *leading* line is above and rising while competitive is flat, the flavor is "
        "still chef-driven and hasn't crossed into mainstream lunch — a *rising* trend.  \n"
        "If *competitive* dominates, the flavor is *established* and the chef-driven scene "
        "may have moved on."
    )
    if chosen:
        cdf = monthly_mentions(city_key, flavors=[chosen], pool="competitive")
        ldf = monthly_mentions(city_key, flavors=[chosen], pool="leading")
        if cdf.empty and ldf.empty:
            st.warning(f"No pool-tagged mentions of '{chosen}' in {city_label}.")
        else:
            if not cdf.empty:
                cdf = cdf.assign(pool="Competitive (indie lunch)")
            if not ldf.empty:
                ldf = ldf.assign(pool="Leading (chef-driven)")
            combined = pd.concat([cdf, ldf], ignore_index=True)
            q_order = sorted(combined["quarter"].unique().tolist())
            chart = (
                alt.Chart(combined)
                .mark_line(point=alt.OverlayMarkDef(size=60, filled=True),
                           interpolate="monotone")
                .encode(
                    x=alt.X("quarter:O", title="Quarter", sort=q_order),
                    y=alt.Y("mentions:Q", title="Review mentions"),
                    color=alt.Color(
                        "pool:N",
                        scale=alt.Scale(
                            domain=["Competitive (indie lunch)", "Leading (chef-driven)"],
                            range=["#3D5A40", "#C46B45"],
                        ),
                        legend=alt.Legend(title="Pool", orient="top"),
                    ),
                    tooltip=["pool", "quarter", "mentions"],
                )
                .properties(height=340, title=f"\"{chosen}\" — pool divergence by quarter in {city_label}")
                .configure_axis(grid=True, gridColor="#e8e0cc", gridOpacity=0.5)
                .configure_view(strokeWidth=0)
                .configure_title(fontSize=14, color="#2A2825", anchor="start")
            )
            st.altair_chart(chart, use_container_width=True)

# ── Methodology host tab — header ─────────────────────────────────────────
with tab_methodology:
    st.markdown(
        f'<div style="background:#f4ede0; border-left:6px solid {MEANING_COLORS["low"]}; '
        f'padding:0.6rem 1rem; margin:0.4rem 0 1rem 0;">'
        f'<div style="font-size:0.7rem; font-weight:700; letter-spacing:0.07em; color:{MEANING_COLORS["low"]};">PILLAR 3 — INNOVATION FEASIBILITY</div>'
        f'<div style="font-size:0.85rem; color:#5a4f3c;">Per-flavor signal rankings, brand pantry inventory, deliverability gaps, cross-city comparisons.</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

# ── Signals ────────────────────────────────────────────────────────────────
with tab_signals:
    st.subheader("Cross-source signal ranking — dual pool")
    st.caption(
        "**Competitive** pool = what your customer chooses INSTEAD for lunch today (indie $-$$, lunch-open).  \n"
        "**Leading** pool = what the chef-driven scene is popularizing for ~2 years out (indie dinner / $$$+).  \n"
        "Chains excluded from both. **Strength** label translates the raw score into a non-statistician read.  \n"
        "**Trajectory**: *Rising* = chef-driven leads, mainstream hasn't caught up · *Established* = mainstream leads, scene moved on · *Peak* = entrenched everywhere."
    )

    # Strength legend — all cutoffs now on the 0-100 scale
    legend = " · ".join(
        f'<span style="background:{color}; color:white; padding:0.1rem 0.5rem; '
        f'border-radius:999px; font-size:0.72rem;">{label}</span>'
        f' <span style="color:#6b6253; font-size:0.72rem;">≥{cutoff}</span>'
        for cutoff, label, color in STRENGTH_LABELS
    )
    st.markdown(f'<div style="margin: 0.5rem 0 1rem 0;">{legend}</div>',
                unsafe_allow_html=True)
    dual = load_signal_ranking_dual(city_key, spec["geo"], limit=20)
    if dual:
        import pandas as pd
        df_rows = []
        for s in dual:
            comp_score = s["competitive_score"] or 0
            lead_score = s["leading_score"] or 0
            best = max(comp_score, lead_score)
            if comp_score >= 0.3 and lead_score >= 0.3:
                traj = "Peak"
            elif lead_score - comp_score >= 0.10:
                traj = "Rising"
            elif comp_score - lead_score >= 0.10:
                traj = "Established"
            elif comp_score >= 0.25 or lead_score >= 0.25:
                traj = "Steady"
            else:
                traj = "Weak"
            strength_text, _ = strength_label(best)
            df_rows.append({
                "Flavor": s["term"],
                "Score (0-100)":  int(round(best * 100)),
                "Strength":       strength_text,
                "Trajectory":     traj,
                "Google Trends":  round(s["trend"] or 0, 1),
            })
        df = pd.DataFrame(df_rows)
        st.dataframe(
            df, use_container_width=True, hide_index=True,
            column_config={
                "Score (0-100)": st.column_config.ProgressColumn(
                    "Score",
                    help="Composite signal score — max of competitive and leading pool. 0-100.",
                    format="%d", min_value=0, max_value=100,
                ),
                "Strength": st.column_config.TextColumn(
                    "Strength",
                    help="Verbal read of the score: Very strong (≥75) · Strong (≥55) · Notable (≥35) · Emerging (≥20) · Background.",
                ),
                "Trajectory": st.column_config.TextColumn(
                    "Trajectory",
                    help="Lifecycle stage from competitive vs leading pool split. Rising (chef-driven, premature for chains) · Established (mainstream, ship-now zone) · Peak (saturated, fad-tail risk) · Steady · Weak.",
                ),
                "Google Trends": st.column_config.NumberColumn(
                    format="%.1f",
                    help="Google Trends 12-month average at the metro-market level (0-100). What people are searching for nationally.",
                ),
            },
        )
        st.caption("Sorted by max(competitive, leading) pool score. Use the Trajectory column to read the strategic call (ship-now vs watch vs caution).")

# ── Pantry ─────────────────────────────────────────────────────────────────
with tab_pantry:
    st.subheader("Brand pantries (live, available SKUs)")
    brand_choice = st.selectbox("Brand", list(BRAND_COLORS.keys()))
    st.markdown(brand_banner(brand_choice), unsafe_allow_html=True)

    pantry = load_pantry(brand_choice)
    existing = load_existing_dishes(brand_choice)
    fit = load_pantry_fit()
    defs = load_flavor_definitions()

    # ── Flavor deliverability grid ──
    if fit and brand_choice in fit:
        st.markdown("#### Flavor deliverability")
        st.caption(
            "Which trending flavors can this pantry honestly express? Match terms must appear "
            "as whole words in the SKU list (parentheticals stripped, so 'thyme' bundled inside "
            "carnitas doesn't count)."
        )
        brand_fit = fit[brand_choice]
        deliverable = sorted([f for f, v in brand_fit.items() if v["deliverable"]])
        non_deliverable = sorted([f for f, v in brand_fit.items() if not v["deliverable"]])

        col_d, col_g = st.columns(2)
        with col_d:
            st.markdown(f"**✓ Delivers ({len(deliverable)})**")
            for f in deliverable:
                matched = ", ".join(brand_fit[f]["matched"])
                st.markdown(
                    f'<div style="padding: 0.25rem 0; font-size: 0.88rem;">'
                    f'<strong>{f}</strong> '
                    f'<span style="color: #6b6253; font-size: 0.78rem;">via {matched}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        with col_g:
            st.markdown(f"**✗ Cannot deliver ({len(non_deliverable)})**")
            with st.container(height=400):
                for f in non_deliverable:
                    missing = ", ".join(brand_fit[f]["missing"])
                    st.markdown(
                        f'<div style="padding: 0.2rem 0; font-size: 0.82rem; color: #8a7c63;">'
                        f'<strong>{f}</strong> '
                        f'<span style="font-size: 0.72rem;">missing: {missing}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

        st.divider()

    if existing:
        with st.expander(f"📋 {len(existing)} existing dishes on menu (do-not-propose anchors)", expanded=False):
            cols = st.columns(2)
            for i, d in enumerate(existing):
                cols[i % 2].caption(f"• {d}")

    # Group by category
    by_cat: dict[str, list[dict]] = {}
    for p in pantry:
        by_cat.setdefault(p["category"], []).append(p)

    for cat, items in sorted(by_cat.items()):
        unavail = sum(1 for i in items if i["available"] == 0)
        label = cat.replace("sweetgreen_byo_", "").replace("_", " ").title()
        suffix = f" · {unavail} unavailable" if unavail else ""
        st.markdown(f"##### {label} ({len(items)}{suffix})")
        cols = st.columns(3)
        for i, item in enumerate(items):
            avail = item["available"] == 1
            color = "#5a4f3c" if avail else "#a04030"
            strikethrough = "text-decoration: line-through;" if not avail else ""
            note = " ⚠ unavailable" if not avail else ""
            cols[i % 3].markdown(
                f'<div style="color: {color}; {strikethrough} font-size: 0.85rem; padding: 0.15rem 0;">'
                f'• {item["item"]}{note}</div>',
                unsafe_allow_html=True,
            )

# ── Dishes ─────────────────────────────────────────────────────────────────
with tab_dishes:
    # Tiny breadcrumb — each pillar label uses its own pillar color to match
    # the PILLAR_HTML on the Overview tab (high=slate, mid=tan, low=rust).
    st.markdown(
        f'<div style="font-size:0.72rem; letter-spacing:0.05em; margin-bottom:0.4rem;">'
        f'<span style="color:{MEANING_COLORS["mid"]}; font-weight:700;">PILLAR 2</span>'
        f'<span style="color:{MEANING_COLORS["mid"]}"> — TREND MATURITY</span>'
        f'<span style="color:#a09683;"> · </span>'
        f'<span style="color:{MEANING_COLORS["low"]}; font-weight:700;">PILLAR 3</span>'
        f'<span style="color:{MEANING_COLORS["low"]}"> — INNOVATION FEASIBILITY</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.subheader(f"Recommendations · {city_label}")
    st.caption("6 dishes per city (2 per brand). Every claim runs through 8 deterministic audits before reaching this card.")

    # Compact legend strip — explains what the badges mean, once
    st.markdown(
        f'<details style="background:#fffaf2; border:1px solid #e6dcc1; '
        f'border-radius:4px; padding:0.5rem 0.85rem; margin:0.4rem 0 1rem 0; font-size:0.82rem;">'
        f'<summary style="cursor:pointer; color:{MEANING_COLORS["high"]}; font-weight:600; user-select:none;">'
        f'What the badges mean</summary>'
        f'<div style="margin-top:0.5rem; color:#5a4f3c; line-height:1.55;">'
        f'<strong>Confidence 0-100</strong> · composite of trend strength, local mentions, maturity, pantry fit, recency, and LTO history (higher = more evidence). '
        f'<strong>Maturity</strong> · Rising (chef-driven, early), Established (mainstream, safe to ship), Peak (saturated, fad-tail risk), Steady, Weak. '
        f'<strong>Lift</strong> · Low (seasoning remix), Medium (prep change or shelf-stable SKU), High (refrigerated SKU or new method). '
        f'<strong>Proven</strong> · brand has shipped this flavor before — newsroom-verified.'
        f'</div></details>',
        unsafe_allow_html=True,
    )


    dishes = load_dishes(city_key)
    if not dishes:
        st.warning(f"No dish recommendations found for `{city_key}`. "
                   f"Run `python scripts/dish_generator.py {city_key}` to generate.")
    else:
        # Filter by brand
        brand_filter = st.multiselect(
            "Show brands:",
            options=list(BRAND_COLORS.keys()),
            default=list(BRAND_COLORS.keys()),
        )

        # Group by brand — full-width brand banner above each pair of cards.
        for brand in BRAND_COLORS:
            if brand not in brand_filter:
                continue
            brand_dishes = [d for d in dishes if d["brand"] == brand]
            if not brand_dishes:
                continue
            # Sort: ship_now first, gap_fill second
            brand_dishes.sort(key=lambda d: 0 if d.get("type") == "ship_now" else 1)

            bc = BRAND_COLORS[brand]
            # Full-width horizontal brand banner
            st.markdown(
                f'<div style="background:{bc}; color:white; padding:0.45rem 1.1rem; '
                f'border-radius:6px; margin-top:1rem; margin-bottom:0.3rem; '
                f'display:flex; align-items:center; gap:0.7rem;">'
                f'<div style="font-family:Georgia, serif; font-size:1.05rem; font-weight:700; '
                f'letter-spacing:0.12em; text-transform:uppercase;">{brand}</div>'
                f'<div style="font-size:0.78rem; opacity:0.85; letter-spacing:0.05em;">'
                f'· {len(brand_dishes)} recommendation{"s" if len(brand_dishes) != 1 else ""}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            # Two-card row below the banner
            cols = st.columns(2)
            for i, d in enumerate(brand_dishes[:2]):
                with cols[i]:
                    render_dish_card(d)

# ── Trends ────────────────────────────────────────────────────────────────
with tab_trends:
    import altair as alt
    import pandas as pd

    brief_path = DATA / "quarterly_brief.json"
    if not brief_path.exists():
        st.warning("Run `python scripts/compute_quarterly_brief.py` to generate the quarterly brief data.")
    else:
        brief_all = json.loads(brief_path.read_text())
        meta = brief_all.get("_meta", {})
        brief = brief_all.get(city_label, {})

        # Dark header chip — quarterly cadence framing
        st.markdown(
            f'<div style="background:#2c3e50; color:#fff; padding:0.6rem 1.1rem; '
            f'border-radius:6px; margin:0.5rem 0 0.9rem 0; display:flex; '
            f'align-items:center; justify-content:space-between;">'
            f'<div><span style="font-size:0.72rem; letter-spacing:0.08em; opacity:0.75;">QUARTERLY BRIEF</span>'
            f'<div style="font-size:1.1rem; font-weight:700;">{meta.get("ref_quarter","")} · {city_label}</div></div>'
            f'<div style="font-size:0.8rem; opacity:0.75; text-align:right;">comparing to {meta.get("prior_quarter","")}<br>'
            f'forecast horizon: {", ".join(meta.get("forecast_horizon", []))}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Headline narrative — auto-generated from the data
        transitions = brief.get("transitions", [])
        movers_up = brief.get("movers_up", [])
        forecasts = brief.get("forecasts", [])
        headline_parts = []
        if transitions:
            t = transitions[0]
            headline_parts.append(
                f"<strong>{t['flavor']}</strong> crossed from "
                f"<em>{t['prior_stage']}</em> to <em>{t['ref_stage']}</em> "
                f"between {meta.get('prior_quarter')} and {meta.get('ref_quarter')}"
            )
        if movers_up:
            m = movers_up[0]
            headline_parts.append(
                f"<strong>{m['flavor']}</strong> is the quarter's top mover (+{m['delta']} mentions)"
            )
        if forecasts:
            rising = [f for f in forecasts if f["direction"] == "rising"]
            if rising:
                f = rising[0]
                headline_parts.append(
                    f"<strong>{f['flavor']}</strong> is forecast to keep rising (~{f['forecast_q2_2026']:.0f} mentions in Q2, ±{f['forecast_band']:.0f})"
                )
        if headline_parts:
            st.markdown(
                f'<div style="font-family: Georgia, serif; font-size:1.1rem; line-height:1.5; '
                f'color:#2A2825; background:#f4ede0; border-left:6px solid {MEANING_COLORS["high"]}; '
                f'padding:0.8rem 1.1rem; margin:0 0 1.2rem 0;">'
                f'{". ".join(headline_parts)}.</div>',
                unsafe_allow_html=True,
            )

        # ── Multi-quarter history chart — top flavors ─────────────────────
        st.markdown("#### 8-quarter trajectory · top flavors")
        st.caption("Each line is one flavor's mention count by quarter. Hover for the exact values. Dashed segment is the linear forecast.")

        histories = brief.get("top_flavor_histories", [])
        if histories:
            # Build long-form DataFrame
            hist_rows = []
            for h in histories:
                for pt in h["history"]:
                    hist_rows.append({
                        "flavor": h["flavor"],
                        "quarter": pt["quarter"],
                        "total": pt["total"],
                        "stage": h["current_stage"],
                    })
            hdf = pd.DataFrame(hist_rows)
            # Show only top 5 to avoid spaghetti
            top5_flavors = [h["flavor"] for h in histories[:5]]
            hdf_top = hdf[hdf["flavor"].isin(top5_flavors)]
            hdf_back = hdf[~hdf["flavor"].isin(top5_flavors)]

            # Background lines for non-top flavors (grayed)
            back_chart = alt.Chart(hdf_back).mark_line(
                strokeWidth=1, opacity=0.3, color="#a09683"
            ).encode(
                x=alt.X("quarter:O", title=None, sort="ascending"),
                y=alt.Y("total:Q", title="Indie mentions"),
                detail="flavor:N",
                tooltip=["flavor", "quarter", "total"],
            )

            fore_chart = alt.Chart(hdf_top).mark_line(
                point=alt.OverlayMarkDef(size=60, filled=True), strokeWidth=2.5
            ).encode(
                x="quarter:O",
                y="total:Q",
                color=alt.Color("flavor:N",
                                scale=alt.Scale(scheme="tableau10"),
                                legend=alt.Legend(title="Top 5 flavors", orient="right")),
                tooltip=["flavor", "quarter", "total"],
            )

            st.altair_chart((back_chart + fore_chart).properties(height=320),
                           use_container_width=True)

        st.divider()

        # ── Forecasts as small multiples ───────────────────────────────────
        st.markdown("#### Next-quarter forecast · top 3 flavors")
        st.caption(
            "Historical bars (solid) followed by Q2 and Q3 2026 forecast bars (dashed outline). "
            "Forecast band = ±1 residual standard deviation from a linear fit on trailing 5 quarters. "
            "Directional, not precise — multi-snapshot Google Trends history would tighten these."
        )

        if forecasts:
            fcols = st.columns(min(3, len(forecasts)))
            for idx, fc in enumerate(forecasts[:3]):
                with fcols[idx]:
                    arrow = ("↑ rising" if fc["direction"] == "rising"
                             else "↓ falling" if fc["direction"] == "falling"
                             else "→ stable")
                    arrow_color = (MEANING_COLORS["high"] if fc["direction"] == "rising"
                                   else MEANING_COLORS["low"] if fc["direction"] == "falling"
                                   else MEANING_COLORS["mid"])
                    st.markdown(
                        f'<div style="margin-bottom:0.3rem;">'
                        f'<div style="font-family:Georgia, serif; font-weight:700; font-size:1.05rem; color:#2A2825;">{fc["flavor"]}</div>'
                        f'<div style="font-size:0.78rem; color:{arrow_color}; font-weight:600;">{arrow}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                    # Build dataframe: historical + forecast as separate kind columns
                    hist_pts = [{"quarter": h["quarter"], "value": h["mentions"],
                                "kind": "historical"} for h in fc["history"]]
                    forecast_pts = [
                        {"quarter": "2026-Q2", "value": fc["forecast_q2_2026"], "kind": "forecast"},
                        {"quarter": "2026-Q3", "value": fc["forecast_q3_2026"], "kind": "forecast"},
                    ]
                    fdf = pd.DataFrame(hist_pts + forecast_pts)
                    band_pts = [
                        {"quarter": "2026-Q2", "low": max(0, fc["forecast_q2_2026"] - fc["forecast_band"]),
                         "high": fc["forecast_q2_2026"] + fc["forecast_band"]},
                        {"quarter": "2026-Q3", "low": max(0, fc["forecast_q3_2026"] - fc["forecast_band"]),
                         "high": fc["forecast_q3_2026"] + fc["forecast_band"]},
                    ]
                    bdf = pd.DataFrame(band_pts)

                    bars = alt.Chart(fdf).mark_bar().encode(
                        x=alt.X("quarter:O", sort=None, title=None,
                                axis=alt.Axis(labelAngle=-40, labelFontSize=10)),
                        y=alt.Y("value:Q", title="Mentions"),
                        color=alt.Color("kind:N",
                                        scale=alt.Scale(domain=["historical", "forecast"],
                                                        range=[MEANING_COLORS["high"], MEANING_COLORS["mid"]]),
                                        legend=None),
                        opacity=alt.condition(alt.datum.kind == "forecast",
                                              alt.value(0.55), alt.value(1.0)),
                        tooltip=["quarter:N", "value:Q", "kind:N"],
                    )
                    band = alt.Chart(bdf).mark_errorbar(color="#8B8378", thickness=2).encode(
                        x=alt.X("quarter:O", sort=None),
                        y="low:Q", y2="high:Q",
                    )
                    st.altair_chart((bars + band).properties(height=200),
                                   use_container_width=True)

        st.divider()

        # ── Movers grid ────────────────────────────────────────────────────
        st.markdown("#### What changed this quarter")
        mcol_up, mcol_down, mcol_trans = st.columns(3)
        with mcol_up:
            st.markdown(
                f'<div style="font-size:0.72rem; font-weight:700; letter-spacing:0.07em; color:{MEANING_COLORS["high"]}; margin-bottom:0.4rem;">▲ MOVERS UP</div>',
                unsafe_allow_html=True,
            )
            if movers_up:
                for m in movers_up[:5]:
                    st.markdown(
                        f'<div style="font-size:0.9rem; margin:0.2rem 0; color:#2A2825;">'
                        f'<strong>{m["flavor"]}</strong> '
                        f'<span style="color:{MEANING_COLORS["high"]}; font-weight:600;">+{m["delta"]}</span> '
                        f'<span style="font-size:0.78rem; color:#7a6f5c;">({m["prior_total"]} → {m["ref_total"]})</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("No flavors gained significantly.")

        with mcol_down:
            movers_down = brief.get("movers_down", [])
            st.markdown(
                f'<div style="font-size:0.72rem; font-weight:700; letter-spacing:0.07em; color:{MEANING_COLORS["low"]}; margin-bottom:0.4rem;">▼ MOVERS DOWN</div>',
                unsafe_allow_html=True,
            )
            if movers_down:
                for m in movers_down[:5]:
                    st.markdown(
                        f'<div style="font-size:0.9rem; margin:0.2rem 0; color:#2A2825;">'
                        f'<strong>{m["flavor"]}</strong> '
                        f'<span style="color:{MEANING_COLORS["low"]}; font-weight:600;">{m["delta"]}</span> '
                        f'<span style="font-size:0.78rem; color:#7a6f5c;">({m["prior_total"]} → {m["ref_total"]})</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("No significant declines.")

        with mcol_trans:
            st.markdown(
                f'<div style="font-size:0.72rem; font-weight:700; letter-spacing:0.07em; color:{MEANING_COLORS["mid"]}; margin-bottom:0.4rem;">⚡ MATURITY TRANSITIONS</div>',
                unsafe_allow_html=True,
            )
            if transitions:
                for t in transitions[:5]:
                    st.markdown(
                        f'<div style="font-size:0.9rem; margin:0.2rem 0; color:#2A2825;">'
                        f'<strong>{t["flavor"]}</strong>: '
                        f'<span style="color:#7a6f5c;">{t["prior_stage"]}</span> → '
                        f'<span style="color:{MEANING_COLORS["high"]}; font-weight:600;">{t["ref_stage"]}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("No stage transitions — landscape is stable.")

        # Footer — roadmap callout
        st.markdown(
            f'<div style="font-size:0.78rem; color:#7a6f5c; margin-top:1rem; padding-top:0.6rem; border-top:1px dotted #d4c89a; line-height:1.6;">'
            f'<em>Forecasts are linear projections from trailing 5 quarters — directional, not precise. '
            f'<strong>Multi-snapshot Google Trends history</strong> (currently 12-month rolling average) and '
            f'<strong>cross-city flow predictions</strong> ("pesto moves east, ~6 quarters behind LA") roll out '
            f'as the platform accrues longitudinal data via scheduled cron snapshots.</em>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ── vs Naive LLM ───────────────────────────────────────────────────────────
with tab_vs_llm:
    st.subheader("Naive LLM vs Taste Engine — same prompt, different rigor")

    # The single most quotable moment in the pitch: 8 vs 0.
    # Big metric row first; story follows.
    metric_left, metric_right = st.columns(2)
    with metric_left:
        st.metric(
            label="Hallucination classes caught — Taste Engine",
            value="8",
            delta="deterministic, in code",
            delta_color="off",
        )
    with metric_right:
        st.metric(
            label="Hallucination classes caught — naive LLM",
            value="0",
            delta="same model, no constraints",
            delta_color="off",
        )

    st.markdown(
        f'<div style="background:#fffaf2; border-left:6px solid {MEANING_COLORS["high"]}; '
        f'padding:0.6rem 1rem; margin:0.6rem 0 1rem 0; font-size:0.88rem; color:#5a4f3c; line-height:1.5;">'
        f'Pantry · duplication · LTO history · cuisine coherence · dish-name truthfulness · '
        f'ambiguous terms · comp restaurant verification · numerical claim audit. '
        f'Every one of these became an audit in code, not a prompt tweak.'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.caption(
        "We asked Claude Opus 4.7 the exact same question with no engine context. "
        "The naive output is the control; the Taste Engine card on the right is the treatment. "
        "Both used the same model."
    )

    npath = DATA / "naive_llm_baseline.json"
    if not npath.exists():
        st.warning("Run `python scripts/naive_llm_baseline.py` to generate the naive baseline.")
    else:
        naive = json.loads(npath.read_text())

        # The prompt
        st.markdown(
            f'<div style="background:#fffaf2; border:1px solid #d4c89a; '
            f'border-radius:4px; padding:0.7rem 1rem; margin:0.5rem 0 1.2rem 0;">'
            f'<div style="font-size:0.68rem; font-weight:700; letter-spacing:0.07em; color:#3D5A40; margin-bottom:0.25rem;">PROMPT (asked of both)</div>'
            f'<div style="font-family: Georgia, serif; font-size:0.98rem; color:#2A2825;">'
            f'"{naive["prompt"]}"</div></div>',
            unsafe_allow_html=True,
        )

        # Two columns: naive on the left, Taste Engine on the right
        left, right = st.columns(2)

        with left:
            st.markdown(
                '<div style="display:flex; align-items:center; gap:0.4rem; margin-bottom:0.5rem;">'
                '<span style="background:#8E4A3C; color:white; padding:0.15rem 0.7rem; '
                'border-radius:4px; font-size:0.72rem; font-weight:700; letter-spacing:0.06em;">NAIVE LLM (CONTROL)</span>'
                '<span style="font-family:monospace; font-size:0.72rem; color:#5a4f3c;">'
                f'{naive.get("model","claude-opus-4-7")}</span>'
                '</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<div style="background:#faf2ec; border-left:3px solid #8E4A3C; '
                f'padding:0.8rem 1rem; border-radius:4px; '
                f'font-size:0.88rem; line-height:1.5; color:#2A2825; '
                f'white-space:pre-wrap; font-family:Georgia, serif;">'
                f'{naive["response"]}'
                f'</div>',
                unsafe_allow_html=True,
            )

        with right:
            st.markdown(
                '<div style="display:flex; align-items:center; gap:0.4rem; margin-bottom:0.5rem;">'
                '<span style="background:#3D5A40; color:white; padding:0.15rem 0.7rem; '
                'border-radius:4px; font-size:0.72rem; font-weight:700; letter-spacing:0.06em;">TASTE ENGINE (TREATMENT)</span>'
                '<span style="font-family:monospace; font-size:0.72rem; color:#5a4f3c;">same model + pipeline</span>'
                '</div>',
                unsafe_allow_html=True,
            )
            # Render the Mission Chipotle al pastor hero card
            mission_cards = load_dishes("mission")
            hero = next((c for c in mission_cards
                         if c["brand"] == "Chipotle" and c.get("signal_term") == "al pastor"),
                        None)
            if hero:
                render_ship_now_card(hero)
            else:
                st.warning("Mission hero card not found.")

        # Failure-mode summary table
        st.divider()
        st.markdown("### Naive LLM failure modes (annotated against ground truth)")
        st.markdown(
            """
| Claim from naive output | Reality |
|---|---|
| "Slow-braised beef brisket" as a ship-now dish | ⛔ Hallucinated SKU. Brisket is NOT in current Chipotle pantry. Was a 2021 LTO (Smoked Brisket) and 2025 Quesabrisket — both currently dormant. |
| "Consommé for dipping" | ⛔ Operationally infeasible. Chipotle's line has no consommé station — multi-day prep + new equipment = HIGH lift, framed as low. |
| "Double-stacked tortillas griddled in braising fat" | ⛔ Process hallucination. Chipotle does not griddle tortillas. |
| "Beef brisket is already in Chipotle's supply chain (close to their barbacoa)" | ⛔ Factually wrong. Brisket and barbacoa are distinct cuts with distinct prep. |
| "Birria and suadero tacos are the dominant trend on Mission Street (2022–2025)" | ⚠️ Directionally right, but no evidence. Naive LLM asserts without citing a single review. |
| "King trumpet + oyster mushrooms" / "cashew crema" / "chayote slaw" / "salsa macha" / "crispy garlic chips" | ⛔ 5 more hallucinated SKUs in the second dish. |
| Pivots to vegan al pastor variation | ⚠️ **Misses the actual brand decision.** Chipotle announced Feb 10, 2026 relaunch of Chicken Al Pastor (not vegan) in real life. Naive LLM has no awareness. |
| No confidence score, no maturity stage, no LTO history, no portability tag | ⛔ No analytical scaffolding. Every claim reads as equally certain. |
            """
        )

        st.divider()

        with st.expander("Full annotated comparison (markdown)"):
            md_path = DATA / "naive_vs_taste_engine_comparison.md"
            if md_path.exists():
                st.markdown(md_path.read_text())

        st.markdown(
            f'<div style="background:#f4ede0; border-left:4px solid #3D5A40; '
            f'padding:0.9rem 1.2rem; margin-top:1rem; border-radius:4px;">'
            f'<div style="font-family: Georgia, serif; font-size:1.05rem; '
            f'font-style: italic; color:#2A2825; line-height:1.5;">'
            f'The LLM is a reasoning layer. The innovation is the pipeline that '
            f'constrains it, anchors it in evidence, and validates it against pantry, '
            f'history, and maturity stage.'
            f'</div></div>',
            unsafe_allow_html=True,
        )

# ── Gaps ───────────────────────────────────────────────────────────────────
with tab_gaps:
    st.markdown("### Gap analysis")
    st.caption(
        f"For each high-signal flavor in {city_label}: which brands can deliver from current pantry, "
        "and what SKUs would the others need. **Filled brand square** = pantry covers it. "
        "**Dashed outline** = gap (hover for the missing SKUs)."
    )

    fit = load_pantry_fit()
    signals = load_signal_ranking_dual(city_key, spec["geo"], limit=15)

    if not fit:
        st.warning("Run `python scripts/compute_pantry_fit.py` to generate the deliverability matrix.")
    elif signals:
        # Build a grid: rows = flavors, columns = brands
        st.markdown("#### Top 15 signals × brand deliverability")
        st.caption("**C** = competitive (lunch indie) score · **L** = leading (chef-driven) score · brand-colored squares = deliverable today")
        rows_html = ['<table style="width:100%; border-collapse: collapse; font-size: 0.88rem;">']
        rows_html.append(
            '<thead><tr style="border-bottom: 2px solid #d6cab2;">'
            '<th style="text-align:left; padding: 0.5rem;">Flavor</th>'
            '<th style="text-align:right; padding: 0.5rem;">C</th>'
            '<th style="text-align:right; padding: 0.5rem;">L</th>'
            '<th style="text-align:left; padding: 0.5rem;">Trajectory</th>'
        )
        for brand in BRAND_COLORS:
            rows_html.append(
                f'<th style="text-align:center; padding: 0.5rem; color: {BRAND_COLORS[brand]};">{brand}</th>'
            )
        rows_html.append('</tr></thead><tbody>')

        for s in signals:
            term = s["term"]
            comp = s["competitive_score"] or 0
            lead = s["leading_score"] or 0
            if comp >= 0.3 and lead >= 0.3: traj, traj_color = "Peak", MEANING_COLORS["low"]
            elif lead - comp >= 0.10: traj, traj_color = "Rising", MEANING_COLORS["mid"]
            elif comp - lead >= 0.10: traj, traj_color = "Established", MEANING_COLORS["high"]
            elif comp >= 0.25 or lead >= 0.25: traj, traj_color = "Steady", MEANING_COLORS["mid"]
            else: traj, traj_color = "Weak", MEANING_COLORS["muted"]
            row_bg = "#fdfaf3" if signals.index(s) % 2 == 0 else "#faf5e9"
            rows_html.append(f'<tr style="background: {row_bg};">')
            rows_html.append(
                f'<td style="padding: 0.45rem 0.5rem;"><strong>{term}</strong></td>'
                f'<td style="padding: 0.45rem 0.5rem; text-align:right; font-family: monospace;">{comp:.2f}</td>'
                f'<td style="padding: 0.45rem 0.5rem; text-align:right; font-family: monospace;">{lead:.2f}</td>'
                f'<td style="padding: 0.45rem 0.5rem; font-size: 0.78rem; color: {traj_color};">{traj}</td>'
            )
            # Heatmap-style cells: filled brand color = deliver; hollow = gap
            # (P2 audit fix — replaces glyph-counting with at-a-glance color)
            for brand in BRAND_COLORS:
                cell = fit.get(brand, {}).get(term, {})
                bc = BRAND_COLORS[brand]
                if cell.get("deliverable"):
                    rows_html.append(
                        f'<td style="padding: 0.45rem 0.5rem; text-align:center;">'
                        f'<span style="display:inline-block; width:22px; height:22px; '
                        f'background:{bc}; border-radius:4px; vertical-align:middle;" '
                        f'title="{brand} can ship {term} from current pantry"></span></td>'
                    )
                else:
                    missing = cell.get("missing", [])
                    tooltip = ", ".join(missing) if missing else "no match"
                    rows_html.append(
                        f'<td style="padding: 0.45rem 0.5rem; text-align:center;" title="needs: {tooltip}">'
                        f'<span style="display:inline-block; width:22px; height:22px; '
                        f'border:1.5px dashed {bc}; border-radius:4px; vertical-align:middle; opacity:0.5;"></span></td>'
                    )
            rows_html.append('</tr>')
        rows_html.append('</tbody></table>')
        st.html("\n".join(rows_html))

        st.divider()

        # Per-brand gap callouts — sourced through load_on_brand_signal_ranking
        # so off-brand flavors are filtered at the data layer, not at the
        # render site. See "DURABLE BRAND-CUISINE FILTER" comment block above.
        st.markdown("#### On-brand gap callouts per brand")
        st.caption(
            "High-signal flavors this brand can't currently deliver, sorted by score. "
            "Filtered at the data layer to flavors that fit each brand's cuisine identity — "
            "Chipotle stays Mexican, CAVA stays Mediterranean / Levantine, Sweetgreen is cross-cuisine. "
            "Off-brand flavors are structurally hidden so no surface can show truffle for Chipotle or mole for CAVA."
        )
        cols = st.columns(3)
        for idx, brand in enumerate(BRAND_COLORS):
            with cols[idx]:
                st.markdown(brand_banner(brand), unsafe_allow_html=True)
                # Helper does the cuisine filter; we still filter for
                # "undeliverable + meaningful score" at the render site since
                # those are the gap criteria specific to this surface.
                on_brand = load_on_brand_signal_ranking(
                    city_key, spec["geo"], brand, limit=15,
                )
                gaps_here = []
                for s in on_brand:
                    cell = fit.get(brand, {}).get(s["term"], {})
                    max_score = max(s.get("competitive_score") or 0, s.get("leading_score") or 0)
                    if cell.get("deliverable") or max_score < 0.30:
                        continue
                    gaps_here.append((s, cell, max_score))
                if not gaps_here:
                    st.caption("No on-brand gaps at the current score threshold.")
                else:
                    st.caption(f"{len(gaps_here)} on-brand gap{'s' if len(gaps_here) != 1 else ''}")
                    for s, cell, max_score in gaps_here[:8]:
                        missing = ", ".join(cell.get("missing", []))
                        label, lcolor = strength_label(max_score)
                        score_100 = int(round(max_score * 100))
                        st.markdown(
                            f'<div style="padding: 0.45rem 0.55rem; margin: 0.3rem 0; '
                            f'background: #fffaf2; border-left: 3px solid {BRAND_COLORS[brand]}; '
                            f'border-radius: 4px; font-size: 0.83rem;">'
                            f'<div style="display: flex; align-items: center; gap: 0.4rem;">'
                            f'<strong>{s["term"]}</strong>'
                            f'<span style="background:{lcolor}; color:white; padding:0.08rem 0.4rem; '
                            f'border-radius:999px; font-size:0.7rem;">{label} · {score_100}/100</span>'
                            f'</div>'
                            f'<div style="font-size: 0.72rem; color: #8a7c63; margin-top: 0.25rem;">'
                            f'add: {missing}</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

# ── Compare ────────────────────────────────────────────────────────────────
with tab_compare:
    st.subheader("Compare two neighborhoods")
    st.caption("Same brand pantries, different cities — see how local signal changes the recommendation.")
    col_a, col_b = st.columns(2)
    with col_a:
        city_a = st.selectbox(
            "City A", list(CITIES.keys()),
            format_func=lambda k: f"{CITIES[k]['emoji']} {CITIES[k]['label']}",
            index=0, key="ca",
        )
    with col_b:
        city_b = st.selectbox(
            "City B", list(CITIES.keys()),
            format_func=lambda k: f"{CITIES[k]['emoji']} {CITIES[k]['label']}",
            index=1, key="cb",
        )
    if city_a == city_b:
        st.warning("Pick two different cities to compare.")
    else:
        sa = load_signal_ranking(city_a, CITIES[city_a]["geo"], limit=15)
        sb = load_signal_ranking(city_b, CITIES[city_b]["geo"], limit=15)
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"### {CITIES[city_a]['emoji']} {CITIES[city_a]['label']}")
            for s in sa[:10]:
                st.markdown(
                    f"- **{s['term']}** `{int((s['signal_score'] or 0)*100)}/100` "
                    f"({s['mentions']} mentions {direction_arrow(s['trend_direction'])})"
                )
        with col2:
            st.markdown(f"### {CITIES[city_b]['emoji']} {CITIES[city_b]['label']}")
            for s in sb[:10]:
                st.markdown(
                    f"- **{s['term']}** `{int((s['signal_score'] or 0)*100)}/100` "
                    f"({s['mentions']} mentions {direction_arrow(s['trend_direction'])})"
                )
