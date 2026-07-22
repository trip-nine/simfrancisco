"""simsoc web console + pixel-USA map — one FastAPI serverless function.
Vercel routes every path here (vercel.json rewrite). Writable disk is /tmp only.

Endpoints: /            console UI        /map          pixel USA UI
           /api/panel   composition       /api/map      terrain + firm entities
           /api/news    live headlines    /api/thought  one sim's current thinking
           /api/poll    /api/feature      synthetic runs (optionally news-grounded)
"""

from __future__ import annotations
import hashlib
import json
import os
import random
import re
import sys
import time
import traceback
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

from fastapi import APIRouter, Body, FastAPI  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402

import mapdata  # noqa: E402
from simsoc import aggregate  # noqa: E402
from simsoc.llm import Cache  # noqa: E402
from simsoc.runner import Run  # noqa: E402
from simsoc.sampler import build_panel, load_configs  # noqa: E402
from simsoc.schema import save_panel  # noqa: E402

DEFAULT_OPTIONS = ["oppose", "lean_oppose", "neutral", "lean_support", "support"]
CONFIG = str(BASE / "config")
PANEL = "/tmp/simsoc-panel.json"
CACHE = "/tmp/simsoc-cache.db"
NEWS_CACHE = "/tmp/simsoc-news.json"
NEWS_TTL = 600
TIER_LABEL = {"t1_f500": "Fortune 500 / global", "t2_large": "Large enterprise",
              "t3_upper_mid": "Upper midmarket", "t4_mid": "Midmarket",
              "t5_smb": "SMB", "t6_small": "Small business"}

app = FastAPI(title="simsoc")
r = APIRouter()
_panel_cache: list | None = None
_map_cache: dict | None = None
_cfg = load_configs(CONFIG)


def _panel():
    global _panel_cache
    if _panel_cache is None:
        _panel_cache = build_panel(CONFIG)
        save_panel(_panel_cache, PANEL)
    elif not Path(PANEL).exists():
        save_panel(_panel_cache, PANEL)
    return _panel_cache


def _rng(*parts) -> random.Random:
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "little"))


# ------------------------------------------------------------------ map world

_NAME_A = ["Granite", "Ridgeline", "Harbor", "Summit", "Cascade", "Pioneer", "Beacon",
           "Ironwood", "Bluewater", "Meridian", "Frontier", "Lakeshore", "Redwood",
           "Palmetto", "Keystone", "Prairie", "Canyon", "Northstar", "Copperfield", "Bayline"]
_NAME_B = {
    "financial_services": ["Financial", "Capital", "Mutual", "Trust"],
    "healthcare": ["Health", "Medical", "Care", "Clinics"],
    "manufacturing": ["Manufacturing", "Industries", "Fabrication", "Works"],
    "retail_ecommerce": ["Retail", "Commerce", "Outfitters", "Goods"],
    "technology_saas": ["Software", "Systems", "Labs", "Cloud"],
    "energy_utilities": ["Energy", "Power", "Utilities", "Grid"],
    "education": ["Academy", "College", "Learning", "Schools"],
    "state_local_gov": ["County", "Municipal", "Public Works", "Services District"],
    "professional_services": ["Advisors", "Consulting", "Partners", "Associates"],
    "transportation_logistics": ["Logistics", "Freight", "Transit", "Shipping"],
    "hospitality": ["Hospitality", "Resorts", "Hotels", "Dining Group"],
    "media_entertainment": ["Media", "Studios", "Broadcasting", "Entertainment"],
}
_NAME_C = ["Group", "Inc", "Co", "Corp", "LLC", "Holdings", ""]


def _firm_name(p) -> str:
    g = _rng("name", p.id)
    b = g.choice(_NAME_B.get(p.industry, ["Enterprises"]))
    tail = g.choice(_NAME_C)
    return " ".join(x for x in (g.choice(_NAME_A), b, tail) if x)


_FIRST = ["Dana","Marcus","Priya","Yuki","Amara","Diego","Ingrid","Kofi","Elena","Tomás",
          "Sasha","Ravi","Maya","Owen","Zainab","Luca","Noor","Felix","Harper","Imani",
          "Jonas","Keiko","Liam","Mira","Nadia","Omar","Paige","Quinn","Rosa","Stefan",
          "Talia","Umar","Vera","Wes","Ximena","Yosef"]
