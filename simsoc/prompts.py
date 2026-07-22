"""Prompt construction. One system prompt frames the simulator (as-of date,
market context, honesty rules); user prompts carry the archetype/persona card
plus the instrument (poll question or feature spec) and a strict JSON schema."""

from __future__ import annotations

SYSTEM_TMPL = """You are a population simulator for US information-security teams — the customer
and prospect base of CrowdStrike. You will be given a cluster of real-feeling practitioner
personas (an "archetype card") or a single persona, plus a question or a product/feature spec.

Reason AS these people, as of {as_of}. Use only knowledge available on that date.

Ground truth about the world as of {as_of}:
<market_context>
{market_context}
</market_context>
{events_block}
Simulation rules — these override any instinct to be agreeable:
1. These are simulated customers, not cheerleaders. Skepticism, indifference, budget
   vetoes, "we'd never get to this until next renewal," and competitor pull are common
   real answers and must appear when plausible for the segment.
2. Reflect within-cluster heterogeneity: distributions should rarely be unanimous.
   A cluster's stance vector, tooling, staffing, and current workload pressures should
   visibly shape the numbers and the verbatims.
3. Practitioners speak like practitioners: terse, concrete, occasionally cynical.
   Executives think in budget lines, board optics, and vendor risk. Small-business
   IT thinks in hours-per-week and what the MSP says.
4. Money is finite. High interest without a budget path is a real and common outcome.
5. Output VALID JSON only, exactly matching the requested schema. No prose outside JSON.
"""

POLL_SCHEMA = """Return JSON only:
{{
  "option_shares": {{{options_keys}}},          // fractions over this cluster, must sum to ~1.0
  "mean_sentiment": <float -2.0..2.0>,          // net feeling about the topic
  "themes": [<up to 4 short strings — the dominant reasoning threads>],
  "verbatims": [{{"role": "<title, segment>", "quote": "<1-2 sentences in that person's voice>"}},
                 {{...}}]                        // exactly 2, from different member types
}}"""

FEATURE_SCHEMA = """Return JSON only:
{
  "adoption_intent_shares": {"1_definitely_not": f, "2_unlikely": f, "3_maybe": f,
                              "4_likely": f, "5_definitely": f},   // sums to ~1.0
  "excitement": <float -2.0..2.0>,
  "workflow_fit": <float 1.0..5.0>,             // fit with how this cluster actually works day-to-day
  "top_objections": [<up to 4 short strings>],
  "budget_source_shares": {"existing_flex": f, "new_budget": f,
                            "displace_another_tool": f, "no_budget_path": f},
  "competitive_pull": "<none|microsoft|palo_alto|sentinelone|google|other>",
  "expected_timeline_shares": {"pilot_this_quarter": f, "within_6_months": f,
                                "at_next_renewal": f, "unlikely_ever": f},
  "questions_for_vendor": [<up to 3 short strings>],
  "verbatims": [{"role": "<title, segment>", "quote": "<1-2 sentences>"},
                 {"role": "...", "quote": "..."}]   // exactly 2
}"""


def build_system(as_of: str, market_context: str, events: list[str]) -> str:
    events_block = ""
    if events:
        joined = "\n\n---\n\n".join(events)
        events_block = ("\nAdditional broadcast events these people have just learned about:\n"
                        f"<events>\n{joined}\n</events>\n")
    return SYSTEM_TMPL.format(as_of=as_of, market_context=market_context, events_block=events_block)


def build_poll_user(card: str, question: str, options: list[str]) -> str:
    keys = ", ".join(f'"{o}": f' for o in options)
    return (f"{card}\n\n---\nPOLL QUESTION for this cluster:\n{question}\n\n"
            f"Answer options: {options}\n\n{POLL_SCHEMA.format(options_keys=keys)}")


def build_feature_user(card: str, spec: str) -> str:
    return (f"{card}\n\n---\nCROWDSTRIKE FEATURE / RELEASE UNDER TEST — react as this cluster "
            f"would on first briefing:\n<feature_spec>\n{spec}\n</feature_spec>\n\n{FEATURE_SCHEMA}")
