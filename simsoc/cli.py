"""simsoc CLI.

  python -m simsoc build-panel  [--n-scale 1.0] [--seed 42] [--out panels/default.json]
  python -m simsoc show-panel   [--panel panels/default.json]
  python -m simsoc poll         --question "..." [--options a,b,c] [flags]
  python -m simsoc feature-test scenarios/spec.md [flags]
  python -m simsoc validate     [--rubric rubric.yaml] [--smoke]

Common flags: --config-dir --panel --engine anthropic|prior --model --mode archetype|persona
              --weight seats|firms --as-of YYYY-MM-DD --events f1.md f2.md --no-context
              --out runs --run-tag TAG
"""

from __future__ import annotations
import argparse
import sys
from collections import Counter
from pathlib import Path

from . import aggregate, report
from .runner import Run, write_run
from .sampler import build_panel
from .schema import load_panel, save_panel
from .validate import DEFAULT_OPTIONS, run_validate


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config-dir", default="config")
    p.add_argument("--panel", default=None, help="panel JSON (else built fresh from configs)")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--n-scale", type=float, default=1.0, help="scale default quotas")
    p.add_argument("--engine", choices=["anthropic", "prior"], default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--mode", choices=["archetype", "persona"], default=None)
    p.add_argument("--weight", choices=["seats", "firms"], default=None)
    p.add_argument("--as-of", dest="as_of", default=None)
    p.add_argument("--events", nargs="*", default=[], help="markdown files broadcast as events")
    p.add_argument("--no-context", action="store_true", help="drop market_context.md injection")
    p.add_argument("--out", default="runs")
    p.add_argument("--run-tag", default="")


def _mk_run(a) -> Run:
    return Run(config_dir=a.config_dir, panel_path=a.panel, seed=a.seed, scale=a.n_scale,
               engine=a.engine, model=a.model, mode=a.mode,
               as_of_date=a.as_of, no_context=a.no_context or None,
                run_tag=a.run_tag, weight_mode=a.weight)


def _events(a) -> list[str]:
    return [Path(p).read_text() for p in a.events]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="simsoc", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    bp = sub.add_parser("build-panel")
    bp.add_argument("--config-dir", default="config")
    bp.add_argument("--seed", type=int, default=None)
    bp.add_argument("--n-scale", type=float, default=1.0)
    bp.add_argument("--out", default="panels/default.json")

    sp = sub.add_parser("show-panel")
    sp.add_argument("--config-dir", default="config")
    sp.add_argument("--panel", default=None)
    sp.add_argument("--seed", type=int, default=None)
    sp.add_argument("--n-scale", type=float, default=1.0)

    pl = sub.add_parser("poll")
    pl.add_argument("--question", required=True)
    pl.add_argument("--options", default=None, help="comma-separated; default 5-pt oppose..support")
    _common(pl)

    ft = sub.add_parser("feature-test")
    ft.add_argument("scenario", help="markdown feature spec")
    _common(ft)

    va = sub.add_parser("validate")
    va.add_argument("--rubric", default="rubric.yaml")
    va.add_argument("--config-dir", default="config")
    va.add_argument("--panel", default=None)
    va.add_argument("--engine", choices=["anthropic", "prior"], default=None)
    va.add_argument("--weight", choices=["seats", "firms"], default=None)
    va.add_argument("--n-scale", type=float, default=1.0)
    va.add_argument("--smoke", action="store_true", help="prior engine + small panel")

    a = ap.parse_args(argv)

    if a.cmd == "build-panel":
        ps = build_panel(a.config_dir, seed=a.seed, scale=a.n_scale)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        save_panel(ps, a.out)
        print(f"wrote {len(ps)} personas -> {a.out}")
        return 0

    if a.cmd == "show-panel":
        ps = load_panel(a.panel) if a.panel else build_panel(a.config_dir, seed=a.seed, scale=a.n_scale)
        fw = sum(p.firm_weight for p in ps)
        sw = sum(p.seat_weight for p in ps)
        print(f"{len(ps)} personas representing ~{fw:,.0f} US firms / ~{sw:,.0f} protected endpoints")
        for label, keyfn in [("tier", lambda p: p.tier), ("relationship", lambda p: p.cs_relationship),
                             ("role family", lambda p: p.family), ("industry", lambda p: p.industry),
                             ("E5/E7", lambda p: "e5" if p.e5 else "non_e5"),
                             ("SIEM", lambda p: p.siem), ("MDR", lambda p: p.mdr_partner)]:
            c = Counter(keyfn(p) for p in ps)
            print(f"  {label:12}: " + ", ".join(f"{k}={v}" for k, v in c.most_common(8)))
        print("sample bios:")
        for p in (ps[0], ps[len(ps) // 2], ps[-1]):
            print(f"  - {p.bio}")
        return 0

    if a.cmd == "poll":
        options = [o.strip() for o in a.options.split(",")] if a.options else DEFAULT_OPTIONS
        run = _mk_run(a)
        rows = run.poll(a.question, options, _events(a))
        mode = a.weight or run.rcfg.get("weight_mode", "seats")
        res = aggregate.aggregate_poll(rows, options, mode)
        meta = {"question": a.question, "as_of": run.rcfg["as_of_date"],
                "n_personas": len(run.personas), "engine_line": run.stats_line()}
        d = write_run(a.out, "poll", meta, rows, res, report.render_poll(meta, res, rows))
        print(f"top2={res['top2']:.3f} sentiment={res['mean_sentiment']:+.2f}  -> {d}/report.md")
        return 0

    if a.cmd == "feature-test":
        spec = Path(a.scenario).read_text()
        run = _mk_run(a)
        rows = run.feature_test(spec, _events(a))
        mode = a.weight or run.rcfg.get("weight_mode", "seats")
        res = aggregate.aggregate_feature(rows, mode)
        title = next((ln.lstrip("# ").strip() for ln in spec.splitlines()
                      if ln.startswith("#")), a.scenario)
        meta = {"scenario": title, "scenario_file": a.scenario, "as_of": run.rcfg["as_of_date"],
                "n_personas": len(run.personas), "engine_line": run.stats_line()}
        d = write_run(a.out, "feature", meta, rows, res, report.render_feature(meta, res, rows))
        print(f"intent_top2={res['intent_top2']:.3f} excitement={res['excitement']:+.2f} "
              f"fit={res['workflow_fit']:.2f}  -> {d}/report.md")
        return 0

    if a.cmd == "validate":
        engine = "prior" if a.smoke else a.engine
        scale = 0.5 if a.smoke else a.n_scale
        return run_validate(a.rubric, a.config_dir, a.panel, engine, scale, a.weight)

    return 2


if __name__ == "__main__":
    sys.exit(main())