_LAST = ["Okafor","Nguyen","Reyes","Cohen","Patel","Kowalski","Tanaka","Silva","Haddad","Berg",
         "Ivanova","O'Brien","Kim","Moreau","Adeyemi","Rossi","Larsen","Mbeki","Chavez","Weiss",
         "Fujita","Novak","Grant","Osei","Petrov","Lindqvist","Marino","Duarte","Khan","Sørensen",
         "Whitfield","Aluko","Vargas","Eriksen","Nakamura","Boateng"]


def _person_name(pid: str, idx: int) -> str:
    g = _rng("pname", pid, idx)
    return f"{g.choice(_FIRST)} {g.choice(_LAST)}"


def _coworkers(p) -> list[dict]:
    """Seeded extra staff around the building, sampled from the tier's role table."""
    roles = _cfg["roles"]["roles"]
    avail = [(k, v) for k, v in roles.items()
             if p.tier in v.get("weights", {}) and k != p.role_key]
    g = _rng("staff", p.id)
    n = {"t1_f500": 3, "t2_large": 3, "t3_upper_mid": 2, "t4_mid": 2,
         "t5_smb": 1, "t6_small": 0}[p.tier]
    out = []
    for k, v in (g.sample(avail, min(n, len(avail))) if avail else []):
        out.append({"role": v["title"], "day": v["day_to_day"], "family": v["family"]})
    return out


def _world():
    """Place every firm on the pixel USA: metro chosen in the persona's region
    (weighted by metro size + tier bias toward big metros), then jittered onto
    the nearest free land cell so cities grow little building clusters."""
    global _map_cache
    if _map_cache is not None:
        return _map_cache
    grid = mapdata.decode()
    W, H = mapdata.W, mapdata.H
    land = lambda x, y: 0 <= y < H and grid[y * W + (x % W)] not in "owz"
    occupied: set[tuple[int, int]] = set()
    by_region: dict[str, list[dict]] = {}
    for m in mapdata.METROS:
        by_region.setdefault(m["region"], []).append(m)

    ents = []
    for p in _panel():
        g = _rng("place", p.id)
        pool = by_region[p.region]
        big_bias = {"t1_f500": 3.0, "t2_large": 2.2, "t3_upper_mid": 1.6}.get(p.tier, 1.0)
        weights = [m["w"] ** big_bias for m in pool]
        metro = g.choices(pool, weights=weights, k=1)[0]
        spread = 3 + int(metro["w"] * 0.8)
        x = y = None
        for _ in range(60):
            cx = metro["x"] + int(round(g.gauss(0, spread)))
            cy = metro["y"] + int(round(g.gauss(0, spread * 0.8)))
            if land(cx, cy) and (cx, cy) not in occupied:
                x, y = cx, cy
                break
        if x is None:
            x, y = metro["x"], metro["y"]
        occupied.add((x, y))
        ents.append({
            "id": p.id, "x": x, "y": y, "metro": metro["name"],
            "region": p.region, "fw": round(p.firm_weight),
            "name": _firm_name(p), "tier": p.tier, "tier_label": p.tier_label,
            "industry": p.industry, "employees": p.employees, "endpoints": p.endpoints,
            "team": p.security_team_size, "rel": p.cs_relationship,
            "modules": p.modules, "flex": p.falcon_flex, "e5": p.e5,
            "edr": p.primary_edr, "siem": p.siem, "identity": p.identity_stack,
            "mdr": p.mdr_partner, "renewal": p.renewal_quarter, "bio": p.bio,
            "seg": {"tier": p.tier, "rel": p.cs_relationship, "family": p.family,
                    "e5": "e5" if p.e5 else "non_e5"},
            "people": [{"name": _person_name(p.id, 0), "role": p.title,
                        "day": p.day_to_day, "family": p.family, "key": True}]
                      + [{**cw, "name": _person_name(p.id, ci + 1)}
                         for ci, cw in enumerate(_coworkers(p))],
        })
    _map_cache = {"w": W, "h": H, "rle": mapdata.RLE,
                  "wrap": getattr(mapdata, "WRAP", False),
                  "us_view": getattr(mapdata, "US_VIEW", None),
                  "metros": [{"name": m["name"], "x": m["x"], "y": m["y"], "w": m["w"]}
                             for m in mapdata.METROS],
                  "entities": ents}
    return _map_cache


