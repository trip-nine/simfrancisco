"""Rubric-driven validation, ported from simfrancisco's `validate` binary.
Checks come in two flavors:
  - target:    a metric on (optionally filtered) rows vs an external ground-truth
               number (your win/loss, NPS, ETR net score, attach rates), scored by
               max_abs_err.
  - direction: an internal/external consistency check comparing a metric between
               two segments (e.g., E5 shops should show more Microsoft pull).
Exit code 0 iff weighted score >= thresholds.weighted_score_min."""

from __future__ import annotations
from pathlib import Path

import yaml

from .aggregate import INTENT_KEYS, _wmean, _wshares, top2
from .runner import Run

DEFAULT_OPTIONS = ["oppose", "lean_oppose", "neutral", "lean_support", "support"]


def _metric(rows: list[dict], field: str, mode: str, options: list[str]) -> float:
    if not rows:
        return float("nan")
    if field == "intent_top2":
        return top2(_wshares(rows, "adoption_intent_shares", INTENT_KEYS, mode), INTENT_KEYS)
    if field == "top2":
        return top2(_wshares(rows, "option_shares", options, mode), options)
    if field in ("mean_sentiment", "excitement", "workflow_fit"):
        return _wmean(rows, field, mode)
    raise ValueError(f"unknown field {field}")


def _filter(rows: list[dict], flt: dict | None) -> list[dict]:
    if not flt:
        return rows
    return [r for r in rows if all(r.get(k) == v for k, v in flt.items())]


def run_validate(rubric_path: str, config_dir: str, panel_path: str | None,
                 engine: str | None, scale: float, weight_mode: str | None) -> int:
    rub = yaml.safe_load(Path(rubric_path).read_text())
    thresholds = rub.get("thresholds", {})
    mode = weight_mode or rub.get("meta", {}).get("weight_mode", "seats")
    cache_rows: dict[str, list[dict]] = {}
    total_w, score_w = 0.0, 0.0
    print(f"simsoc validate — rubric={rubric_path} engine={engine or 'config default'} "
          f"scale={scale} weight={mode}\n" + "-" * 72)
    for chk in rub.get("checks", []):
        run = Run(config_dir=config_dir, panel_path=panel_path, scale=scale,
                  engine=engine, run_tag=f"validate:{chk['id']}")
        ckey = chk.get("scenario") or chk.get("question")
        if ckey not in cache_rows:
            if chk["kind"] == "feature":
                spec = Path(chk["scenario"]).read_text()
                cache_rows[ckey] = run.feature_test(spec, [])
            else:
                opts = chk.get("options", DEFAULT_OPTIONS)
                cache_rows[ckey] = run.poll(chk["question"], opts, [])
        rows = cache_rows[ckey]
        opts = chk.get("options", DEFAULT_OPTIONS)
        w = float(chk.get("weight", 1.0))
        if "target" in chk:
            val = _metric(_filter(rows, chk.get("segment")), chk["field"], mode, opts)
            err = abs(val - float(chk["target"]))
            mx = float(chk.get("max_abs_err", 0.1))
            s = 1.0 if err <= mx else max(0.0, 1.0 - (err - mx) / mx)
            print(f"[target   ] {chk['id']:<38} value={val:.3f} target={chk['target']} "
                  f"abs_err={err:.3f} score={s:.2f}")
        else:
            d = chk["direction"]
            va = _metric(_filter(rows, d["a"]), chk["field"], mode, opts)
            vb = _metric(_filter(rows, d["b"]), chk["field"], mode, opts)
            ok = va > vb if d["expect"] == "a>b" else va < vb
            s = 1.0 if ok else 0.0
            print(f"[direction] {chk['id']:<38} a={va:.3f} b={vb:.3f} expect {d['expect']} "
                  f"-> {'PASS' if ok else 'FAIL'}")
        total_w += w
        score_w += w * s
    headline = score_w / total_w if total_w else 0.0
    gate = float(thresholds.get("weighted_score_min", 0.7))
    print("-" * 72 + f"\nweighted score = {headline:.3f} (gate {gate}) -> "
          + ("GREEN" if headline >= gate else "RED"))
    return 0 if headline >= gate else 1
