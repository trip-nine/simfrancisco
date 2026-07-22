# simsoc — build & iteration brief (port of the simfrancisco orchestration loop)

Hand this to Claude Code with `/goal`: *maximize the weighted score in `rubric.yaml`.
Do not stop until (a) `python -m simsoc validate --smoke` is GREEN offline, (b)
`python -m simsoc validate` is GREEN on the live engine, and (c) a full poll +
feature-test run completes and writes reports.* Done is machine-checkable.

The four loops, unchanged from simfrancisco:
1. **Self-correction** — read your own validate scorecard; pick the next experiment
   (prior tweaks, prompt tuning, context edits) yourself.
2. **Verifier + adversarial critic** — before calling a gain real, a fresh agent
   re-runs validate; a second agent tries to prove the gain is spurious (target
   leakage into prompts, weight gaming, confounded direction checks — see the
   `e5_gravity_direction` conditioning note in rubric.yaml for a caught example).
3. **Dynamic workflows** — hillclimb (validate -> diagnose -> one fix-agent per
   failing check -> verify), prompt A/B with a held-out check split, batch scenario
   sweeps ending in scorecards + cost logs.
4. **Continuous feedback** — pre-push hook: `python -m simsoc validate --smoke`.

Tuning may touch: priors (config/*.yaml), prompts, market context, aggregation.
Tuning may never touch: rubric targets, or the definition of a check after it's set.

Freshness rule: `config/market_context.md` is a dated artifact. Any run whose
as-of date is >45 days past the digest date should refuse-or-warn; refresh the
digest from primary sources (vendor press releases, earnings) before big sweeps.
