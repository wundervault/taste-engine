#!/usr/bin/env python3
"""Post-generation numerical claim audit.

LLM prose in signal_rank_note / confidence_reason / operational_lift / etc.
often makes numerical claims:
  - "miso scores 0.83 locally"  → must match card.signal_score
  - "Al pastor has been shipped in 2023, 2024, and 2026"  → must match LTO years
  - "58 indie mentions across 14 restaurants"  → must match evidence_counts

This audit extracts numbers from the text fields, classifies them by context,
and verifies each against ground truth. Flags any unsupported number as a
potential hallucination.

For hackathon scope, we check three classes:
  signal_score   — decimal 0.0-1.0 matched against card.signal_score
  shipped_year   — 4-digit year matched against lto_proven.years
  mention_count  — integer in "N mentions" / "N reviews" context

Reads:
    data/{city}_dish_recommendations_v6.json

Writes:
    data/numerical_audit.json

Usage:
    python scripts/audit_numerical_claims.py
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CITIES = ["weho", "williamsburg", "mission"]


def extract_signal_score_claims(text: str) -> list[float]:
    """Decimal that appears to be a SIGNAL score (not a pairing/coherence score).

    Patterns matched as signal-score claims:
      - "signal_score 0.83" / "signal score 0.83" / "signal of 0.83"
      - "(0.58)" only when the surrounding ~40 chars mention "signal" or "score"
        and DO NOT mention "pair" / "compatibility" / "coherence"

    Pairing/coherence scores (e.g., "harissa pairs with lentils at score 1.0")
    are deliberately excluded — those come from get_pairing_score, not the
    card's signal_score field, and are not what this audit is checking."""
    out = []
    text_l = text.lower()
    # Explicit signal-score claims
    for m in re.finditer(r"\bsignal[_\s]score[:\s]+(\d\.\d+)\b", text, re.IGNORECASE):
        out.append(float(m.group(1)))
    for m in re.finditer(r"\bsignal\s+of\s+(\d\.\d+)\b", text, re.IGNORECASE):
        out.append(float(m.group(1)))
    # Parenthetical decimal — only count if surrounding context names "signal"
    # AND doesn't name "pair"/"compatibility"/"coherence"
    for m in re.finditer(r"\((\d\.\d+)\)", text):
        start = max(0, m.start() - 60)
        end = min(len(text), m.end() + 30)
        window = text_l[start:end]
        if "signal" in window and not any(w in window for w in
                                          ("pair", "compatibility", "coherence",
                                           "cuisine", "family", "families")):
            out.append(float(m.group(1)))
    return out


def extract_year_claims(text: str) -> list[int]:
    """4-digit years 2010-2030 only (so we don't match scores like 2026 as a
    year vs as some other number)."""
    return [int(m.group(0)) for m in re.finditer(r"\b(?:201[0-9]|202[0-9]|2030)\b", text)]


def extract_mention_count_claims(text: str) -> list[tuple[int, str]]:
    """Integers in 'N mentions' / 'N reviews' / 'across N restaurants' context."""
    out = []
    for m in re.finditer(
        r"\b(\d+)\s+(?:indie\s+)?(mentions?|reviews?|restaurants?|local\s+mentions?)\b",
        text, re.IGNORECASE,
    ):
        out.append((int(m.group(1)), m.group(2).lower()))
    return out


