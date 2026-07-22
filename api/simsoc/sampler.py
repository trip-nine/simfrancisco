"""Seeded, deterministic firm + persona sampling from firmographic priors.
Seed discipline is ported from simfrancisco: slot_seed = sha256(seed|tier|i),
so panels are byte-reproducible and cheap (zero LLM calls to build)."""

from __future__ import annotations
import hashlib
import math
import random
from pathlib import Path

import yaml

from .schema import Persona

TIER_ORDER = ["t1_f500", "t2_large", "t3_upper_mid", "t4_mid", "t5_smb", "t6_small"]
STANCE_GLOSS = {
    "budget_pressure": "cost scrutiny",
    "consolidation_preference": "platform-over-best-of-breed",
    "microsoft_gravity": "pull toward Microsoft stack",
    "ai_trust": "trust in AI/agentic claims",
    "automation_appetite": "willingness to let agents act",
    "crowdstrike_affinity": "CrowdStrike affinity",
    "outage_scar": "July-2024 outage scar tissue",
    "data_cost_sensitivity": "SIEM/ingest cost pain",
    "staffing_pain": "understaffing pain",
    "change_fatigue": "migration/change fatigue",
    "compliance_burden": "regulatory burden",
    "channel_reliance": "MSP/MSSP reliance",
    "risk_tolerance": "risk tolerance",
}


def _slot_rng(seed: int, tier: str, i: int) -> random.Random:
    h = hashlib.sha256(f"{seed}|{tier}|{i}".encode()).digest()
    return random.Random(int.from_bytes(h[:8], "little"))


def _wchoice(rng: random.Random, table: dict[str, float]) -> str:
    keys, weights = list(table.keys()), list(table.values())
    return rng.choices(keys, weights=weights, k=1)[0]


def _clamp(x: float) -> float:
    return round(min(0.98, max(0.02, x)), 2)


def load_configs(config_dir: str) -> dict:
    cfg = {}
    for name in ["firmographics", "roles", "tooling", "pressures", "panel"]:
        with open(Path(config_dir) / f"{name}.yaml") as f:
            cfg[name] = yaml.safe_load(f)
    cfg["market_context"] = (Path(config_dir) / "market_context.md").read_text()
    intl = Path(config_dir) / "international.yaml"
    if intl.exists():
        with open(intl) as f:
            cfg["international"] = yaml.safe_load(f)
    return cfg


def _sample_role(rng: random.Random, roles: dict, tier: str) -> tuple[str, dict]:
    avail = {k: v["weights"][tier] for k, v in roles.items() if tier in v.get("weights", {})}
    key = _wchoice(rng, avail)
    return key, roles[key]


def _stance(rng, tier_i, ind_meta, p: Persona) -> dict[str, float]:
    n = lambda s=0.08: rng.gauss(0, s)
    tier_small = tier_i / 5.0                      # 0=t1 .. 1=t6
    staffing = _clamp(0.35 + 0.35 * tier_small + (0.15 if p.security_team_size <= 1 else 0.0)
                      + (0.10 if p.budget_trend == "cut" else 0.0) + n())
    compliance = _clamp(ind_meta["compliance"] * (0.72 + 0.28 * (1 - tier_small)) + n(0.06))
    ai_trust = _clamp(0.50 + (0.12 if p.maturity in ("defined", "optimized") else -0.05)
                      - 0.10 * compliance + n(0.12))
    was_cs_by_2024 = (p.cs_relationship == "customer" and p.cs_tenure_years >= 2) or p.cs_relationship == "churned"
    scar = ind_meta["outage_scar"] * (rng.uniform(0.55, 1.0) if was_cs_by_2024 else rng.uniform(0.10, 0.35))
    affinity = {"customer": 0.55 + 0.025 * p.cs_tenure_years + 0.02 * len(p.modules),
                "prospect": 0.45, "none": 0.33, "churned": 0.15}[p.cs_relationship]
    s = {
        "budget_pressure": _clamp(0.42 + 0.22 * tier_small
                                  + {"cut": 0.20, "flat": 0.05, "up": -0.12}[p.budget_trend] + n()),
        "consolidation_preference": _clamp(0.52 + (0.12 if p.falcon_flex else 0.0)
                                           + (0.10 if p.budget_trend == "cut" else 0.0) + n(0.12)),
        "microsoft_gravity": _clamp(0.22 + (0.42 if p.e5 else 0.0)
                                    + (0.14 if p.siem == "ms_sentinel" else 0.0)
                                    + (0.10 if p.primary_edr == "microsoft_defender" else 0.0) + n(0.06)),
        "ai_trust": ai_trust,
        "automation_appetite": _clamp(0.32 + 0.30 * staffing + 0.18 * ai_trust - 0.18 * compliance + n()),
        "crowdstrike_affinity": _clamp(affinity - 0.25 * scar + n(0.06)),
        "outage_scar": _clamp(scar),
        "data_cost_sensitivity": _clamp(0.38 + (0.30 if p.siem == "splunk" else 0.0)
                                        + (0.16 if p.siem == "ms_sentinel" else 0.0)
                                        + 0.10 * (1 - tier_small) + n(0.07)),
        "staffing_pain": staffing,
        "change_fatigue": _clamp(0.34 + 0.30 * rng.random() + (0.12 if p.cs_relationship == "churned" else 0.0)),
        "compliance_burden": compliance,
        "channel_reliance": _clamp((0.92 if p.family == "channel" else 0.18)
                                   + (0.45 if p.mdr_partner not in ("in_house",) and p.family != "channel" else 0.0)
                                   + 0.10 * tier_small + n(0.05)),
        "risk_tolerance": _clamp(0.50 - 0.20 * compliance + n(0.12)),
    }
    return s