# ------------------------------------------------------------------ live news

_NEWS_FALLBACK = [
    "Agentic SOC adoption accelerates as vendors race on autonomy controls",
    "SIEM ingest costs push more enterprises toward pipeline filtering",
    "Cyber insurers tighten questionnaires on AI-agent governance",
]


def get_news() -> dict:
    try:
        c = json.loads(Path(NEWS_CACHE).read_text())
        if time.time() - c["ts"] < NEWS_TTL:
            return c
    except Exception:
        c = None
    url = ("https://news.google.com/rss/search?q="
           "CrowdStrike%20OR%20cybersecurity%20OR%20%22Palo%20Alto%20Networks%22"
           "%20OR%20%22Microsoft%20security%22&hl=en-US&gl=US&ceid=US:en")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "simsoc/0.1"})
        xml = urllib.request.urlopen(req, timeout=6).read()
        items = []
        for it in ET.fromstring(xml).iter("item"):
            t = (it.findtext("title") or "").strip()
            t = re.sub(r"\s+-\s+[^-]+$", "", t)  # strip trailing " - Source"
            if t:
                items.append(t)
            if len(items) >= 12:
                break
        if not items:
            raise ValueError("empty feed")
        out = {"ts": time.time(), "source": "Google News RSS (live)", "items": items}
        Path(NEWS_CACHE).write_text(json.dumps(out))
        return out
    except Exception:
        if c:
            return c
        return {"ts": time.time(), "source": "fallback (feed unreachable)",
                "items": _NEWS_FALLBACK}


def _news_event_block() -> str:
    n = get_news()
    return ("Live headlines your teams saw today (" + n["source"] + "):\n"
            + "\n".join(f"- {t}" for t in n["items"]))


# ------------------------------------------------------------------ thoughts

_THOUGHT_TMPL = [  # (stance dim, high?, template)
    ("microsoft_gravity", True, "Copilot's already inside our E5 bill. \u201c{hl}\u201d just makes that argument for procurement."),
    ("data_cost_sensitivity", True, "Read \u201c{hl}\u201d between two ingest-bill reviews. Everything is a pipeline-cost problem now."),
    ("outage_scar", True, "\u201c{hl}\u201d \u2014 and I still think about July '24 every time one vendor wants to run everything."),
    ("staffing_pain", True, "\u201c{hl}\u201d, sure, but I'm down an analyst and the queue doesn't care about headlines."),
    ("ai_trust", False, "Saw \u201c{hl}\u201d. Until someone shows me the audit trail, an agent isn't touching prod."),
    ("automation_appetite", True, "\u201c{hl}\u201d \u2014 honestly if an agent clears my tier-1 queue, it can have the queue."),
    ("budget_pressure", True, "\u201c{hl}\u201d is nice; my CFO still wants a displaced line item for every new one."),
    ("channel_reliance", True, "Forwarded \u201c{hl}\u201d to our MSP. Whatever they standardize on is what we run."),
    ("crowdstrike_affinity", True, "\u201c{hl}\u201d \u2014 Flex wallet makes trying the new stuff basically frictionless for us."),
    ("compliance_burden", True, "\u201c{hl}\u201d, meanwhile I owe the auditors evidence by Friday. AI policy draft is on v6."),
]


def _prior_thought(stance: dict, hl: str, salt: str) -> str:
    scored = []
    for dim, high, tmpl in _THOUGHT_TMPL:
        v = stance.get(dim, 0.5)
        scored.append(((v if high else 1 - v), tmpl))
    scored.sort(key=lambda t: -t[0])
    g = _rng("thought", salt, hl)
    tmpl = g.choice(scored[:3])[1]
    return tmpl.format(hl=hl if len(hl) < 90 else hl[:87] + "\u2026")


