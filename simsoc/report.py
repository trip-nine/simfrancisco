"""Markdown reports for runs/<id>/report.md."""

from __future__ import annotations
from .aggregate import SEG_FIELDS, collect_verbatims


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _shares_table(title: str, shares: dict) -> list[str]:
    lines = [f"### {title}", "", "| option | weighted share |", "|---|---|"]
    lines += [f"| {k} | {_pct(v)} |" for k, v in shares.items()]
    return lines + [""]


def _segments_block(res: dict, cols: list[tuple[str, str]]) -> list[str]:
    lines = ["## Segment cuts", ""]
    for field in SEG_FIELDS:
        lines += [f"### by {field}", "", "| segment | weight share | units | "
                  + " | ".join(c for c, _ in cols) + " |",
                  "|---|---|---|" + "---|" * len(cols)]
        for val, m in res["segments"][field].items():
            cells = " | ".join(_pct(m[k]) if k.endswith("2") else f"{m[k]:+.2f}" if "sentiment" in k or "excitement" in k
                               else f"{m[k]:.2f}" for _, k in cols)
            lines.append(f"| {val} | {_pct(m['weight_share'])} | {m['units']} | {cells} |")
        lines.append("")
    return lines


def _verbatims_block(rows: list[dict]) -> list[str]:
    lines = ["## Simulated verbatims (synthetic — not real customer quotes)", ""]
    for v in collect_verbatims(rows):
        lines.append(f"- **{v['role']}** ({v['segment']}): \u201c{v['quote']}\u201d")
    return lines + [""]


def _footer(meta: dict) -> list[str]:
    return ["---",
            "*Synthetic panel output. Personas are seeded simulations grounded in editable "
            "firmographic priors and a market-context digest; they are directional signal for "
            "hypothesis generation and message testing, not a substitute for real customer "
            "research. Calibrate against win/loss, NPS, and attach-rate ground truth via "
            "`rubric.yaml` before weighting decisions on segment-level numbers.*",
            "", f"*Run meta: {meta}*", ""]


def render_poll(meta: dict, res: dict, rows: list[dict]) -> str:
    lines = [f"# Poll — {meta['question']}", "",
             f"As-of **{meta['as_of']}** · weighting **{res['weight_mode']}** · "
             f"{meta['n_personas']} personas in {len(rows)} response units · {meta['engine_line']}", "",
             f"**Top-2 share: {_pct(res['top2'])}** · mean sentiment **{res['mean_sentiment']:+.2f}** (−2…+2)", ""]
    lines += _shares_table("Overall option shares", res["option_shares"])
    lines += ["### Dominant themes", ""] + [f"- {t} ({_pct(w)})" for t, w in res["themes"]] + [""]
    lines += _segments_block(res, [("top-2", "top2"), ("sentiment", "mean_sentiment")])
    lines += _verbatims_block(rows)
    lines += _footer(meta)
    return "\n".join(lines)


def render_feature(meta: dict, res: dict, rows: list[dict]) -> str:
    lines = [f"# Feature test — {meta['scenario']}", "",
             f"As-of **{meta['as_of']}** · weighting **{res['weight_mode']}** · "
             f"{meta['n_personas']} personas in {len(rows)} response units · {meta['engine_line']}", "",
             f"**Adoption intent top-2: {_pct(res['intent_top2'])}** · excitement "
             f"**{res['excitement']:+.2f}** (−2…+2) · workflow fit **{res['workflow_fit']:.2f}**/5", ""]
    lines += _shares_table("Adoption intent", res["adoption_intent_shares"])
    lines += _shares_table("Budget source", res["budget_source_shares"])
    lines += _shares_table("Expected timeline", res["expected_timeline_shares"])
    lines += _shares_table("Competitive pull (where hesitant demand leaks)", res["competitive_pull"])
    lines += ["### Top objections (weighted)", ""] + \
             [f"- {t} ({_pct(w)})" for t, w in res["top_objections"]] + [""]
    lines += ["### Questions the panel would ask the vendor", ""] + \
             [f"- {t}" for t, _ in res["questions_for_vendor"]] + [""]
    lines += _segments_block(res, [("intent top-2", "intent_top2"),
                                   ("excitement", "excitement"), ("fit", "workflow_fit")])
    lines += _verbatims_block(rows)
    lines += _footer(meta)
    return "\n".join(lines)