REGION_LABELS = {"northeast": "Northeast US", "southeast": "Southeast US",
                 "midwest": "Midwest US", "southwest": "Southwest US", "west": "Western US",
                 "emea": "EMEA", "apac": "APAC", "americas_x": "Americas ex-US"}
GEO_NOTES = {
    "emea": "GDPR/NIS2 and the EU AI Act govern the stack; AI tooling clears DPO and works-council review before rollout",
    "apac": "APAC operation: a regional MSSP delivers most hands-on coverage and data-residency rules shape tooling choices",
    "americas_x": "Americas ex-US operation: PIPEDA/LGPD obligations and FX pressure on USD-priced tooling shape the budget",
}


def _bio(p: Persona) -> str:
    stack = []
    if p.cs_relationship == "customer":
        mods = ", ".join(p.modules[:6]) + ("…" if len(p.modules) > 6 else "")
        stack.append(f"Falcon customer ~{p.cs_tenure_years}y ({mods})" + ("; Falcon Flex wallet" if p.falcon_flex else ""))
        if p.sku_note:
            stack.append(p.sku_note)
    elif p.cs_relationship == "churned":
        stack.append(f"former Falcon customer, displaced to {p.primary_edr}")
    else:
        stack.append(f"runs {p.primary_edr} for endpoint ({p.cs_relationship} for CrowdStrike)")
    stack.append(f"SIEM: {p.siem}; identity: {p.identity_stack}; MDR: {p.mdr_partner}")
    if p.e5:
        stack.append("Microsoft 365 E5/E7 licensed (Security Copilot bundled)")
    return (f"{p.title} at a ~{p.employees:,}-employee {p.industry.replace('_',' ')} firm "
            f"({REGION_LABELS.get(p.region, p.region)}, {p.tier_label}); security team of {p.security_team_size}; "
            f"maturity {p.maturity}; budget {p.budget_trend}; renewal {p.renewal_quarter}. "
            + " | ".join(stack)
            + (f". {GEO_NOTES[p.region]}" if p.region in GEO_NOTES else "")
            + f". Day-to-day: {p.day_to_day}.")


def _rel_allocation(q: int, weights: list[float], rng: random.Random) -> list[str]:
    """Stratified relationship allocation: largest-remainder seats per class, so
    every class with non-trivial prior mass is represented even at small tier
    quotas (a 2.5% customer prior over 36 slots must yield one customer, not
    whatever an independent coin flip happens to produce). Order is shuffled
    deterministically so relationship doesn't correlate with slot index."""
    rels = ["customer", "prospect", "churned", "none"]
    total = sum(weights) or 1.0
    exact = [q * w / total for w in weights]
    seats = [int(x) for x in exact]
    for k in sorted(range(len(rels)), key=lambda k: exact[k] - seats[k], reverse=True):
        if sum(seats) >= q:
            break
        seats[k] += 1
    while sum(seats) > q:  # defensive; floors can't exceed q
        seats[seats.index(max(seats))] -= 1
    # The customer class anchors the worldwide-customer calibration; at tiny
    # quotas largest-remainder can starve it (0.015 prior x 3 slots -> 0 seats),
    # which silently drops that cell's entire customer mass in post-strat.
    # Guarantee it one seat (stolen from the largest class) whenever its prior
    # is non-trivial; post-stratification then scales its weight to the true
    # tiny target, so representation is guaranteed and calibration exact.
    if q >= 2 and weights[0] / total > 0.005 and seats[0] == 0:
        seats[seats.index(max(seats))] -= 1
        seats[0] += 1
    out = [r for r, n in zip(rels, seats) for _ in range(n)]
    rng.shuffle(out)
    return out