def _live_thought(p, role: str, hl_block: str) -> str:
    from anthropic import Anthropic
    cache = Cache(CACHE)
    system = ("You are one simulated US security practitioner. Reply with a single "
              "1-2 sentence inner thought, in character, terse and concrete, reacting "
              "to today's news given who you are. No quotes around it, no preamble.")
    user = f"WHO: {role} \u2014 {p.bio}\nTODAY'S NEWS:\n{hl_block}"
    k = Cache.key("thought-sonnet", system, user, 120)
    hit = cache.get(k)
    if hit:
        return hit
    resp = Anthropic().messages.create(model="claude-sonnet-4-6", max_tokens=120,
                                       temperature=0.8, system=system,
                                       messages=[{"role": "user", "content": user}])
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    cache.put(k, text)
    return text


# ------------------------------------------------------------------ runs

US_REGIONS = {"northeast", "southeast", "midwest", "southwest", "west"}
REGION_LABEL = {"world": "worldwide", "usa": "USA", "northeast": "Northeast US",
                "southeast": "Southeast US", "midwest": "Midwest US",
                "southwest": "Southwest US", "west": "Western US",
                "emea": "EMEA", "apac": "APAC", "americas_x": "Americas ex-US"}


def _scoped_panel(region: str | None) -> tuple[str, str]:
    """Write (once per cold start per scope) a panel file filtered to the
    requested region; runs then aggregate with the correct sub-universe
    weights automatically since every persona carries its own weight."""
    ps = _panel()
    region = (region or "world").lower()
    if region in ("world", ""):
        return PANEL, "worldwide"
    sub = [p for p in ps if (p.region in US_REGIONS)] if region == "usa"         else [p for p in ps if p.region == region]
    if not sub:
        return PANEL, "worldwide"
    path = f"/tmp/simsoc-panel-{region}.json"
    if not Path(path).exists():
        save_panel(sub, path)
    return path, REGION_LABEL.get(region, region)


def _mk_run(engine: str, weight: str, region: str | None = None) -> Run:
    panel_path, scope = _scoped_panel(region)
    live = engine == "anthropic"
    run = Run(config_dir=CONFIG, panel_path=panel_path, engine=engine,
              cache_db=CACHE, weight_mode=weight,
              max_archetypes=20 if live else 48,
              concurrency=8 if live else 4,
              max_tokens=1200 if live else 1400)
    run.scope_label = scope
    return run


def _fail(e: Exception) -> JSONResponse:
    msg = str(e)
    if "ANTHROPIC_API_KEY" in msg or "api_key" in msg.lower() or "authentication" in msg.lower():
        msg = ("Live engine needs an ANTHROPIC_API_KEY. In Vercel: Project \u2192 Settings \u2192 "
               "Environment Variables \u2192 add ANTHROPIC_API_KEY, then redeploy. "
               "The prior engine works without it.")
    traceback.print_exc()
    return JSONResponse({"error": msg}, status_code=500)


def _events(body: dict) -> list[str]:
    return [_news_event_block()] if body.get("news") else []


def _rows_map(rows: list[dict], kind: str) -> list[dict]:
    out = []
    for row in rows:
        if kind == "poll":
            m = float(row["data"].get("mean_sentiment", 0))
        else:
            sh = row["data"]["adoption_intent_shares"]
            m = sh.get("4_likely", 0) + sh.get("5_definitely", 0)
        out.append({"tier": row["tier"], "rel": row["cs_relationship"],
                    "family": row["family"], "e5": row["e5_bucket"], "m": round(m, 3)})
    return out


@r.get("/panel")
def panel_info():
    ps = _panel()
    strip = [{"tier": p.tier, "family": p.family, "rel": p.cs_relationship,
              "e5": p.e5, "bio": p.bio} for p in ps]
    return {"n": len(ps), "firms": round(sum(p.firm_weight for p in ps)),
            "seats": round(sum(p.seat_weight for p in ps)),
            "cs_customers": round(sum(p.firm_weight for p in ps
                                      if p.cs_relationship == "customer")),
            "as_of": "2026-07-21", "tiers": TIER_LABEL, "strip": strip,
            "live_ready": bool(os.environ.get("ANTHROPIC_API_KEY"))}


