#!/usr/bin/env python3
"""Agent-style dish generator using Anthropic tool use.

Replaces the single-shot prompt with a multi-turn loop:
  1. LLM reasons
  2. LLM calls a tool
  3. We execute the tool and return results
  4. LLM continues with new information
  5. Eventually LLM emits a final dish via propose_dish; if accepted, it's
     included in the final answer

The LLM cannot assert facts that aren't backed by a tool. Every quantitative
claim (signal score, mention count, LTO year, restaurant name, cuisine
compatibility) must come through a tool. check_brand_positioning catches
fabricated brand-behavior claims like "routes customers to tomatillo as a
stand-in for mole".

Usage:
    set -a && source .env && set +a
    python scripts/dish_generator_agent.py mission
"""
import json
import os
import sys
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from dish_tools import TOOL_DEFINITIONS, dispatch  # noqa: E402

DATA = ROOT / "data"
MODEL = "claude-opus-4-7"
MAX_AGENT_TURNS = 30  # safety cap on tool-call loop

CITY_KEY_TO_LABEL = {
    "weho": "West Hollywood",
    "williamsburg": "Williamsburg",
    "mission": "Mission District",
}

SYSTEM_PROMPT = """You are a menu innovation consultant for fast-casual restaurant chains.
You produce neighborhood-aware recommendations grounded in verified data.

Your core constraint: you may NOT assert factual claims that aren't backed by
a tool call. Every quantitative claim (scores, mention counts, LTO years,
restaurant names, cuisine compatibility) must come from a tool. Every claim
about brand operations, customer routing, marketing positioning, or business
strategy must be checked via check_brand_positioning before you assert it.

When you reason, talk freely. When you assert a fact, cite a tool. When the
data says "no_evidence", either drop the claim or reframe as opinion
("could position", "a customer might", "one read of this is") — never as fact.

You have these tools available:

DATA LOOKUP
  - get_brand_pantry(brand)         live SKUs the brand stocks
  - get_brand_existing_dishes(brand) current menu (avoid duplicates)
  - get_brand_lto_history(brand)    past LTOs with source URLs (relaunch lever)
  - get_signal_ranking(city, top_n) top flavors by cross-source score
  - get_pantry_fit(brand, flavor)   can brand deliver this flavor today?
  - get_pairing_score(flavor_a, flavor_b) cuisine compatibility
  - verify_restaurant_real(city, name) does this comp restaurant exist in our scrape?
  - get_evidence_counts(city, flavor) mention counts by pool
  - compute_lift_tier(brand, flavor) operational lift + rollout portability
  - compute_confidence(city, flavor, brand) 0-100 composite + components

GROUND-TRUTH CHECKS
  - check_brand_positioning(brand, claim) verify a brand-behavior claim
  - propose_dish(dish, brand, city, dish_type) final submission gate

WORKFLOW
For each (brand, city) pair, produce exactly two dishes:
  1. ship_now — built from current pantry, anchor on a deliverable signal
  2. gap_fill — high-signal flavor brand cannot currently deliver, naming
                specific missing SKUs

BRAND-CUISINE IDENTITY FILTER (mandatory before picking anchors)
Brands have cuisine identities and our recommendations must respect them.
Off-brand flavors (e.g. truffle for Chipotle, mole for CAVA) are NEVER
acceptable as recommendations — they ask the brand to extend beyond its
current cuisine identity, which is strategic repositioning, not a quick-win
menu suggestion. We are specifically positioned as a quick-win tool.

For EVERY candidate flavor you're considering as an anchor, call
check_brand_cuisine_fit(brand, flavor) FIRST. If it returns fit="off_brand",
drop that flavor immediately and look further down the signal ranking for
another candidate. Do not propose off-brand recommendations even if they have
high signal — surface a lower-signal on-brand alternative instead.

LONG-SHOT TIER (new)
If after filtering off-brand flavors out, your best on-brand candidate has
compute_confidence().score < 50, both the ship_now and gap_fill
recommendations must be marked with "long_shot": true. The prose must honestly
acknowledge: this isn't the city's loudest signal, but it's the strongest
flavor we can recommend that stays within the brand's cuisine. Use language
like "lower-volume but on-cuisine" or "smaller signal but quick-win-shaped".

Recommended steps per brand:
  1. get_brand_pantry, get_brand_existing_dishes, get_brand_lto_history
  2. get_signal_ranking for the city
  3. For EACH candidate flavor before short-listing: check_brand_cuisine_fit
     — drop off-brand candidates immediately
  4. For each remaining (on-brand) candidate: get_pantry_fit
  5. For ship_now: pick top on-brand deliverable, get_evidence_counts +
     compute_confidence + compute_lift_tier
  6. For gap_fill: pick top on-brand undeliverable
  7. If best on-brand candidate confidence < 50, set long_shot: true on both
     recommendations and adjust prose accordingly
  8. If combining multiple flavors: get_pairing_score for each pair
  9. Draft the dish
 10. If naming comp restaurants: verify_restaurant_real each one
 11. If making any brand-behavior claim: check_brand_positioning
 12. propose_dish — if rejected, fix and retry
 13. When all 3 brands × 2 dishes are accepted, emit final JSON array

DISH SCHEMA
ship_now:
  {type: "ship_now", brand, dish_name, tagline, ingredients[], signal_term,
   signal_score, signal_rank_note, novelty_check, confidence, confidence_reason,
   comp_context, long_shot: bool}

gap_fill:
  {type: "gap_fill", brand, target_flavor, signal_score, missing_skus[],
   dish_potential, operational_lift, comp_context, long_shot: bool}

long_shot field: set to true when the brand's top on-brand candidates all
score below 50 confidence (after off-brand flavors are filtered out). False
otherwise. The card UI renders long-shot cards with a distinct visual treatment
so operators see the honesty signal.

OUTPUT
Once all 6 dishes (2 per brand × 3 brands) are accepted by propose_dish,
emit your final response as a JSON array. NO markdown, just the array.
"""