def audit_card(card: dict) -> dict:
    text_fields = ["signal_rank_note", "confidence_reason",
                    "dish_potential", "operational_lift", "novelty_check"]
    text = " ".join((card.get(f) or "") for f in text_fields)

    # Ground truth
    card_signal_score = card.get("signal_score")
    if card_signal_score is not None:
        try:
            card_signal_score = float(card_signal_score)
        except (ValueError, TypeError):
            card_signal_score = None
    lto = card.get("lto_proven") or {}
    lto_years = [int(y) for y in (lto.get("years") or []) if str(y).isdigit()]
    ev = card.get("evidence_counts") or {}
    comp = ev.get("competitive_mentions", 0) or 0
    lead = ev.get("leading_mentions", 0) or 0
    total = comp + lead
    recent12 = ev.get("recent_12mo_mentions", 0) or 0

    violations = []

    # Signal score check — tolerance ±0.02 to handle rounding
    for claimed in extract_signal_score_claims(text):
        if card_signal_score is None:
            continue
        if abs(claimed - card_signal_score) > 0.02:
            violations.append({
                "kind": "signal_score_mismatch",
                "claimed": claimed,
                "actual": card_signal_score,
                "delta": round(claimed - card_signal_score, 3),
            })

    # Year claims — only against LTO years IF the surrounding sentence
    # references an LTO / shipped / launched / brought back
    if lto_years:
        lto_keywords_re = re.compile(
            r"\b(?:lto|shipped|launched|relaunch|brought back|active|"
            r"first\s+shipped|debut|introduced)\b",
            re.IGNORECASE,
        )
        sentences = re.split(r"[.!?]\s+", text)
        for sent in sentences:
            if not lto_keywords_re.search(sent):
                continue
            for year in extract_year_claims(sent):
                # Stale-claim or invented-year detection
                if year not in lto_years:
                    # If the year is plausibly a different reference
                    # (e.g., a launch year for a different LTO), still flag
                    violations.append({
                        "kind": "lto_year_unsupported",
                        "claimed": year,
                        "actual_years": lto_years,
                        "context": sent[:200],
                    })

    # Mention count check
    for claimed, kind in extract_mention_count_claims(text):
        target = None
        if "mention" in kind:
            target = total
        elif "review" in kind:
            target = total  # reviews ~ total mention rows; rough check
        elif "restaurant" in kind:
            target = ev.get("dma_trend")
            # Don't check restaurants — we don't have a clean restaurant count
            target = None
        if target is None:
            continue
        # Loose tolerance: within ±20% or ±5 absolute
        tol = max(5, target * 0.2)
        if abs(claimed - target) > tol:
            violations.append({
                "kind": "mention_count_mismatch",
                "claimed": claimed,
                "actual": target,
                "kind_of_count": kind,
            })

    return {
        "violations": violations,
        "verdict": "OK" if not violations else "MISMATCH",
        "signal_claims_checked": extract_signal_score_claims(text),
        "year_claims_checked": extract_year_claims(text),
        "mention_claims_checked": extract_mention_count_claims(text),
    }


def main():
    out = {}
    for city in CITIES:
        path = DATA / f"{city}_dish_recommendations_v6.json"
        if not path.exists():
            continue
        cards = json.loads(path.read_text())
        city_audit = []
        for card in cards:
            audit = audit_card(card)
            city_audit.append({
                "brand": card["brand"],
                "type": card["type"],
                "dish_name": card.get("dish_name") or f'gap_fill: {card.get("target_flavor")}',
                "audit": audit,
            })
        out[city] = city_audit

    target = DATA / "numerical_audit.json"
    target.write_text(json.dumps(out, indent=2))
    print(f"Wrote {target}\n")

    total_violations = 0
    print("AUDIT SUMMARY:")
    for city, audits in out.items():
        print(f"\n{city.upper()}:")
        for a in audits:
            v = a["audit"]["verdict"]
            mark = "✓" if v == "OK" else "⚠"
            vc = len(a["audit"]["violations"])
            total_violations += vc
            print(f"  {mark} {v:<8s} {a['brand']:<11s} {a['dish_name']:<45s} "
                  f"violations={vc}")
            for viol in a["audit"]["violations"]:
                if viol["kind"] == "signal_score_mismatch":
                    print(f"      → claimed {viol['claimed']} actual {viol['actual']} (Δ {viol['delta']})")
                elif viol["kind"] == "lto_year_unsupported":
                    print(f"      → year {viol['claimed']} not in LTO history {viol['actual_years']}")
                else:
                    print(f"      → {viol}")
    print(f"\nOVERALL: {total_violations} numerical violations across all cards.")


if __name__ == "__main__":
    main()