@r.get("/map")
def map_world():
    w = dict(_world())
    ps = _panel()
    w["totals"] = {"firms": round(sum(p.firm_weight for p in ps)),
                   "cs_customers": round(sum(p.firm_weight for p in ps
                                             if p.cs_relationship == "customer"))}
    w["live_ready"] = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return w


@r.get("/news")
def news():
    n = get_news()
    return {"source": n["source"], "age_s": int(time.time() - n["ts"]), "items": n["items"]}


@r.get("/thought")
def thought(pid: str, person: int = 0, engine: str = "prior"):
    try:
        p = next(x for x in _panel() if x.id == pid)
        ent = next(e for e in _world()["entities"] if e["id"] == pid)
        person = max(0, min(person, len(ent["people"]) - 1))
        role = ent["people"][person]["role"]
        n = get_news()
        hl = n["items"][(int(time.time() // 300) + _rng(pid, person).randrange(97))
                        % len(n["items"])]
        if engine == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
            txt = _live_thought(p, role, "\n".join(f"- {t}" for t in n["items"][:6]))
            src = "claude \u00b7 live"
        else:
            txt = _prior_thought(p.stance, hl, f"{pid}:{person}")
            src = "stance prior"
        return {"role": role, "text": txt, "engine": src, "headline": hl}
    except Exception as e:  # noqa: BLE001
        return _fail(e)


@r.get("/scenarios")
def scenarios():
    out = []
    for f in sorted((BASE / "scenarios").glob("*.md")):
        text = f.read_text()
        title = text.splitlines()[0].lstrip("# ").split(":", 1)[-1].strip()
        out.append({"id": f.stem, "title": title, "spec": text})
    return out


@r.post("/poll")
def poll(body: dict = Body(...)):
    try:
        t0 = time.time()
        engine = body.get("engine", "prior")
        weight = body.get("weight", "seats")
        options = [o.strip() for o in body.get("options", "").split(",") if o.strip()] \
            or DEFAULT_OPTIONS
        run = _mk_run(engine, weight, body.get("region"))
        rows = run.poll(body["question"], options, _events(body))
        res = aggregate.aggregate_poll(rows, options, weight)
        res["verbatims"] = aggregate.collect_verbatims(rows)
        res["rows_map"] = _rows_map(rows, "poll")
        res["meta"] = {"engine_line": run.stats_line(), "seconds": round(time.time() - t0, 1),
                       "units": len(rows), "personas": len(run.personas),
                       "scope": run.scope_label,
                       "news_grounded": bool(body.get("news"))}
        return res
    except Exception as e:  # noqa: BLE001
        return _fail(e)


@r.post("/feature")
def feature(body: dict = Body(...)):
    try:
        t0 = time.time()
        engine = body.get("engine", "prior")
        weight = body.get("weight", "seats")
        run = _mk_run(engine, weight, body.get("region"))
        rows = run.feature_test(body["spec"], _events(body))
        res = aggregate.aggregate_feature(rows, weight)
        res["verbatims"] = aggregate.collect_verbatims(rows)
        res["rows_map"] = _rows_map(rows, "feature")
        res["meta"] = {"engine_line": run.stats_line(), "seconds": round(time.time() - t0, 1),
                       "units": len(rows), "personas": len(run.personas),
                       "scope": run.scope_label,
                       "news_grounded": bool(body.get("news"))}
        return res
    except Exception as e:  # noqa: BLE001
        return _fail(e)


_HTML = (BASE / "static" / "index.html").read_text()
_MAP = (BASE / "static" / "map.html").read_text()


@app.get("/map")  # must register before the bare router: it also defines GET /map (JSON)
def map_ui():
    return HTMLResponse(_MAP)


# register under both path shapes Vercel's rewrite can deliver
app.include_router(r, prefix="/api")
app.include_router(r)


@app.get("/{_path:path}")  # catch-all last
def ui(_path: str = ""):
    return HTMLResponse(_HTML)
