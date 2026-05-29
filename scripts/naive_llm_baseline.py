#!/usr/bin/env python3
"""Naive LLM baseline — what a generic prompt produces with NO Taste Engine context.

Captures the control side of the control-vs-treatment demonstration. The naive
prompt asks Claude to recommend dishes for Chipotle in Mission District based on
"current trends" — no review data, no pantry, no LTO history, no maturity
analysis, no deliverability filter. The output we capture should exhibit the
predictable failure modes: hallucinated SKUs, stereotype-level neighborhood
detail, no evidence anchors, false confidence, no operational lift, no awareness
of what Chipotle has already shipped.

Output:
    data/naive_llm_baseline.json  — { prompt, response, captured_at }

Usage:
    set -a && source .env && set +a
    python scripts/naive_llm_baseline.py
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

NAIVE_PROMPT = (
    "Recommend dishes for Chipotle to launch in the Mission District of San "
    "Francisco based on current food trends in that neighborhood. Give me 2 "
    "specific dish concepts with ingredients and rationale."
)


def main():
    if "ANTHROPIC_API_KEY" not in os.environ:
        sys.exit("ANTHROPIC_API_KEY not set. Run: set -a && source .env && set +a")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=2000,
        messages=[{"role": "user", "content": NAIVE_PROMPT}],
    )
    text = "\n".join(b.text for b in resp.content if hasattr(b, "text"))

    out_path = DATA / "naive_llm_baseline.json"
    out_path.write_text(json.dumps({
        "prompt": NAIVE_PROMPT,
        "model": "claude-opus-4-7",
        "response": text,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "_purpose": (
            "Control sample for the naive-LLM vs Taste Engine comparison. The "
            "Taste Engine treatment is data/mission_dish_recommendations_v6.json "
            "(Chipotle entries). Side-by-side annotation lives in "
            "data/naive_vs_taste_engine_comparison.md."
        ),
    }, indent=2))
    print(f"Wrote {out_path} ({len(text)} chars)")
    print()
    print("--- response preview ---")
    print(text[:1200])


if __name__ == "__main__":
    main()
