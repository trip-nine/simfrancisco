"""Weighted aggregation (the PWGTP post-stratification, re-weighted for firms/seats)
plus segment crosstabs by tier, CrowdStrike relationship, role family, and E5 status."""

from __future__ import annotations
from collections import Counter, defaultdict

from .runner import BUDGET_KEYS, INTENT_KEYS, PULL_KEYS, TIMELINE_KEYS

SEG_FIELDS = ["tier", "cs_relationship", "family", "e5_bucket"]


def _w(row: dict, mode: str) -> float:
    return row["sw"] if mode == "seats" else row["fw"]


def _wshares(rows, field, keys, mode) -> dict[str, float]:
    tot = sum(_w(r, mode) for r in rows) or 1.0
    return {k: round(sum(_w(r, mode) * r["data"][field].get(k, 0.0) for r in rows) / tot, 4)
            for k in keys}


def _wmean(rows, field, mode) -> float:
    tot = sum(_w(r, mode) for r in rows) or 1.0
    return round(sum(_w(r, mode) * float(r["data"].get(field, 0.0)) for r in rows) / tot, 3)


def _wtally(rows, field, mode, top=8) -> list[tuple[str, float]]:
    c: Counter = Counter()
    orig: dict[str, str] = {}
    for r in rows:
        for item in r["data"].get(field, []) or []:
            k = str(item).strip().lower()[:90]
            if not k:
                continue
            c[k] += _w(r, mode)
            orig.setdefault(k, str(item).strip())
    tot = sum(c.values()) or 1.0
    return [(orig[k], round(v / tot, 3)) for k, v in c.most_common(top)]


def _segments(rows, mode, metric_fn) -> dict:
    out = {}
    total_w = sum(_w(r, mode) for r in rows) or 1.0
    for field in SEG_FIELDS:
        buckets = defaultdict(list)
        for r in rows:
            buckets[r[field]].append(r)
        out[field] = {
            val: {"weight_share": round(sum(_w(r, mode) for r in rs) / total_w, 3),
                  "units": len(rs), **metric_fn(rs)}
            for val, rs in sorted(buckets.items())
        }
    return out


def top2(shares: dict, keys: list[str]) -> float:
    return round(shares.get(keys[-1], 0) + shares.get(keys[-2], 0), 4)


def aggregate_poll(rows: list[dict], options: list[str], mode: str) -> dict:
    def metric(rs):
        sh = _wshares(rs, "option_shares", options, mode)
        return {"top2": top2(sh, options), "mean_sentiment": _wmean(rs, "mean_sentiment", mode)}
    shares = _wshares(rows, "option_shares", options, mode)
    return {
        "kind": "poll", "weight_mode": mode, "options": options,
        "option_shares": shares, "top2": top2(shares, options),
        "mean_sentiment": _wmean(rows, "mean_sentiment", mode),
        "themes": _wtally(rows, "themes", mode),
        "segments": _segments(rows, mode, metric),
    }


def aggregate_feature(rows: list[dict], mode: str) -> dict:
    def metric(rs):
        sh = _wshares(rs, "adoption_intent_shares", INTENT_KEYS, mode)
        return {"intent_top2": top2(sh, INTENT_KEYS),
                "excitement": _wmean(rs, "excitement", mode),
                "workflow_fit": _wmean(rs, "workflow_fit", mode)}
    intent = _wshares(rows, "adoption_intent_shares", INTENT_KEYS, mode)
    tot = sum(_w(r, mode) for r in rows) or 1.0
    pull = {k: round(sum(_w(r, mode) for r in rows if r["data"]["competitive_pull"] == k) / tot, 3)
            for k in PULL_KEYS}
    return {
        "kind": "feature", "weight_mode": mode,
        "adoption_intent_shares": intent, "intent_top2": top2(intent, INTENT_KEYS),
        "excitement": _wmean(rows, "excitement", mode),
        "workflow_fit": _wmean(rows, "workflow_fit", mode),
        "budget_source_shares": _wshares(rows, "budget_source_shares", BUDGET_KEYS, mode),
        "expected_timeline_shares": _wshares(rows, "expected_timeline_shares", TIMELINE_KEYS, mode),
        "competitive_pull": pull,
        "top_objections": _wtally(rows, "top_objections", mode),
        "questions_for_vendor": _wtally(rows, "questions_for_vendor", mode, top=6),
        "segments": _segments(rows, mode, metric),
    }


def collect_verbatims(rows: list[dict], limit: int = 12) -> list[dict]:
    seen, out = set(), []
    ordered = sorted(rows, key=lambda r: -r["sw"])
    tiers = []
    for r in ordered:                     # round-robin by tier for diversity
        tiers.append(r) if r["tier"] not in {x["tier"] for x in tiers} else None
    pool = tiers + [r for r in ordered if r not in tiers]
    for r in pool:
        for v in r["data"].get("verbatims", []) or []:
            q = (v.get("quote") or "").strip()
            if q and q not in seen:
                seen.add(q)
                out.append({"segment": r["key"], "role": v.get("role", ""), "quote": q})
            if len(out) >= limit:
                return out
    return out
