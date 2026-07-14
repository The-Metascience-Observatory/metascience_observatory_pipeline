"""
FastAPI orchestrator for the MO pipeline.

Polling-based (no WebSockets): the dashboard polls /stages and /system. Stages
run as detached subprocesses (runner.py) with mutex-group guards enforced at
launch. Nothing here holds long-lived state beyond the on-disk PID/state files,
so the API can restart freely without losing running stages.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from mo_pipeline import config
from . import runner, state
from .registry import STAGES, BY_ID, stage_dict, _mem_available_gb, _grobid_up, _catalog_stats
from .settings import CONVERT_MIN_MEM_GB

app = FastAPI(title="MO Pipeline Orchestrator", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── models ───────────────────────────────────────────────────────────────────
class RunRequest(BaseModel):
    params: dict = {}


class BatchPatch(BaseModel):
    batch: str | None = None
    tag: str | None = None


class KeywordEdit(BaseModel):
    items: list[str]


# ── stage status assembly ────────────────────────────────────────────────────
def _stage_status(stage) -> dict:
    st = state.load()
    run = runner.status(stage.id)
    try:
        probe = stage.probe(st) if stage.probe else {"state": "unknown"}
    except Exception as e:
        probe = {"state": "error", "detail": str(e)}
    return {**stage_dict(stage), "run": run, "probe": probe}


def _running_mutex_groups(exclude: str | None = None) -> dict:
    """Map cross-stage mutex group -> stage_id for every running stage.

    'self' is a per-stage singleton marker (only one instance of a given stage),
    enforced separately by runner.is_running(stage_id); it is NOT a shared group
    and must not cross-block different stages, so it is skipped here.
    """
    active = {}
    for s in STAGES:
        if s.id == exclude:
            continue
        if runner.is_running(s.id):
            for g in s.mutex_groups:
                if g == "self":
                    continue
                active[g] = s.id
    return active


# ── endpoints ────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"ok": True}


@app.get("/stages")
def list_stages():
    return {"stages": [_stage_status(s) for s in STAGES], "state": state.load()}


@app.get("/stages/{stage_id}")
def get_stage(stage_id: str):
    if stage_id not in BY_ID:
        raise HTTPException(404, "unknown stage")
    return _stage_status(BY_ID[stage_id])


@app.get("/stages/{stage_id}/logs")
def stage_logs(stage_id: str, lines: int = Query(200, le=5000)):
    if stage_id not in BY_ID:
        raise HTTPException(404, "unknown stage")
    return {"log": runner.tail_log(stage_id, lines)}


@app.post("/stages/{stage_id}/run")
def run_stage(stage_id: str, req: RunRequest):
    if stage_id not in BY_ID:
        raise HTTPException(404, "unknown stage")
    stage = BY_ID[stage_id]
    params = req.params or {}
    st = state.load()

    # Mutex enforcement.
    active = _running_mutex_groups(exclude=stage_id)
    for g in stage.mutex_groups:
        if g in active:
            raise HTTPException(409, f"blocked: mutex '{g}' held by stage '{active[g]}'")
    if runner.is_running(stage_id):
        raise HTTPException(409, f"stage '{stage_id}' already running")

    # Convert RAM guard.
    if stage_id == "convert":
        mem = _mem_available_gb()
        if mem < CONVERT_MIN_MEM_GB:
            raise HTTPException(409, f"blocked: MemAvailable {mem:.0f}GB < {CONVERT_MIN_MEM_GB}GB")

    # Extract needs a tag (from params or state).
    tag = params.get("tag") or st.get("tag")
    try:
        argv = stage.build_argv(params, st)
    except Exception as e:
        raise HTTPException(400, f"cannot build command: {e}")

    rec = runner.launch(stage_id, argv, cwd=stage.cwd, batch=st.get("batch"),
                        tag=tag, params=params)
    return {"launched": True, "pid": rec["pid"], "argv": argv, "log": rec["log"]}


@app.post("/stages/{stage_id}/stop")
def stop_stage(stage_id: str, force: bool = False):
    if stage_id not in BY_ID:
        raise HTTPException(404, "unknown stage")
    return runner.stop(stage_id, force=force)


@app.get("/pipeline")
def pipeline():
    """Ordered stage states for the DAG view."""
    return {"stages": [{"id": s.id, "num": s.num, "label": s.label,
                        "run": runner.status(s.id).get("state"),
                        "probe": (s.probe(state.load()) if s.probe else {}).get("state")}
                       for s in STAGES]}


@app.get("/system")
def system():
    du = shutil.disk_usage(config.MEDIA_ROOT) if config.MEDIA_ROOT.exists() else None
    return {
        "mem_available_gb": round(_mem_available_gb(), 1),
        "grobid_up": _grobid_up(),
        "media_disk": ({"free_gb": round(du.free / 1e9, 1),
                        "total_gb": round(du.total / 1e9, 1),
                        "pct_used": round(100 * du.used / du.total, 1)} if du else None),
        "media_mounted": config.MEDIA_ROOT.exists(),
    }


@app.get("/batch")
def get_batch():
    batches = []
    if config.MEDIA_ROOT.exists():
        batches = sorted(p.name for p in config.MEDIA_ROOT.iterdir() if p.is_dir())
    return {"state": state.load(), "available_batches": batches}


@app.put("/batch")
def put_batch(patch: BatchPatch):
    return {"state": state.save({"batch": patch.batch, "tag": patch.tag})}


@app.get("/keywords")
def get_keywords():
    """Effective stage-1 search keyword lists + metadata + which are overridden."""
    # Importing the search module registers the code defaults with the overlay.
    from mo_pipeline.discover import search_for_replication_studies  # noqa: F401
    from mo_pipeline.discover import keywords as kw
    eff = kw.effective()
    overridden = kw.is_overridden()
    return {"lists": [{**m, "items": eff.get(m["key"], []),
                       "count": len(eff.get(m["key"], [])),
                       "overridden": overridden.get(m["key"], False)}
                      for m in kw.KEY_META]}


@app.put("/keywords/{key}")
def put_keywords(key: str, edit: KeywordEdit):
    from mo_pipeline.discover import search_for_replication_studies  # noqa: F401
    from mo_pipeline.discover import keywords as kw
    try:
        eff = kw.save_list(key, edit.items)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"saved": True, "key": key, "count": len(eff.get(key, []))}


@app.post("/keywords/{key}/reset")
def reset_keywords(key: str):
    from mo_pipeline.discover import search_for_replication_studies  # noqa: F401
    from mo_pipeline.discover import keywords as kw
    if key not in [m["key"] for m in kw.KEY_META]:
        raise HTTPException(404, "unknown keyword list")
    eff = kw.reset(key)
    return {"reset": True, "key": key, "count": len(eff.get(key, []))}


@app.get("/corpus")
def corpus(source_batch: str | None = None, status_filter: str | None = None,
           contains_replications: bool | None = None, limit: int = Query(200, le=2000)):
    stats = _catalog_stats()
    if stats is None:
        return {"available": False, "detail": "no catalog yet"}
    from mo_pipeline.corpus import catalog
    conn = catalog.connect()
    try:
        rows = catalog.query(conn, status=status_filter, source_batch=source_batch,
                             contains_replications=contains_replications, limit=limit)
        papers = [dict(r) for r in rows]
    finally:
        conn.close()
    return {"available": True, "stats": stats, "count": len(papers), "papers": papers}
