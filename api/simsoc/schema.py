"""Core data model. Personas are seeded + deterministic (no LLM per agent),
mirroring simfrancisco's persona layer. Weights replace PUMS PWGTP:
firm_weight = US firms represented by this panel slot; seat_weight scales by
protected endpoints (a proxy for seat/ARR share of the market)."""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any
import json


@dataclass
class Persona:
    id: str
    tier: str
    tier_label: str
    industry: str
    region: str
    employees: int
    endpoints: int
    security_team_size: int
    maturity: str
    budget_trend: str
    cs_relationship: str            # customer | prospect | churned | none
    cs_tenure_years: int
    modules: list[str] = field(default_factory=list)
    falcon_flex: bool = False
    sku_note: str = ""
    e5: bool = False
    primary_edr: str = "crowdstrike"
    siem: str = "none"
    identity_stack: str = "entra_only"
    mdr_partner: str = "in_house"
    renewal_quarter: str = ""
    role_key: str = ""
    title: str = ""
    family: str = ""
    buying_role: str = ""
    day_to_day: str = ""
    stance: dict[str, float] = field(default_factory=dict)
    firm_weight: float = 1.0
    seat_weight: float = 1.0
    bio: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Persona":
        return Persona(**d)


@dataclass
class Archetype:
    key: str
    persona_ids: list[str]
    n: int
    firm_weight: float
    seat_weight: float
    tier: str
    cs_relationship: str
    family: str
    e5_bucket: str
    card: str = ""                  # rendered at prompt time (includes run pressures)
    stats: dict[str, Any] = field(default_factory=dict)


def save_panel(personas: list[Persona], path: str) -> None:
    with open(path, "w") as f:
        json.dump([p.to_dict() for p in personas], f, indent=1)


def load_panel(path: str) -> list[Persona]:
    with open(path) as f:
        return [Persona.from_dict(d) for d in json.load(f)]
