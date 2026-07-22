# simsoc

**simulate and pressure-test CrowdStrike's customers before the release goes out**

A rebuild of [simfrancisco](https://github.com/trip-nine/simfrancisco) — the
distributionally-grounded synthetic-population poller — re-aimed from San Francisco
voters to **US information-security teams, Fortune 500 to solo-IT-admin SMB**. Ask the
panel anything; ship it a hypothetical CrowdStrike module, feature, or pricing change;
get back adoption-intent distributions, budget paths, objections, competitive leakage,
and in-character verbatims — all answered *inside* the mid-2026 market reality (the
agentic-SOC wave, Security Copilot bundled into Microsoft E5/E7, Palo Alto's completed
CyberArk acquisition, SIEM ingest-cost pain, two years after the July 2024 outage) and
*under* day-to-day task pressure (mid-incident, audit week, renewal negotiation,
half the team on July vacation).

## What ported from simfrancisco

| simfrancisco | simsoc |
|---|---|
| ACS PUMS person microdata + PWGTP weights | Firmographic tier priors (SUSB-order-of-magnitude firm counts) + `firm_weight` / `seat_weight` |
| Seeded deterministic personas + value vectors | Seeded deterministic firms/personas + 13-dim **stance vectors** (budget pressure, Microsoft gravity, AI trust, automation appetite, outage scar, ingest-cost pain, …) |
| Demographic archetype clustering, one batched LLM call per archetype | Archetype clustering on (tier × CrowdStrike relationship × role family × E5 status), ≤48 calls per question |
| Weighted post-stratification | Same, with `--weight seats` (endpoint/ARR-proxy share) or `--weight firms` (one-company-one-vote) |
| Broadcast events + first-class as-of date | `config/market_context.md` always-on world state + `--events` for release/news injection |
| sqlite prompt cache, byte-reproducible clean mode | Identical (`sha256(model|system|user|max_tokens)`) |
| `rubric.yaml` + `validate` binary | `python -m simsoc validate` — direction + target checks, weighted score, exit-code gate |
| Life-sim / pixel map | Not ported (SF eye-candy; the scored prediction core is what transfers) |

## Quickstart

```bash
pip install -r requirements.txt
cp .env.example .env            # set ANTHROPIC_API_KEY (not needed for --engine prior)

python -m simsoc build-panel                      # 240 seeded personas -> panels/default.json
python -m simsoc show-panel --panel panels/default.json

# offline pipeline check (no API): the stance-vector null baseline
python -m simsoc validate --smoke

# poll the panel (≤48 archetype calls, cached)
python -m simsoc poll --panel panels/default.json \
  --question "Proposition: an AI agent will handle most of our tier-1 triage within 12 months."

# feature-test a hypothetical release
python -m simsoc feature-test scenarios/ngsiem_flex_included.md --panel panels/default.json

# inject breaking news alongside the standing market context
python -m simsoc poll --question "..." --events news/competitor_announcement.md
```

Every run writes `runs/<timestamp>-<kind>/{raw.jsonl, results.json, report.md}` — the
report has headline numbers, segment cuts (tier / relationship / role family / E5),
weighted objection rankings, budget-source and timeline splits, competitive pull, and
labeled synthetic verbatims.

## The knobs that matter

- **`config/market_context.md`** — the world as the panel knows it (dated 2026-07-21;
  compiled from vendor press releases, earnings, and trade coverage). Refresh it or the
  panel drifts. `--as-of` + a trimmed context lets you re-ask history.
- **`config/*.yaml`** — every prior is editable: tier firm counts, attach rates,
  E5 penetration, MDR outsourcing, relationship mix, pressures. Replace the shipped
  order-of-magnitude priors with your real TAM/customer data for calibrated cuts.
- **`--mode persona`** — one call per respondent instead of per archetype: 5× cost,
  much richer verbatims. Use small panels (`--n-scale 0.25`) for qual deep-dives,
  archetype mode for quant.
- **`--engine prior`** — no-API heuristic answering purely from stance vectors.
  It ignores question semantics *by design*: it is the pipeline test and the null
  baseline your LLM runs should beat on rubric checks.

## Calibration honesty (read before trusting numbers)

SF had certified election canvasses; infosec mostly doesn't. The shipped
`rubric.yaml` contains internal-consistency direction checks (E5 gravity among
non-customers, customer>non-customer intent, churned skepticism). Real calibration
means adding `target:` checks from data you own — win/loss and churn reasons,
NPS by tier, module attach rates, spend-intention surveys — then tuning **priors,
prompts, and context only**. Targets are fixed once set. Until you've done that,
treat output as directional signal for hypothesis generation, objection discovery,
and message testing — not as a substitute for talking to actual customers.

Cost: archetype mode ≈ 48 calls/question; the cache makes re-runs and rubric
iteration free. A `validate` sweep is dozens of calls, not thousands.

## Layout

```
config/       firmographics, roles, tooling (Falcon modules + competitor stacks),
              pressures, panel defaults, market_context.md
simsoc/       sampler -> archetypes -> prompts -> llm (cache) -> runner ->
              aggregate -> report -> validate -> cli
scenarios/    example feature specs (AgentWorks SMB tier, NG-SIEM Flex ingest,
              Charlotte autonomy dial + audit packs)
questions/    example poll library
rubric.yaml   the machine-checkable gate
```
