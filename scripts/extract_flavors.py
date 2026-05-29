#!/usr/bin/env python3
"""Extract flavor/ingredient mentions from reviews into flavor_mentions.

Approach: deterministic keyword matching against a vocabulary seeded from
the `trends` table (so flavor_mentions.flavor JOINs cleanly with trends.term)
plus a small curated EXTRA list for support flavors that show up in gap
analysis but aren't tracked by Google Trends.

Idempotent: clears flavor_mentions and rebuilds from scratch each run.

Usage:
    python scripts/extract_flavors.py
"""
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hermes.db import init_db  # noqa: E402

# Curated additions — flavors mentioned in gap-analysis or known cuisine markers
# that don't appear in the trends table. Keep this list short and intentional.
EXTRA_VOCAB = [
    "cotija",
    "crema",
    "allspice",
    "banchan",
    "galbi",
    "japchae",
    "shawarma",
    "labneh",
    "halloumi",
    "guajillo",
    "ancho",
    "tofu",
    "kabocha",
    "ube",
    "matcha",
]

# Context-aware extraction for ambiguous terms. Each entry has:
#   require_within: list of food-context words; at least one must appear within
#                   ±CONTEXT_WINDOW tokens of the match
#   forbid_phrases: list of phrases that, if present in the surrounding sentence,
#                   reject the match
# All checks are case-insensitive.
#
# Without these rules, "what a jerk move" would falsely contribute to jerk
# flavor signal; "honey, can you" would contribute to honey signal; etc.
CONTEXT_WINDOW = 6  # tokens on each side

AMBIGUOUS_TERMS = {
    "jerk": {
        # In Caribbean-heavy restaurant reviews, "jerk" is overwhelmingly the
        # spice/cooking-style noun. Require-within is too narrow (Sweet Jerk
        # Burritos, jerk mushrooms, "best jerk around" all get false-rejected).
        # Use forbid-only filtering with comprehensive insult patterns.
        "require_within": [],
        "forbid_phrases": [
            "what a jerk", "such a jerk", "real jerk", "jerk move",
            "being a jerk", "you jerk", "jerk off", "kick some jerk",
            "arrested the jerk", "stupid jerk", "rude jerk", "total jerk",
            "biggest jerk", "the jerk for being", "absolute jerk",
        ],
    },
    "honey": {
        "require_within": [
            "chicken", "hot", "mustard", "sesame", "glazed", "glaze",
            "drizzle", "drizzled", "sweet", "butter", "jar", "side", "sauce",
            "dressing", "vinaigrette", "wing", "wings", "biscuit", "bread",
            "bread", "harissa", "ginger", "lemon", "lime", "balsamic",
            "soy", "miso", "lavender", "tea", "infused", "raw", "local",
            "wildflower", "buckwheat", "comb", "honeycomb", "spicy",
            "habanero", "chili", "chile", "cocktail", "cocktails", "syrup",
            "tahini", "salmon", "salmon", "pork", "ham", "cake", "yogurt",
            "ricotta", "goat", "feta", "cornbread", "pancake", "pancakes",
            "fried", "tequila", "whiskey", "bourbon",
        ],
        "forbid_phrases": [
            "honey,", "oh honey", "hi honey", "honey bunny",
            "hey honey", "honey baby", "honey i'm", "honey i am",
            "honey can you", "honey would you", "honey did you",
        ],
    },
    "curry": {
        # In restaurant reviews, plain "curry" is overwhelmingly food.
        # Use forbid-only filtering — only reject the "curry favor" idiom.
        "require_within": [],
        "forbid_phrases": [
            "curry favor", "currying favor", "to curry",
        ],
    },
    "mole": {
        # In restaurant reviews, plain "mole" (already word-bounded against
        # "guacamole") is overwhelmingly the sauce. Require-within was too
        # narrow and dropped real food mentions like "the mole was exquisite".
        # Use forbid-only filtering for the very rare dermatology context.
        "require_within": [],
        "forbid_phrases": [
            "mole removed", "mole removal", "had a mole", "skin mole",
            "moles on", "dermatology", "mohs surgery",
        ],
    },
    "truffle": {
        # In restaurant reviews, truffle is overwhelmingly a savory food
        # element (truffle oil, truffle burger, truffle mash, truffle pasta).
        # Require-within was too narrow and dropped real mentions.
        # Use forbid-only filtering — reject only chocolate/candy contexts.
        "require_within": [],
        "forbid_phrases": [
            "chocolate truffle", "chocolate truffles", "candy truffle",
            "candy truffles", "dessert truffle", "dessert truffles",
            "truffle pop", "chocolate-covered truffle", "chocolate-truffle",
            "truffle cake", "truffle ball", "truffle balls",
            "rum truffle", "boozy truffle",
        ],
    },
}


