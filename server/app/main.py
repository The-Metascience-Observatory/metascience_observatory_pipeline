"""
FastAPI orchestrator for the MO pipeline.

Polling-based (no WebSockets): the dashboard polls /stages and /system. Stages
run as detached subprocesses (runner.py) with mutex-group guards enforced at
launch. Nothing here holds long-lived state beyond the on-disk PID/state files,
so the API can restart freely without losing running stages.
"""
from __future__ import annotations

import os

import shutil

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from mo_pipeline import config
from . import runner, state
from .registry import STAGES, BY_ID, stage_dict, _mem_available_gb, _grobid_up, _catalog_stats
from .settings import CONVERT_MIN_MEM_GB

app = FastAPI(title="MO Pipeline Orchestrator", version="0.1.0")
# The dashboard reaches the API through Next's /api rewrite (same origin), so
# CORS only matters for direct browser calls. Allowing "*" let any web page the
# user visits POST runs or DELETE doi-runs on localhost:8090.
_DASH_PORT = os.environ.get("MO_DASH_PORT", "3010")
app.add_middleware(CORSMiddleware,
                   allow_origins=[f"http://localhost:{_DASH_PORT}", f"http://127.0.0.1:{_DASH_PORT}"],
                   allow_methods=["*"], allow_headers=["*"])


# ── models ───────────────────────────────────────────────────────────────────
class RunRequest(BaseModel):
    params: dict = {}


class StatePatch(BaseModel):
    tag: str | None = None


class KeywordEdit(BaseModel):
    items: list[str]


class DoiRunCreate(BaseModel):
    name: str
    csv_text: str | None = None    # pasted / uploaded CSV content
    source_path: str | None = None  # or a server-side CSV path


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

        # Convert GROBID guard. pdf4llm needs it at :8070 and nothing else
        # starts it, so this both brings it up and refuses to launch without
        # it -- otherwise the batch runs and fails per-PDF, thousands of times,
        # for a reason that is only visible in the log. `start()` is a no-op
        # when it is already answering and never touches a running container.
        from .grobid import start as _grobid_start, ANSWERING, RUNNING_NOT_ANSWERING
        grobid_state = _grobid_start()
        if grobid_state != ANSWERING:
            detail = ("container is up but unreachable — this host's docker port "
                      "publishing is broken; recreate it on --network host"
                      if grobid_state == RUNNING_NOT_ANSWERING else
                      "could not be started")
            raise HTTPException(409, f"blocked: GROBID {grobid_state} — {detail}")

    # Extract needs a tag (from params or state).
    tag = params.get("tag") or st.get("tag")
    try:
        argv = stage.build_argv(params, st)
    except Exception as e:
        raise HTTPException(400, f"cannot build command: {e}")

    rec = runner.launch(stage_id, argv, cwd=stage.cwd, tag=tag, params=params)
    return {"launched": True, "pid": rec["pid"], "argv": argv, "log": rec["log"]}


@app.post("/stages/{stage_id}/stop")
def stop_stage(stage_id: str, force: bool = False):
    if stage_id not in BY_ID:
        raise HTTPException(404, "unknown stage")
    return runner.stop(stage_id, force=force)


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


@app.get("/state")
def get_state():
    return {"state": state.load()}


@app.put("/state")
def put_state(patch: StatePatch):
    return {"state": state.save({"tag": patch.tag})}


@app.get("/keywords")
def get_keywords():
    """Effective stage-1 search keyword lists + metadata + which are overridden."""
    from mo_pipeline.discover import keywords as kw
    kw.ensure_defaults()  # registers the code defaults with the overlay
    eff = kw.effective()
    overridden = kw.is_overridden()
    return {"lists": [{**m, "items": eff.get(m["key"], []),
                       "count": len(eff.get(m["key"], [])),
                       "overridden": overridden.get(m["key"], False)}
                      for m in kw.KEY_META]}


@app.get("/keywords/stats")
def keyword_stats():
    """Cached per-keyword yield stats. Never computes inline — POST …/refresh."""
    from mo_pipeline.discover import keyword_stats as ks
    return ks.status()


@app.post("/keywords/stats/refresh")
def keyword_stats_refresh():
    """Recompute yield stats in a background thread (streams the big CSVs)."""
    from mo_pipeline.discover import keyword_stats as ks
    if not ks.start_refresh_background():
        raise HTTPException(409, "keyword stats recompute already running")
    return {"started": True}


