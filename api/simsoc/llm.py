"""Model access. Two engines:
- anthropic: live calls through the Anthropic API, wrapped in a sqlite cache keyed by
  sha256(model|system|user|max_tokens) — the simfrancisco pattern that makes clean-mode
  re-runs byte-reproducible and free.
- prior: a no-API heuristic that generates schema-valid answers from the archetype's
  stance vector alone. It ignores question semantics by design; use it as an offline
  pipeline test and as a null baseline ("does the LLM beat the priors?")."""

from __future__ import annotations
import hashlib
import json
import os
import random
import re
import sqlite3
import threading
import time


class Cache:
    """Thread-safe, best-effort. The runner fans out over a thread pool, so every
    touch of the shared connection is serialized behind a lock; any sqlite error
    degrades to a cache miss / dropped write instead of killing the run (an
    unlocked shared connection under concurrency raises SQLITE_MISUSE:
    "bad parameter or other API misuse")."""

    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        with self._lock, self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT)")
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(model: str, system: str, user: str, max_tokens: int) -> str:
        h = hashlib.sha256()
        for part in (model, system, user, str(max_tokens)):
            h.update(part.encode())
            h.update(b"|")
        return h.hexdigest()

    def get(self, k: str) -> str | None:
        try:
            with self._lock:
                row = self.db.execute("SELECT v FROM cache WHERE k=?", (k,)).fetchone()
        except sqlite3.Error:
            row = None
        if row:
            self.hits += 1
            return row[0]
        self.misses += 1
        return None

    def put(self, k: str, v: str) -> None:
        try:
            with self._lock, self.db:
                self.db.execute("INSERT OR REPLACE INTO cache (k, v) VALUES (?, ?)", (k, v))
        except sqlite3.Error:
            pass


class AnthropicEngine:
    def __init__(self, model: str, cache: Cache, max_tokens: int = 1400, temperature: float = 0.7):
        from anthropic import Anthropic  # lazy: prior engine needs no SDK
        self.client = Anthropic()        # reads ANTHROPIC_API_KEY
        self.model, self.cache = model, cache
        self.max_tokens, self.temperature = max_tokens, temperature
        self.live_calls = 0

    def complete(self, system: str, user: str) -> str:
        k = Cache.key(self.model, system, user, self.max_tokens)
        hit = self.cache.get(k)
        if hit is not None:
            return hit
        last = None
        for attempt in range(4):
            try:
                resp = self.client.messages.create(
                    model=self.model, max_tokens=self.max_tokens, temperature=self.temperature,
                    system=system, messages=[{"role": "user", "content": user}],
                )
                text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
                self.live_calls += 1
                self.cache.put(k, text)
                return text
            except Exception as e:  # rate limits / transient
                last = e
                time.sleep(2 * (2 ** attempt))
        raise RuntimeError(f"anthropic call failed after retries: {last}")


def extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON object found")
    return json.loads(m.group(0))


def normalize_shares(d: dict | None, keys: list[str]) -> dict[str, float]:
    d = d or {}
    out = {k: max(0.0, float(d.get(k, 0.0) or 0.0)) for k in keys}
    s = sum(out.values())
    if s <= 0:
        return {k: 1.0 / len(keys) for k in keys}
    return {k: v / s for k, v in out.items()}


# ---------------------------------------------------------------- prior engine

_OBJECTION_BANK = [
    ("data_cost_sensitivity", "ingest / per-action metering pricing"),
    ("budget_pressure", "no budget line without displacing something"),
    ("change_fatigue", "no appetite for another migration/rollout this year"),
    ("compliance_burden", "audit and change-control questions unanswered"),
    ("microsoft_gravity", "we already pay Microsoft for something like this in E5"),
    ("outage_scar", "single-vendor concentration risk after July 2024"),
    ("staffing_pain", "no one has cycles to evaluate, let alone operate it"),
    ("channel_reliance", "our MSP decides this, not us"),
]


def _rng_for(system: str, user: str) -> random.Random:
    h = hashlib.sha256((system + user).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "little"))


def _discrete_normal(rng: random.Random, center: float, keys: list[str]) -> dict[str, float]:
    out = {}
    for i, k in enumerate(keys, start=1):
        out[k] = math_exp(-((i - center) ** 2) / 1.6) + rng.uniform(0, 0.05)
    s = sum(out.values())
    return {k: v / s for k, v in out.items()}


def math_exp(x: float) -> float:
    import math
    return math.exp(x)


class PriorEngine:
    """Stance-vector heuristic. Requires the archetype stance dict at call time."""

    def __init__(self):
        self.live_calls = 0

    def poll(self, system: str, user: str, stance: dict, options: list[str]) -> dict:
        rng = _rng_for(system, user)
        lean = 0.45 * stance.get("crowdstrike_affinity", .5) + 0.30 * stance.get("ai_trust", .5) \
            + 0.25 * (1 - stance.get("budget_pressure", .5))
        center = 1 + lean * (len(options) - 1)
        shares = _discrete_normal(rng, center, options)
        return {"option_shares": shares,
                "mean_sentiment": round((lean - 0.5) * 3.2, 2),
                "themes": [t for _, t in sorted(
                    _OBJECTION_BANK, key=lambda kv: -stance.get(kv[0], 0))[:3]],
                "verbatims": [
                    {"role": "prior-engine baseline", "quote": "Heuristic answer from stance priors; no question semantics."},
                    {"role": "prior-engine baseline", "quote": "Use --engine anthropic for real simulated opinions."}]}

    def feature(self, system: str, user: str, stance: dict) -> dict:
        rng = _rng_for(system, user)
        intent = 1 + 4 * (0.28 * stance.get("automation_appetite", .5)
                          + 0.26 * stance.get("crowdstrike_affinity", .5)
                          + 0.18 * (1 - stance.get("change_fatigue", .5))
                          + 0.16 * (1 - stance.get("budget_pressure", .5))
                          + 0.12 * (1 - stance.get("microsoft_gravity", .5)))
        keys5 = ["1_definitely_not", "2_unlikely", "3_maybe", "4_likely", "5_definitely"]
        objections = [t for _, t in sorted(_OBJECTION_BANK, key=lambda kv: -stance.get(kv[0], 0))[:4]]
        pull = "microsoft" if stance.get("microsoft_gravity", 0) > 0.55 else "none"
        return {"adoption_intent_shares": _discrete_normal(rng, intent, keys5),
                "excitement": round((intent - 3) * 0.8, 2),
                "workflow_fit": round(1 + 4 * stance.get("automation_appetite", .5), 1),
                "top_objections": objections,
                "budget_source_shares": normalize_shares(
                    {"existing_flex": stance.get("crowdstrike_affinity", .3) * .6,
                     "new_budget": .15, "displace_another_tool": .2,
                     "no_budget_path": stance.get("budget_pressure", .5) * .7},
                    ["existing_flex", "new_budget", "displace_another_tool", "no_budget_path"]),
                "competitive_pull": pull,
                "expected_timeline_shares": _discrete_normal(
                    rng, 1 + (5 - intent),
                    ["pilot_this_quarter", "within_6_months", "at_next_renewal", "unlikely_ever"]),
                "questions_for_vendor": ["pricing model?", "human-in-the-loop controls?", "audit trail?"],
                "verbatims": [
                    {"role": "prior-engine baseline", "quote": "Heuristic answer from stance priors only."},
                    {"role": "prior-engine baseline", "quote": "Use --engine anthropic for real simulated opinions."}]}
