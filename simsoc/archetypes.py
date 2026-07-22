"""Cluster personas into archetypes so one LLM call answers for a weighted cluster,
then post-stratify — simfrancisco's cost-control pattern, re-keyed for infosec:
(tier, CrowdStrike relationship, role family, E5 status)."""

from __future__ import annotations
from collections import Counter, defaultdict
from statistics import median, mean

from .sampler import STANCE_GLOSS
from .schema import Archetype, Persona


def _key(p: Persona, coarse: bool = False) -> tuple:
    if coarse:
        return (p.tier, p.cs_relationship)
    return (p.tier, p.cs_relationship, p.family, "e5" if p.e5 else "non_e5")


def build_archetypes(personas: list[Persona], max_archetypes: int = 48) -> list[Archetype]:
    groups: dict[tuple, list[Persona]] = defaultdict(list)
    for p in personas:
        groups[_key(p)].append(p)
    # fold singletons into their coarse (tier, relationship) bucket
    folded: dict[tuple, list[Persona]] = defaultdict(list)
    for k, ps in groups.items():
        folded[_key(ps[0], coarse=len(ps) < 2)].extend(ps) if len(ps) < 2 else folded[k].extend(ps)
    # if still over budget, merge the smallest groups upward to coarse keys
    while len(folded) > max_archetypes:
        k = min(folded, key=lambda kk: len(folded[kk]))
        ps = folded.pop(k)
        ck = _key(ps[0], coarse=True)
        if ck == k:  # already coarse: merge into largest same-tier group
            tgt = max((kk for kk in folded if kk[0] == k[0]), key=lambda kk: len(folded[kk]), default=None)
            if tgt is None:
                folded[k] = ps
                break
            folded[tgt].extend(ps)
        else:
            folded[ck].extend(ps)
    out = []
    for k, ps in sorted(folded.items(), key=lambda kv: -sum(p.seat_weight for p in kv[1])):
        a = Archetype(
            key="/".join(str(x) for x in k),
            persona_ids=[p.id for p in ps], n=len(ps),
            firm_weight=sum(p.firm_weight for p in ps),
            seat_weight=sum(p.seat_weight for p in ps),
            tier=ps[0].tier, cs_relationship=ps[0].cs_relationship,
            family=ps[0].family if len(k) > 2 else "mixed",
            e5_bucket=k[3] if len(k) > 3 else "mixed",
        )
        a.stats = _stats(ps)
        out.append(a)
    return out


def _stats(ps: list[Persona]) -> dict:
    inds = Counter(p.industry for p in ps).most_common(3)
    roles = Counter(p.title for p in ps).most_common(3)
    mods = Counter(m for p in ps for m in p.modules)
    stance = {d: round(mean(p.stance[d] for p in ps), 2) for d in ps[0].stance}
    return {
        "n": len(ps),
        "tier_label": ps[0].tier_label,
        "industries": inds,
        "roles": roles,
        "median_employees": int(median(p.employees for p in ps)),
        "median_endpoints": int(median(p.endpoints for p in ps)),
        "median_team": int(median(p.security_team_size for p in ps)),
        "e5_share": round(mean(1.0 if p.e5 else 0.0 for p in ps), 2),
        "flex_share": round(mean(1.0 if p.falcon_flex else 0.0 for p in ps), 2),
        "module_rates": {m: round(c / len(ps), 2) for m, c in mods.most_common(8)},
        "edr_mix": Counter(p.primary_edr for p in ps).most_common(4),
        "siem_mix": Counter(p.siem for p in ps).most_common(4),
        "mdr_mix": Counter(p.mdr_partner for p in ps).most_common(3),
        "stance": stance,
        "sample_bios": [p.bio for p in ps[: min(2, len(ps))]],
    }


def render_card(a: Archetype, pressures: list[str]) -> str:
    s = a.stats
    lines = [
        f"ARCHETYPE {a.key} — {s['n']} panelists representing ~{a.firm_weight:,.0f} US firms "
        f"/ ~{a.seat_weight:,.0f} protected endpoints",
        f"Segment: {s['tier_label']}; CrowdStrike relationship: {a.cs_relationship}; "
        f"role family: {a.family}; Microsoft E5/E7: {a.e5_bucket} (share {s['e5_share']:.0%})",
        f"Typical firm: ~{s['median_employees']:,} employees, ~{s['median_endpoints']:,} endpoints, "
        f"security team of {s['median_team']}",
        "Top industries: " + ", ".join(f"{k} ({c})" for k, c in s["industries"]),
        "Roles present: " + ", ".join(f"{k} ({c})" for k, c in s["roles"]),
        "Endpoint vendor mix: " + ", ".join(f"{k} ({c})" for k, c in s["edr_mix"]),
        "SIEM mix: " + ", ".join(f"{k} ({c})" for k, c in s["siem_mix"])
        + " | MDR mix: " + ", ".join(f"{k} ({c})" for k, c in s["mdr_mix"]),
    ]
    if a.cs_relationship == "customer":
        lines.append("Falcon module ownership rates: "
                     + ", ".join(f"{m} {r:.0%}" for m, r in s["module_rates"].items())
                     + f"; Falcon Flex share {s['flex_share']:.0%}")
    lines.append("Stance vector (0=low, 1=high): "
                 + "; ".join(f"{STANCE_GLOSS[d]} {v:.2f}" for d, v in s["stance"].items()))
    lines.append("Current workload pressures common in this cluster this week: "
                 + " / ".join(pressures))
    lines.append("Representative members:")
    for b in s["sample_bios"]:
        lines.append(f"  - {b}")
    return "\n".join(lines)