@app.get("/artifacts")
def artifacts():
    """Dataset-state panel: cheap stats (size/mtime) for the discover-stage
    artifacts, plus warnings when a downstream file is older than its input."""
    items = [  # (name, owning stage, path, upstream input file or None)
        ("candidates_raw.csv", "search", config.CANDIDATES_RAW_CSV, None),
        ("candidates_dedup.csv", "dedup", config.CANDIDATES_DEDUP_CSV, "candidates_raw.csv"),
        ("candidates_filtered.csv", "prefilter", config.CANDIDATES_FILTERED_CSV, "candidates_dedup.csv"),
        ("classified.csv", "classify", config.CLASSIFIED_CSV, "candidates_filtered.csv"),
        ("confirmed_replications.csv", "classify", config.CONFIRMED_REPLICATIONS_CSV, "classified.csv"),
        ("search_progress.json", "search", config.SEARCH_PROGRESS_FILE, None),
        ("classify_progress.json", "classify", config.CLASSIFY_PROGRESS_FILE, None),
    ]
    out, warnings = [], []
    mtimes: dict[str, float | None] = {}
    for name, stage, path, _up in items:
        try:
            st = path.stat()
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            size = mtime = None
        mtimes[name] = mtime
        out.append({"name": name, "stage": stage, "size": size, "mtime": mtime})
    for name, stage, _path, up in items:
        m, um = mtimes[name], mtimes.get(up)
        if m and um and m < um:
            warnings.append(f"{name} is older than its input {up} — stale, re-run {stage}")
    return {"artifacts": out, "warnings": warnings}


@app.put("/keywords/{key}")
def put_keywords(key: str, edit: KeywordEdit):
    from mo_pipeline.discover import keywords as kw
    kw.ensure_defaults()
    try:
        eff = kw.save_list(key, edit.items)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"saved": True, "key": key, "count": len(eff.get(key, []))}


@app.post("/keywords/{key}/reset")
def reset_keywords(key: str):
    from mo_pipeline.discover import keywords as kw
    kw.ensure_defaults()
    if key not in [m["key"] for m in kw.KEY_META]:
        raise HTTPException(404, "unknown keyword list")
    eff = kw.reset(key)
    return {"reset": True, "key": key, "count": len(eff.get(key, []))}


@app.get("/doi-runs")
def doi_runs_list():
    """All named DOI-list runs with their pipeline status."""
    from mo_pipeline.discover import doi_runs
    out = []
    for meta in doi_runs.list_runs():
        try:
            status = doi_runs.run_status(meta["slug"])
        except Exception as e:
            status = {"error": str(e)}
        out.append({**meta, "status": status})
    return {"runs": out}


@app.post("/doi-runs")
def doi_runs_create(req: DoiRunCreate):
    """Create a run from pasted/uploaded CSV text or a server-side CSV path."""
    from mo_pipeline.discover import doi_runs
    try:
        meta = doi_runs.create_run(req.name, csv_text=req.csv_text,
                                   source_path=req.source_path)
    except FileExistsError as e:
        raise HTTPException(409, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {**meta, "status": doi_runs.run_status(meta["slug"])}


@app.delete("/doi-runs/{slug}")
def doi_runs_delete(slug: str):
    """Remove the run's DOI list + metadata (never touches the corpus)."""
    from mo_pipeline.discover import doi_runs
    try:
        doi_runs.delete_run(slug)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return {"deleted": True, "slug": slug}


@app.get("/corpus/download/options")
def corpus_download_options():
    """Paper types (stage-4 replication_type) with corpus counts, for checkboxes."""
    from . import corpus_download
    return {"types": corpus_download.options()}


@app.get("/corpus/download")
def corpus_download_zip(types: str = "all"):
    """Stream a zip of paper folders: ?types=all or ?types=direct,close,unclassified."""
    import re as _re
    from fastapi.responses import StreamingResponse
    from . import corpus_download
    sel = (None if types.strip().lower() in ("", "all")
           else {t.strip().lower() for t in types.split(",") if t.strip()})
    folders = corpus_download.folders_for(sel)
    if not folders:
        raise HTTPException(404, "no papers match the selected types")
    label = "all" if sel is None else _re.sub(r"[^a-z0-9-]+", "_", "-".join(sorted(sel)))[:60]
    return StreamingResponse(
        corpus_download.zip_stream(folders),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="mo_corpus_{label}.zip"'},
    )


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