def build_panel(config_dir: str, seed: int | None = None,
                quotas: dict[str, int] | None = None, scale: float = 1.0) -> list[Persona]:
    cfg = load_configs(config_dir)
    firmo, tooling, roles = cfg["firmographics"], cfg["tooling"], cfg["roles"]["roles"]
    intl = cfg.get("international")
    seed = cfg["panel"]["panel"]["seed"] if seed is None else seed
    quotas = quotas or {t: max(2, int(round(q * scale)))
                        for t, q in cfg["panel"]["panel"]["quotas"].items()}
    personas: list[Persona] = []

    def make(pid: str, rng: random.Random, tier: str, tier_i: int, tmeta: dict,
             fw: float, rel: str, region: str | None,
             e5_mult: float = 1.0, stance_delta: dict | None = None) -> None:
        ind = _wchoice(rng, {k: v["weight"] for k, v in firmo["industries"].items()})
        ind_meta = firmo["industries"][ind]
        lo, hi = tmeta["employees"]
        employees = int(math.exp(rng.uniform(math.log(lo), math.log(hi))))
        endpoints = int(employees * tmeta["endpoints_per_employee"] * rng.uniform(0.85, 1.15))
        tlo, thi = tmeta["security_team"]
        frac = (math.log(employees) - math.log(lo)) / max(1e-9, math.log(hi) - math.log(lo))
        team = int(round(tlo + frac * (thi - tlo) * rng.uniform(0.6, 1.3)))
        team = max(tlo, min(thi, team))

        if region is None:  # US path: draw here, preserving the original RNG order
            region = _wchoice(rng, firmo["regions"])
        p = Persona(
            id=pid, tier=tier, tier_label=tmeta["label"], industry=ind,
            region=region, employees=employees, endpoints=endpoints,
            security_team_size=team,
            maturity=rng.choices(["initial", "managed", "defined", "optimized"],
                                 weights=firmo["maturity"][tier])[0],
            budget_trend=rng.choices(["cut", "flat", "up"], weights=firmo["budget_trend"][tier])[0],
            cs_relationship=rel, cs_tenure_years=0,
            renewal_quarter=rng.choice(["Q3 2026", "Q4 2026", "Q1 2027", "Q2 2027"]),
            firm_weight=fw,
        )
        # --- tooling state ---
        p.e5 = rng.random() < tooling["microsoft"]["e5_or_e7_prob"][tier_i] * e5_mult
        if rel == "customer":
            p.cs_tenure_years = min(10, 1 + int(rng.expovariate(1 / (4.5 - 0.4 * tier_i)) )) if tier_i < 4 \
                else rng.randint(1, 5)
            p.modules = [m for m, meta in tooling["crowdstrike_modules"].items()
                         if rng.random() < meta["attach"][tier_i]]
            if "falcon_prevent_insight" not in p.modules:
                p.modules.insert(0, "falcon_prevent_insight")
            p.falcon_flex = rng.random() < tooling["falcon_flex_prob"][tier_i]
            p.primary_edr = "crowdstrike"
            if tier in ("t5_smb", "t6_small"):
                p.sku_note = rng.choice(["Falcon Go via e-commerce", "Falcon Pro", "Falcon via MSP bundle"])
        else:
            if rng.random() < tooling["microsoft"]["defender_primary_if_non_cs"][tier_i]:
                p.primary_edr = "microsoft_defender"
            else:
                shares = dict(tooling["competitor_edr_if_non_cs"])
                if tier in ("t5_smb", "t6_small"):
                    shares["huntress_managed"] *= 2.5
                    shares["palo_cortex"] *= 0.25
                    shares["trellix_or_legacy"] *= 0.4
                p.primary_edr = _wchoice(rng, shares)
        # SIEM
        if "ngsiem" in p.modules:
            p.siem = "crowdstrike_ngsiem"
        elif rng.random() < tooling["siem_incumbent"]["runs_siem_prob"][tier_i]:
            shares = dict(tooling["siem_incumbent"]["shares"])
            if p.e5:
                shares["ms_sentinel"] *= 1.7
            if rel != "customer":       # NG-SIEM without Falcon endpoint is rare
                shares["crowdstrike_ngsiem"] *= 0.08
            p.siem = _wchoice(rng, shares)
        # identity
        ishares = dict(tooling["identity_stack"]["shares"])
        if p.e5:
            ishares["entra_only"] *= 1.6
        p.identity_stack = _wchoice(rng, ishares)
        # MDR
        if "falcon_complete_mdr" in p.modules:
            p.mdr_partner = "falcon_complete"
        elif rng.random() < tooling["mdr_partner_smb"]["outsourced_prob"][tier_i]:
            p.mdr_partner = _wchoice(rng, tooling["mdr_partner_smb"]["shares"])
        # role, stance, bio, weights
        p.role_key, rmeta = _sample_role(rng, roles, tier)
        p.title, p.family = rmeta["title"], rmeta["family"]
        p.buying_role, p.day_to_day = rmeta["buying_role"], rmeta["day_to_day"]
        p.stance = _stance(rng, tier_i, ind_meta, p)
        if stance_delta:
            for k, d in stance_delta.items():
                p.stance[k] = min(1.0, max(0.0, p.stance.get(k, 0.5) + d))
        p.seat_weight = p.firm_weight * p.endpoints
        p.bio = _bio(p)
        personas.append(p)

    # --- US panel ---
    for tier_i, tier in enumerate(TIER_ORDER):
        tmeta = firmo["tiers"][tier]
        q = quotas.get(tier, 0)
        fw = tmeta["us_firms"] / max(q, 1)
        rel_alloc = _rel_allocation(q, tooling["crowdstrike_relationship"][tier],
                                    random.Random(f"{seed}|{tier}|rel"))
        for i in range(q):
            rng = _slot_rng(seed, tier, i)
            make(f"{tier}-{i:03d}", rng, tier, tier_i, tmeta, fw, rel_alloc[i], None)

    # --- international panel (config/international.yaml) ---
    if intl:
        for geo, g in intl["geos"].items():
            for tier_i, tier in enumerate(TIER_ORDER):
                tg = g["tiers"].get(tier)
                if not tg:
                    continue
                tmeta = firmo["tiers"][tier]
                q = tg["quota"]
                fw = tg["firms"] / max(q, 1)
                rel_alloc = _rel_allocation(q, g["crowdstrike_relationship"][tier],
                                            random.Random(f"{seed}|{geo}|{tier}|rel"))
                for i in range(q):
                    rng = _slot_rng(seed, f"{geo}:{tier}", i)
                    make(f"{geo}-{tier}-{i:02d}", rng, tier, tier_i, tmeta, fw,
                         rel_alloc[i], geo,
                         e5_mult=g.get("e5_mult", 1.0),
                         stance_delta=g.get("stance_delta"))

    # --- post-stratify weights to the relationship priors (per tier, per geo) ---
    # Small quotas wobble sampled shares +-25%; rescaling each relationship
    # class to its prior keeps the calibrated anchors exact (~26k US + ~14k
    # intl = ~40k CrowdStrike customers worldwide). Missing rare classes
    # redistribute proportionally.
    US_REGIONS = {"northeast", "southeast", "midwest", "southwest", "west"}
    rels = ["customer", "prospect", "churned", "none"]

    def _poststrat(sub: list[Persona], universe: float, weights: list[float]) -> None:
        if not sub:
            return
        prior = dict(zip(rels, weights))
        present = {p.cs_relationship for p in sub}
        # Present non-residual classes get their exact prior mass; any class
        # that sampled zero personas folds into "none" (the residual bucket),
        # never inflating customer/prospect/churned above their calibrated
        # priors.
        target = {}
        residual = universe
        for r in ("customer", "prospect", "churned"):
            if r in present:
                target[r] = universe * prior[r]
                residual -= target[r]
        if "none" in present:
            target["none"] = residual
        else:  # degenerate: no residual class sampled — spread proportionally
            norm = sum(prior[r] for r in present) or 1.0
            target = {r: universe * prior[r] / norm for r in present}
        for r in present:
            grp = [p for p in sub if p.cs_relationship == r]
            cur = sum(p.firm_weight for p in grp)
            sc = target[r] / cur if cur else 0.0
            for p in grp:
                p.firm_weight *= sc
                p.seat_weight = p.firm_weight * p.endpoints

    for tier, tmeta in firmo["tiers"].items():
        _poststrat([p for p in personas if p.tier == tier and p.region in US_REGIONS],
                   tmeta["us_firms"], tooling["crowdstrike_relationship"][tier])
    if intl:
        for geo, g in intl["geos"].items():
            for tier, tg in g["tiers"].items():
                _poststrat([p for p in personas if p.tier == tier and p.region == geo],
                           tg["firms"], g["crowdstrike_relationship"][tier])
    return personas


def sample_pressures(cfg: dict, persona: Persona, run_seed: str, k: int = 2) -> list[str]:
    """Run-time workflow pressure injection: deterministic per (run_seed, persona)."""
    h = hashlib.sha256(f"{run_seed}|{persona.id}".encode()).digest()
    rng = random.Random(int.from_bytes(h[:8], "little"))
    lib = cfg["pressures"]["pressures"]
    weights = {}
    for key, meta in lib.items():
        w = meta["weight"]
        w *= meta.get("family_bias", {}).get(persona.family, 1.0)
        w *= meta.get("tier_bias", {}).get(persona.tier, 1.0)
        if w > 0:
            weights[key] = w
    picks: list[str] = []
    pool = dict(weights)
    for _ in range(min(k, len(pool))):
        key = _wchoice(rng, pool)
        picks.append(lib[key]["text"])
        pool.pop(key)
    return picks
