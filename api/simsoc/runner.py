"""The run pipeline shared by poll and feature-test:
panel -> archetypes (or single personas) -> run-time pressures -> prompts ->
engine (concurrent, cached) -> parsed + normalized rows ready for aggregation."""

from __future__ import annotations
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import archetypes as arch
from . import prompts
from .llm import AnthropicEngine, Cache, PriorEngine, extract_json, normalize_shares
from .sampler import build_panel, load_configs, sample_pressures
from .schema import Persona, load_panel

INTENT_KEYS = ["1_definitely_not", "2_unlikely", "3_maybe", "4_likely", "5_definitely"]
BUDGET_KEYS = ["existing_flex", "new_budget", "displace_another_tool", "no_budget_path"]
TIMELINE_KEYS = ["pilot_this_quarter", "within_6_months", "at_next_renewal", "unlikely_ever"]
PULL_KEYS = ["none", "microsoft", "palo_alto", "sentinelone", "google", "other"]


class Run:
    def __init__(self, config_dir: str = "config", panel_path: str | None = None,
                 seed: int | None = None, scale: float = 1.0, **overrides):
        self.cfg = load_configs(config_dir)
        rcfg = dict(self.cfg["panel"]["run"])
        rcfg.update({k: v for k, v in overrides.items() if v is not None})
        self.rcfg = rcfg
        if panel_path and Path(panel_path).exists():
            self.personas = load_panel(panel_path)
        else:
            self.personas = build_panel(config_dir, seed=seed, scale=scale)
        self.mode = rcfg.get("mode", "archetype")
        self.engine_name = rcfg.get("engine", "anthropic")
        self.cache = Cache(rcfg.get("cache_db", "runs/cache.db"))
        if self.engine_name == "anthropic":
            self.engine = AnthropicEngine(rcfg["model"], self.cache,
                                          rcfg.get("max_tokens", 1400),
                                          rcfg.get("temperature", 0.7))
        else:
            self.engine = PriorEngine()

    # ------------------------------------------------------------ units

    def _units(self) -> list[dict]:
        """A unit = one LLM call: an archetype (default) or one persona."""
        run_seed = f"{self.rcfg['as_of_date']}|{self.rcfg.get('run_tag', '')}"
        k = self.rcfg.get("pressures_per_persona", 2)
        by_id = {p.id: p for p in self.personas}
        units = []
        if self.mode == "persona":
            for p in self.personas:
                pres = sample_pressures(self.cfg, p, run_seed, k)
                card = (f"YOU ARE ONE PERSON (answer as an individual; put ~1.0 of each share "
                        f"on your single choice):\n{p.bio}\nStance vector: "
                        + "; ".join(f"{d}={v}" for d, v in p.stance.items())
                        + "\nYour workload this week: " + " / ".join(pres))
                units.append(self._unit(p.id, [p], card))
        else:
            for a in arch.build_archetypes(self.personas, self.rcfg.get("max_archetypes", 48)):
                ps = [by_id[i] for i in a.persona_ids]
                counts = Counter(t for p in ps for t in sample_pressures(self.cfg, p, run_seed, k))
                pres = [t for t, _ in counts.most_common(3)]
                units.append(self._unit(a.key, ps, arch.render_card(a, pres),
                                        fw=a.firm_weight, sw=a.seat_weight,
                                        seg=dict(tier=a.tier, cs_relationship=a.cs_relationship,
                                                 family=a.family, e5_bucket=a.e5_bucket)))
        return units

    @staticmethod
    def _unit(key: str, ps: list[Persona], card: str, fw=None, sw=None, seg=None) -> dict:
        p = ps[0]
        stance = {d: sum(q.stance[d] for q in ps) / len(ps) for d in p.stance}
        return {"key": key, "card": card, "n": len(ps), "stance": stance,
                "fw": fw if fw is not None else p.firm_weight,
                "sw": sw if sw is not None else p.seat_weight,
                "seg": seg or dict(tier=p.tier, cs_relationship=p.cs_relationship,
                                   family=p.family, e5_bucket="e5" if p.e5 else "non_e5")}

    # ------------------------------------------------------------ execution

    def _execute(self, kind: str, build_user, prior_call, events: list[str]) -> list[dict]:
        ctx = "" if self.rcfg.get("no_context") else self.cfg["market_context"]
        system = prompts.build_system(self.rcfg["as_of_date"], ctx, events)
        units = self._units()

        def one(u: dict) -> dict:
            user = build_user(u["card"])
            if self.engine_name == "prior":
                data = prior_call(system, user, u["stance"])
            else:
                data = self._ask_json(system, user)
            return {**{k: u[k] for k in ("key", "n", "fw", "sw")}, **u["seg"], "data": data}

        workers = self.rcfg.get("concurrency", 4) if self.engine_name == "anthropic" else 8
        with ThreadPoolExecutor(max_workers=workers) as ex:
            rows = list(ex.map(one, units))
        for r in rows:
            self._normalize(kind, r["data"])
        return rows

    def _ask_json(self, system: str, user: str) -> dict:
        text = self.engine.complete(system, user)
        try:
            return extract_json(text)
        except Exception:
            text = self.engine.complete(
                system, user + "\n\nREMINDER: your entire reply must be one valid JSON object.")
            return extract_json(text)

    def _normalize(self, kind: str, d: dict) -> None:
        if kind == "poll":
            d["option_shares"] = normalize_shares(d.get("option_shares"), self._options)
            d["mean_sentiment"] = float(d.get("mean_sentiment", 0) or 0)
            d.setdefault("themes", [])
            d.setdefault("verbatims", [])
        else:
            d["adoption_intent_shares"] = normalize_shares(d.get("adoption_intent_shares"), INTENT_KEYS)
            d["budget_source_shares"] = normalize_shares(d.get("budget_source_shares"), BUDGET_KEYS)
            d["expected_timeline_shares"] = normalize_shares(d.get("expected_timeline_shares"), TIMELINE_KEYS)
            d["excitement"] = float(d.get("excitement", 0) or 0)
            d["workflow_fit"] = float(d.get("workflow_fit", 3) or 3)
            if d.get("competitive_pull") not in PULL_KEYS:
                d["competitive_pull"] = "other"
            d.setdefault("top_objections", [])
            d.setdefault("questions_for_vendor", [])
            d.setdefault("verbatims", [])

    # ------------------------------------------------------------ public API

    def poll(self, question: str, options: list[str], events: list[str]) -> list[dict]:
        self._options = options
        return self._execute(
            "poll",
            lambda card: prompts.build_poll_user(card, question, options),
            lambda s, u, st: self.engine.poll(s, u, st, options),
            events)

    def feature_test(self, spec: str, events: list[str]) -> list[dict]:
        return self._execute(
            "feature",
            lambda card: prompts.build_feature_user(card, spec),
            lambda s, u, st: self.engine.feature(s, u, st),
            events)

    def stats_line(self) -> str:
        live = getattr(self.engine, "live_calls", 0)
        return (f"engine={self.engine_name} model={self.rcfg.get('model')} mode={self.mode} "
                f"units={'persona' if self.mode == 'persona' else 'archetype'} "
                f"live_calls={live} cache_hits={self.cache.hits}")


def write_run(out_dir: str, kind: str, meta: dict, rows: list[dict], results: dict, report_md: str) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    d = Path(out_dir) / f"{ts}-{kind}"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "raw.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    with open(d / "results.json", "w") as f:
        json.dump({"meta": meta, "results": results}, f, indent=1)
    (d / "report.md").write_text(report_md)
    return str(d)