def build_vocab(conn):
    """Return list of (term, compiled_regex). Vocab = trends + EXTRA, dedup'd."""
    terms = {row["term"] for row in conn.execute("SELECT DISTINCT term FROM trends")}
    terms.update(EXTRA_VOCAB)
    vocab = []
    for term in sorted(terms):
        # Multi-word: allow flexible whitespace. Single-word: word boundary.
        escaped = re.escape(term).replace(r"\ ", r"\s+")
        pattern = re.compile(rf"\b{escaped}\b", re.IGNORECASE)
        vocab.append((term, pattern))
    return vocab


def passes_context_filter(text: str, term: str, match_start: int,
                          match_end: int) -> bool:
    """For ambiguous terms, verify required food-context near the match
    and absence of forbidden phrases in the surrounding sentence."""
    rules = AMBIGUOUS_TERMS.get(term)
    if not rules:
        return True

    text_l = text.lower()

    # Forbidden phrase check — sentence-level
    # Get the surrounding ~120 chars (rough sentence window)
    win_start = max(0, match_start - 80)
    win_end = min(len(text), match_end + 80)
    surrounding = text_l[win_start:win_end]
    for forbidden in rules.get("forbid_phrases", []):
        if forbidden.lower() in surrounding:
            return False

    # Required context check — tokenize ±CONTEXT_WINDOW around the match
    # Get the word-level window
    pre_text = text_l[:match_start]
    post_text = text_l[match_end:]
    pre_tokens = re.findall(r"[a-z']+", pre_text)[-CONTEXT_WINDOW:]
    post_tokens = re.findall(r"[a-z']+", post_text)[:CONTEXT_WINDOW]
    window_tokens = set(pre_tokens + post_tokens)

    require = rules.get("require_within", [])
    if not require:
        # Forbid-only mode (passed the forbid check above) → accept.
        return True
    return any(req in window_tokens for req in require)


def extract(text, vocab):
    """Return Counter{term: count} for one review.

    Context-aware: ambiguous terms (jerk, honey, curry, mole, truffle) must
    pass a food-context filter to be counted. Each individual match is
    evaluated separately — a review can have 2 jerk hits where only 1 passes."""
    c = Counter()
    for term, pat in vocab:
        if term in AMBIGUOUS_TERMS:
            # Per-match context check
            n_pass = 0
            for m in pat.finditer(text):
                if passes_context_filter(text, term, m.start(), m.end()):
                    n_pass += 1
            if n_pass:
                c[term] = n_pass
        else:
            n = len(pat.findall(text))
            if n:
                c[term] = n
    return c


def main():
    conn = init_db()
    try:
        vocab = build_vocab(conn)
        print(f"vocabulary: {len(vocab)} terms")

        # Rebuild from scratch — cheap and keeps the table in sync with vocab edits.
        conn.execute("DELETE FROM flavor_mentions")

        rows = conn.execute("SELECT id, text FROM reviews").fetchall()
        total_mentions = 0
        reviews_with_hits = 0
        term_totals = Counter()

        for row in rows:
            hits = extract(row["text"], vocab)
            if not hits:
                continue
            reviews_with_hits += 1
            for term, count in hits.items():
                conn.execute(
                    "INSERT INTO flavor_mentions (review_id, flavor, count) VALUES (?, ?, ?)",
                    (row["id"], term, count),
                )
                total_mentions += count
                term_totals[term] += count

        conn.commit()

        print(f"reviews scanned:       {len(rows)}")
        print(f"reviews with >=1 hit:  {reviews_with_hits}")
        print(f"total mention count:   {total_mentions}")
        print(f"distinct terms hit:    {len(term_totals)}")
        print("\ntop 15 flavors by raw mention count:")
        for term, n in term_totals.most_common(15):
            print(f"  {term:<20} {n}")

        zero_hit = sorted(set(t for t, _ in vocab) - set(term_totals))
        if zero_hit:
            print(f"\nvocab terms with 0 hits ({len(zero_hit)}): {', '.join(zero_hit)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