def run_agent(city_key: str) -> list:
    if "ANTHROPIC_API_KEY" not in os.environ:
        sys.exit("ANTHROPIC_API_KEY not set.")
    city_label = CITY_KEY_TO_LABEL[city_key]

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    user_msg = (
        f"Generate 6 dish recommendations (1 ship_now + 1 gap_fill per brand) "
        f"for Chipotle, CAVA, and Sweetgreen in {city_label}. "
        f"Use the tools to gather facts. Call propose_dish for each dish before "
        f"including it in your final answer. When all 6 dishes pass propose_dish, "
        f"return the final JSON array — no markdown."
    )

    messages = [{"role": "user", "content": user_msg}]
    total_in = 0
    total_out = 0
    total_cache_read = 0
    total_cache_write = 0
    tool_call_count = 0

    for turn in range(MAX_AGENT_TURNS):
        resp = client.messages.create(
            model=MODEL,
            max_tokens=8192,
            system=[
                {"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}},
            ],
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )
        total_in += resp.usage.input_tokens
        total_out += resp.usage.output_tokens
        total_cache_read += getattr(resp.usage, "cache_read_input_tokens", 0) or 0
        total_cache_write += getattr(resp.usage, "cache_creation_input_tokens", 0) or 0

        # Print thinking + tool calls so we can watch the agent reason
        for block in resp.content:
            if block.type == "text":
                text = (block.text or "").strip()
                if text:
                    print(f"\n[turn {turn} · text]")
                    print(text[:600] + ("..." if len(text) > 600 else ""))
            elif block.type == "tool_use":
                tool_call_count += 1
                print(f"\n[turn {turn} · tool] {block.name}({json.dumps(block.input)[:200]})")

        if resp.stop_reason == "end_turn":
            # LLM is done — extract final JSON. Concatenate text from this AND
            # the most recent prior assistant message in case the array was
            # split across a max_tokens-continue cycle.
            final_text = ""
            # Walk recent assistant messages backward and prepend their text
            recent_assistants = []
            for msg in messages[::-1]:
                if msg["role"] == "assistant":
                    recent_assistants.insert(0, msg)
                    if len(recent_assistants) >= 2:
                        break
            for msg in recent_assistants:
                for block in msg["content"]:
                    if hasattr(block, "type"):
                        if block.type == "text":
                            final_text += block.text
                    elif isinstance(block, dict) and block.get("type") == "text":
                        final_text += block.get("text", "")
            # Also include current response's text
            final_text += "".join(b.text for b in resp.content if b.type == "text")

            print(f"\n=== AGENT DONE ===")
            print(f"turns={turn+1} tool_calls={tool_call_count}")
            print(f"tokens: input={total_in} output={total_out} cache_read={total_cache_read} cache_write={total_cache_write}")
            # Find a JSON array in the concatenated text — prefer the LAST one
            import re
            matches = list(re.finditer(r"\[\s*\{.*\}\s*\]", final_text, re.DOTALL))
            if not matches:
                print("WARNING: no JSON array found in final response")
                print(final_text[-3000:])
                return []
            try:
                return json.loads(matches[-1].group(0))
            except json.JSONDecodeError as e:
                print(f"WARNING: JSON parse failed: {e}")
                print(final_text[-3000:])
                return []

        # Append assistant's full message (text + tool_use blocks)
        messages.append({"role": "assistant", "content": resp.content})

        # Process tool calls and append results
        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                result = dispatch(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })
        if not tool_results:
            # Likely stop_reason="max_tokens" — model was mid-stream. Continue.
            if resp.stop_reason == "max_tokens":
                print(f"[turn {turn} · continue] stop_reason=max_tokens, prompting to continue")
                messages.append({"role": "user",
                    "content": "Continue from exactly where you left off. Do not repeat what you already emitted. Just resume the JSON array. End with just the closing ]."})
                continue
            print(f"WARNING: assistant stopped without end_turn and no tool calls "
                  f"(stop_reason={resp.stop_reason})")
            break
        messages.append({"role": "user", "content": tool_results})

    print(f"\nWARNING: hit MAX_AGENT_TURNS ({MAX_AGENT_TURNS})")
    return []


def main():
    city_key = (sys.argv[1] if len(sys.argv) > 1 else "mission").lower()
    if city_key not in CITY_KEY_TO_LABEL:
        sys.exit(f"unknown city: {city_key}. choices: {list(CITY_KEY_TO_LABEL)}")

    print(f"Running agent for {CITY_KEY_TO_LABEL[city_key]}...\n")
    dishes = run_agent(city_key)
    if not dishes:
        print("\n(no dishes returned)")
        return

    out_path = DATA / f"{city_key}_dish_recommendations_v8.json"
    out_path.write_text(json.dumps(dishes, indent=2))
    print(f"\nSaved {len(dishes)} dishes → {out_path}")


if __name__ == "__main__":
    main()
