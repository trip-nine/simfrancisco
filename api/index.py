"""simsoc web console — one FastAPI serverless function.
Vercel routes every path here (vercel.json rewrite); the UI is served from
api/static/index.html and the engine runs in-process. Writable disk is /tmp only.
"""

from __future__ import annotations
import os
import sys
import time
import traceback
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402
from fastapi import APIRouter, Body  # noqa: E402

from simsoc import aggregate  # noqa: E402
from simsoc.runner import Run  # noqa: E402
from simsoc.sampler import build_panel  # noqa: E402
from simsoc.schema import save_panel  # noqa: E402
DEFAULT_OPTIONS = ["oppose", "lean_oppose", "neutral", "lean_support", "support"]

CONFIG = str(BASE / "config")
PANEL = "/tmp/simsoc-panel.json"
CACHE = "/tmp/simsoc-cache.db"
TIER_LABEL = {"t1_f500": "Fortune 500 / global", "t2_large": "Large enterprise",
              "t3_upper_mid": "Upper midmarket", "t4_mid": "Midmarket",
              "t5_smb": "SMB", "t6_small": "Small business"}

app = FastAPI(title="simsoc")
r = APIRouter()
_panel_cache: list | None = None


def _panel():
    global _panel_cache
    if _panel_cache is None:
        _panel_cache = build_panel(CONFIG)
        save_panel(_panel_cache, PANEL)
    elif not Path(PANEL).exists():
        save_panel(_panel_cache, PANEL)
    return _panel_cache


def _mk_run(engine: str, weight: str) -> Run:
    _panel()
    live = engine == "anthropic"
    return Run(config_dir=CONFIG, panel_path=PANEL, engine=engine,
               cache_db=CACHE, weight_mode=weight,
               # keep live runs inside serverless time limits
               max_archetypes=20 if live else 48,
               concurrency=8 if live else 4,
               max_tokens=1200 if live else 1400)


def _fail(e: Exception) -> JSONResponse:
    msg = str(e)
    if "ANTHROPIC_API_KEY" in msg or "api_key" in msg.lower() or "authentication" in msg.lower():
        msg = ("Live engine needs an ANTHROPIC_API_KEY. In Vercel: Project → Settings → "
               "Environment Variables → add ANTHROPIC_API_KEY, then redeploy. "
               "The prior engine works without it.")
    traceback.print_exc()
    return JSONResponse({"error": msg}, status_code=500)


@r.get("/panel")
def panel_info():
    ps = _panel()
    fw = sum(p.firm_weight for p in ps)
    sw = sum(p.seat_weight for p in ps)
    strip = [{"tier": p.tier, "family": p.family, "rel": p.cs_relationship,
              "e5": p.e5, "bio": p.bio} for p in ps]
    return {"n": len(ps), "firms": round(fw), "seats": round(sw),
            "as_of": "2026-07-21", "tiers": TIER_LABEL, "strip": strip,
            "live_ready": bool(os.environ.get("ANTHROPIC_API_KEY"))}


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
        run = _mk_run(engine, weight)
        rows = run.poll(body["question"], options, [])
        res = aggregate.aggregate_poll(rows, options, weight)
        res["verbatims"] = aggregate.collect_verbatims(rows)
        res["meta"] = {"engine_line": run.stats_line(), "seconds": round(time.time() - t0, 1),
                       "units": len(rows), "personas": len(run.personas)}
        return res
    except Exception as e:  # noqa: BLE001
        return _fail(e)


@r.post("/feature")
def feature(body: dict = Body(...)):
    try:
        t0 = time.time()
        engine = body.get("engine", "prior")
        weight = body.get("weight", "seats")
        run = _mk_run(engine, weight)
        rows = run.feature_test(body["spec"], [])
        res = aggregate.aggregate_feature(rows, weight)
        res["verbatims"] = aggregate.collect_verbatims(rows)
        res["meta"] = {"engine_line": run.stats_line(), "seconds": round(time.time() - t0, 1),
                       "units": len(rows), "personas": len(run.personas)}
        return res
    except Exception as e:  # noqa: BLE001
        return _fail(e)


# register the API under both path shapes Vercel's rewrite can deliver
app.include_router(r, prefix="/api")
app.include_router(r)

_HTML = (BASE / "static" / "index.html").read_text()


@app.get("/{_path:path}")
def ui(_path: str = ""):
    return HTMLResponse(_HTML)
