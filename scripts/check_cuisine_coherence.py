#!/usr/bin/env python3
"""Cuisine coherence checker.

Determines whether a set of flavors can be coherently combined into a single
dish. Used both at prompt-construction time (to suggest compatible co-flavors
per anchor) and as a post-generation audit (to reject dishes that fuse
incompatible cuisines without an explicit cross_family_justification).

Two flavors are compatible if any of their family memberships pair at score
>= 0.5 per data/cuisine_compatibility.json. Scores >= 0.7 are allowed by
default; 0.5-0.7 are allowed only with explicit cross_family_justification.

Usage as a CLI smoke test:
    python scripts/check_cuisine_coherence.py miso pesto
    python scripts/check_cuisine_coherence.py al-pastor cotija mole
"""
import json
import sys
from pathlib import Path
from itertools import combinations

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

THRESHOLD_ALLOWED = 0.7
THRESHOLD_JUSTIFICATION = 0.5


def load_families() -> dict:
    raw = json.loads((DATA / "flavor_cuisine_families.json").read_text())
    return raw["families"]


def load_compat() -> dict:
    raw = json.loads((DATA / "cuisine_compatibility.json").read_text())
    return raw["scores"]


def families_for(flavor: str, families: dict) -> list[str]:
    f = families.get(flavor)
    if not f:
        return []
    out = [f["primary"]]
    out.extend(f.get("secondary") or [])
    return out


def pair_score(fam_a: str, fam_b: str, compat: dict) -> float:
    """Lookup symmetric compatibility score."""
    if fam_a == fam_b:
        return compat.get(f"{fam_a}|{fam_b}", 1.0)
    return max(
        compat.get(f"{fam_a}|{fam_b}", 0),
        compat.get(f"{fam_b}|{fam_a}", 0),
    )


def best_score(flavor_a: str, flavor_b: str, families: dict, compat: dict
              ) -> tuple[float, tuple[str, str]]:
    """Return (best_score, (best_family_a, best_family_b)) across all
    family-pair combinations. Two flavors are as compatible as their MOST
    compatible family-membership pair."""
    fams_a = families_for(flavor_a, families)
    fams_b = families_for(flavor_b, families)
    if not fams_a or not fams_b:
        return 0.0, ("?", "?")
    best = -1.0
    best_pair = ("?", "?")
    for a in fams_a:
        for b in fams_b:
            s = pair_score(a, b, compat)
            if s > best:
                best = s
                best_pair = (a, b)
    return best, best_pair


def check_dish_coherence(flavors: list[str], families: dict | None = None,
                         compat: dict | None = None) -> dict:
    """Return a structured coherence report for a dish containing N flavors.

    Result fields:
      coherent        — True if every pair score >= 0.7 (allowed default)
      needs_justification — True if any pair is 0.5-0.7 (allowed with justification)
      incompatible    — list of (flavor_a, flavor_b, score, best_family_pair)
                        for pairs scoring < 0.5 (blocked)
      pair_scores     — full pairwise score map
    """
    families = families or load_families()
    compat = compat or load_compat()

    pair_scores = {}
    incompatible = []
    needs_justification_pairs = []

    for a, b in combinations(flavors, 2):
        score, pair = best_score(a, b, families, compat)
        key = tuple(sorted([a, b]))
        pair_scores[f"{key[0]} + {key[1]}"] = {
            "score": round(score, 2),
            "family_pair": pair,
        }
        if score < THRESHOLD_JUSTIFICATION:
            incompatible.append({"a": a, "b": b, "score": round(score, 2),
                                "family_pair": pair})
        elif score < THRESHOLD_ALLOWED:
            needs_justification_pairs.append({"a": a, "b": b,
                                              "score": round(score, 2),
                                              "family_pair": pair})

    return {
        "coherent": not incompatible and not needs_justification_pairs,
        "needs_justification": bool(needs_justification_pairs),
        "blocked": bool(incompatible),
        "incompatible_pairs": incompatible,
        "borderline_pairs": needs_justification_pairs,
        "pair_scores": pair_scores,
    }


def compatible_flavors_for(anchor: str, candidates: list[str],
                            families: dict | None = None,
                            compat: dict | None = None) -> dict:
    """For prompt construction: which candidates pair with the anchor and at
    what score. Returns sorted-by-score lists."""
    families = families or load_families()
    compat = compat or load_compat()

    allowed, borderline, blocked = [], [], []
    for c in candidates:
        if c == anchor:
            continue
        score, pair = best_score(anchor, c, families, compat)
        entry = {"flavor": c, "score": round(score, 2), "family_pair": pair}
        if score >= THRESHOLD_ALLOWED:
            allowed.append(entry)
        elif score >= THRESHOLD_JUSTIFICATION:
            borderline.append(entry)
        else:
            blocked.append(entry)
    allowed.sort(key=lambda x: -x["score"])
    borderline.sort(key=lambda x: -x["score"])
    blocked.sort(key=lambda x: -x["score"])
    return {"allowed": allowed, "borderline": borderline, "blocked": blocked}


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        print("\nExamples:")
        print("  python scripts/check_cuisine_coherence.py miso pesto")
        print("  python scripts/check_cuisine_coherence.py 'al pastor' cotija mole")
        sys.exit(1)
    flavors = [a.replace("-", " ") for a in sys.argv[1:]]
    result = check_dish_coherence(flavors)
    print(f"\nDish flavors: {flavors}")
    print(f"Coherent (no constraint required): {result['coherent']}")
    print(f"Blocked: {result['blocked']}")
    print(f"Needs cross_family_justification: {result['needs_justification']}")
    if result["incompatible_pairs"]:
        print("\nBlocked pairs:")
        for p in result["incompatible_pairs"]:
            print(f"  {p['a']} + {p['b']}  score={p['score']}  via {p['family_pair']}")
    if result["borderline_pairs"]:
        print("\nBorderline pairs (need justification):")
        for p in result["borderline_pairs"]:
            print(f"  {p['a']} + {p['b']}  score={p['score']}  via {p['family_pair']}")
    print("\nAll pair scores:")
    for k, v in result["pair_scores"].items():
        print(f"  {k:<40s} {v['score']}  {v['family_pair']}")


if __name__ == "__main__":
    main()
