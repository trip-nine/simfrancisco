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

## Web console (Vercel)

`vercel.json` + `api/` ship the whole panel as a one-function FastAPI console —
the SIMSOC dark-ops UI with the live 240-persona strip, poll and feature-test
tabs, engine and weight toggles, and rendered distributions. It runs the exact
engine in this repo: the instant `prior` baseline works with no secrets; add
`ANTHROPIC_API_KEY` in Vercel → Settings → Environment Variables to light up
the live Claude engine (capped at 20 archetype calls per question to fit
serverless time limits; cache lives in `/tmp`).

`api/` is a self-contained mirror of `simsoc/`, `config/`, and `scenarios/`
because Vercel's Python builder guarantees bundling of the function directory.
Never edit `api/simsoc` or `api/config` directly — edit the canonical roots and
run `scripts/sync_api.sh`. Enable the guard once per clone:

```bash
git config core.hooksPath .githooks   # pre-push: mirror-drift check + validate --smoke
```
### Pixel Earth map (`/map`)

The console's sibling view is a zoomable, pannable pixel-art map of the whole
planet — equirectangular, wrapping horizontally so panning east-west never ends
— baked offline by `scripts/build_worlddata.py` into `api/mapdata.py` (climate
bands, country borders, and US state borders; no geo libraries at runtime).
The panel is global: 240 US personas plus 80 international ones across EMEA,
APAC, and the Americas ex-US (`config/international.yaml` — per-geo universes,
relationship priors calibrated so worldwide customers sum to the ~40k public
anchor, E5 multipliers, and stance deltas for GDPR/NIS2, MSSP reliance, and FX
pressure). The region pill flies the camera *and scopes the ask*: polls and
feature tests accept `region`, running on just that geography's sims with the
correct sub-universe weights.
Terrain re-renders through three levels of detail as you zoom: a baked world
image at globe scale, then procedurally textured 4px- and 12px-per-cell tiles
(waves, forests, dunes, ice cracks) generated on the fly from coordinate
hashes, so close-up never turns into flat blurred squares. Every one of the 240 panel
firms is placed in a US metro in its sampled region and rendered as a hand-
styled pixel building sized by tier — outlines, lit windows, side shading, a
red roof-cap and flag for CrowdStrike customers; each building has its panel
respondent plus deterministically sampled coworkers wandering outside.

- **Tap a building** → firmographics, stack, modules, renewal, people on site.
- **Tap a person** → role, day-to-day, and what they're *thinking right now* —
  thoughts are composed from the persona's stance vector reacting to **live
  headlines** (`/api/news`, keyless Google News RSS, cached 10 min), or by a
  single cached Claude call when `ANTHROPIC_API_KEY` is set.
- **Chat bar on the map** runs the same synthetic polls/feature tests as the
  console with `news: true`, which injects today's real headlines into every
  cluster's context as events. Results tint the buildings red↔blue by cluster
  sentiment (or adoption intent), so the answer is readable on the map itself.

`GET /api/map` returns terrain RLE + entities; `GET /api/thought` returns one
sim's current thinking; poll/feature responses carry `rows_map` for the tint
join. The prior engine ignores headline semantics by design (it is a
stance-arithmetic baseline); only the live engine actually *reads* the news.

