"""
The 9-stage pipeline registry + per-stage progress probes.

Each Stage declares how to launch it (argv builder), what parameters its run
form exposes, which mutex groups it belongs to, and a probe() that reports cheap
read-only progress. Probes never launch anything; they stat files / query the
catalog. The runner (runner.py) owns process lifecycle; this module owns "what
is a stage and how far along is it".
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from mo_pipeline import config

PY = sys.executable
REPO = str(config.REPO_ROOT)


# ── small probe helpers ──────────────────────────────────────────────────────
def _mtime(path: Path):
    return path.stat().st_mtime if path.exists() else None


def _csv_rows(path: Path) -> int | None:
    if not path.exists():
        return None
    import csv
    csv.field_size_limit(2**31 - 1)
    with open(path, newline="") as f:
        return max(0, sum(1 for _ in csv.reader(f)) - 1)


def _json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _prog(state: str, done=None, total=None, detail="", **extra) -> dict:
    d = {"state": state, "detail": detail}
    if done is not None:
        d["progress_done"] = done
    if total is not None:
        d["progress_total"] = total
    d.update(extra)
    return d


# ── per-stage probes ─────────────────────────────────────────────────────────
_SEARCH_TOTAL_QUERIES = 152  # PubMed+OpenAlex(x2)+EuropePMC+Crossref+OSF+S2

def probe_search(_state) -> dict:
    p = _json(config.SEARCH_PROGRESS_FILE) or {}
    done = len(p.get("completed_queries", []))
    raw = _csv_rows(config.CANDIDATES_RAW_CSV)
    if not done:
        return _prog("idle", detail="no search progress yet")
    return _prog("done" if done >= _SEARCH_TOTAL_QUERIES else "partial",
                 done, _SEARCH_TOTAL_QUERIES,
                 detail=f"{done}/{_SEARCH_TOTAL_QUERIES} queries, {raw or 0} raw candidates")


def probe_dedup(_state) -> dict:
    out = config.CANDIDATES_DEDUP_CSV
    if not out.exists():
        return _prog("idle", detail="no dedup output")
    n = _csv_rows(out)
    return _prog("done", n, n, detail=f"{n} unique candidates", last_output_mtime=_mtime(out))


def probe_prefilter(_state) -> dict:
    out = config.CANDIDATES_FILTERED_CSV
    if not out.exists():
        return _prog("idle", detail="no prefilter output")
    n = _csv_rows(out)
    return _prog("done", n, n, detail=f"{n} kept for classification", last_output_mtime=_mtime(out))


def probe_classify(_state) -> dict:
    p = _json(config.CLASSIFY_PROGRESS_FILE) or {}
    done = len(p.get("classified_indices", []))
    stored = p.get("input_row_count")
    total = _csv_rows(config.CANDIDATES_FILTERED_CSV)
    confirmed = _csv_rows(config.CONFIRMED_REPLICATIONS_CSV)
    # Pre-flag the checkpoint/input mismatch that hard-stops classify.
    if stored is not None and total is not None and stored != total:
        return _prog("stale", done, stored,
                     detail=f"checkpoint built from {stored} rows but input has {total} — "
                            f"delete {config.CLASSIFY_PROGRESS_FILE.name} to reset")
    if not done:
        return _prog("idle", detail="not started")
    tot = stored or total or done
    return _prog("done" if done >= tot else "partial", done, tot,
                 detail=f"{done}/{tot} classified, {confirmed or 0} confirmed replications")


def probe_filter_direct(_state) -> dict:
    out = config.DIRECT_REPLICATIONS_CSV
    n = _csv_rows(out)
    if n is None:
        return _prog("idle", detail="not run")
    pdfs = len(list(config.DIRECT_REPLICATIONS_PDF_DIR.glob("*.pdf"))) \
        if config.DIRECT_REPLICATIONS_PDF_DIR.exists() else 0
    return _prog("done", n, n, detail=f"{n} direct replications, {pdfs} PDFs")


def probe_download(_state) -> dict:
    inbox = config.INBOX_DIR
    have = len(list(inbox.glob("*.pdf"))) if inbox.exists() else 0
    confirmed = _csv_rows(config.CONFIRMED_REPLICATIONS_CSV) or 0
    failed = _csv_rows(config.DATA_DIR / "failed_dois.csv") or 0
    return _prog("partial" if have else "idle", have, confirmed or None,
                 detail=f"{have} PDFs in inbox awaiting conversion; {failed} known failures")


def probe_convert(_state) -> dict:
    inbox = config.INBOX_DIR
    pending = len(list(inbox.glob("*.pdf"))) if inbox.exists() else 0
    mem = _mem_available_gb()
    grobid = _grobid_up()
    detail = f"{pending} PDFs in inbox; MemAvailable {mem:.0f}GB; GROBID {'up' if grobid else 'down'}"
    return _prog("partial" if pending else "idle", 0, pending, detail=detail,
                 mem_available_gb=mem, grobid_up=grobid)


def probe_extract(state) -> dict:
    tag = (state or {}).get("tag")
    cat = _catalog_stats()
    if cat is None:
        return _prog("idle", detail="no catalog")
    total = cat["total"]
    if not tag:
        extracted = cat["by_status"].get("extracted", 0)
        return _prog("partial", extracted, total,
                     detail=f"{extracted}/{total} papers extracted (set a tag for per-run progress)")
    # Per-tag: how many papers already have this tag.
    done = _catalog_count_tag(tag)
    return _prog("done" if done >= total else "partial", done, total,
                 detail=f"{done}/{total} papers extracted under tag '{tag}'")


def probe_ingest(_state) -> dict:
    vh = config.VERSION_HISTORY_PATH
    latest = None
    if vh.exists():
        for line in vh.read_text().splitlines():
            line = line.split("#")[0].strip()
            if line:
                latest = line
    ck = _csv_rows(config.INGESTION_CHECKPOINT_PATH)
    detail = f"latest DB: {latest or 'none'}"
    if ck:
        detail += f"; checkpoint {ck} rows"
    return _prog("idle", detail=detail, latest_db=latest)


# ── system helpers ───────────────────────────────────────────────────────────
def _mem_available_gb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024 / 1024
    except Exception:
        pass
    return 0.0


def _grobid_up() -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:8070/api/isalive", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def _catalog_stats():
    if not config.CATALOG_PATH.exists():
        return None
    try:
        from mo_pipeline.corpus import catalog
        conn = catalog.connect()
        try:
            return catalog.stats(conn)
        finally:
            conn.close()
    except Exception:
        return None


def _catalog_count_tag(tag: str) -> int:
    if not config.CATALOG_PATH.exists():
        return 0
    from mo_pipeline.corpus import catalog
    conn = catalog.connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE (',' || tags || ',') LIKE ?",
            (f"%,{tag},%",)).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


# ── stage definitions ────────────────────────────────────────────────────────
@dataclass
class Param:
    name: str
    type: str = "int"           # int | str | bool
    default: object = None
    min: int | None = None
    max: int | None = None
    help: str = ""


@dataclass
class Stage:
    id: str
    num: int
    label: str
    description: str
    build_argv: Callable          # (params, state) -> list[str]
    params: list = field(default_factory=list)
    mutex_groups: list = field(default_factory=lambda: ["self"])
    probe: Callable = None
    cwd: str = REPO


def _search_argv(params, state):
    argv = [PY, "-m", "mo_pipeline.discover.search_for_replication_studies"]
    if params.get("max_per_query"):
        argv += ["--max-per-query", str(params["max_per_query"])]
    if params.get("sources"):
        argv += ["--sources", str(params["sources"])]
    return argv


def _module_argv(mod):
    return lambda params, state: [PY, "-m", mod]


def _classify_argv(params, state):
    argv = [PY, "-m", "mo_pipeline.discover.classify_candidates"]
    if params.get("workers"):
        argv += ["--workers", str(params["workers"])]
    if params.get("limit"):
        argv += ["--limit", str(params["limit"])]
    return argv


def _download_argv(params, state):
    argv = [PY, "-m", "mo_pipeline.discover.download_all_confirmed"]
    if params.get("type"):
        argv += ["--type", str(params["type"])]
    if params.get("limit"):
        argv += ["--limit", str(params["limit"])]
    argv += ["--workers", str(params.get("workers", 4))]
    if params.get("legalonly"):
        argv += ["--legalonly"]
    return argv


def _convert_argv(params, state):
    workers = min(int(params.get("workers", 4)), 4)
    return ["pdf4llm", "batch", str(config.INBOX_DIR), "-o", str(config.PAPERS_DIR),
            "--mode", "full-grobid", "--workers", str(workers), "--movepdf", "--resume"]


def _extract_argv(params, state):
    tag = params.get("tag") or (state or {}).get("tag")
    argv = [PY, "-m", "mo_pipeline.extract.extract", str(config.PAPERS_DIR),
            "--batch", "--level", "full", "--workers", str(params.get("workers", 4))]
    if tag:
        argv += ["--tag", tag]
    if params.get("include_list"):
        argv += ["--include-list", str(params["include_list"])]
    if params.get("model"):
        argv += ["--model", str(params["model"])]
    return argv


def _ingest_argv(params, state):
    inp = params.get("input_csv")
    argv = [PY, "-m", "mo_pipeline.ingest.data_ingestor", str(inp),
            "--no-gui", "--workers", str(params.get("workers", 4))]
    if params.get("skip_api_calls"):
        argv += ["--skip-api-calls"]
    return argv


STAGES: list[Stage] = [
    Stage("search", 1, "Search", "Query 7 scholarly sources for replication candidates",
          _search_argv, [Param("max_per_query", "int", 1000, help="cap results per query"),
                         Param("sources", "str", "", help="comma-separated source list (blank=all)")],
          ["self"], probe_search),
    Stage("dedup", 2, "Deduplicate", "Collapse duplicate candidates by DOI/PMID/title",
          _module_argv("mo_pipeline.discover.deduplicate_candidates"), [],
          ["self", "heavy_ram"], probe_dedup),
    Stage("prefilter", 3, "Prefilter", "Keyword keep/exclude down to the classifier set",
          _module_argv("mo_pipeline.discover.prefilter_candidates"), [],
          ["self"], probe_prefilter),
    Stage("classify", 4, "Classify", "LLM classify each candidate as a replication (Haiku)",
          _classify_argv, [Param("workers", "int", 20, 1, 40, "concurrent claude calls"),
                           Param("limit", "int", None, help="only first N unclassified")],
          ["self", "claude_cli"], probe_classify),
    Stage("filter_direct", 5, "Filter direct", "Isolate high-confidence direct replications",
          _module_argv("mo_pipeline.discover.filter_direct_replications"), [],
          ["self"], probe_filter_direct),
    Stage("download", 6, "Download PDFs", "Fetch PDFs for confirmed replications into inbox/",
          _download_argv, [Param("type", "str", "", help="filter by replication type"),
                           Param("limit", "int", None, help="max DOIs to attempt"),
                           Param("workers", "int", 4, 1, 8, "parallel downloads"),
                           Param("legalonly", "bool", False, help="skip Sci-Hub")],
          ["self"], probe_download),
    Stage("convert", 7, "Convert (pdf4llm)", "PDF -> abstract/body/refs into papers/",
          _convert_argv, [Param("workers", "int", 4, 1, 4, "capped at 4 (RAM)")],
          ["self", "heavy_ram"], probe_convert),
    Stage("extract", 8, "Extract", "LLM extract structured replication records (Sonnet)",
          _extract_argv, [Param("tag", "str", "", help="run tag (per-run resume)"),
                          Param("workers", "int", 4, 1, 8, "parallel papers"),
                          Param("include_list", "str", "", help="path to include-list file"),
                          Param("model", "str", "sonnet", help="claude model alias")],
          ["self", "claude_cli"], probe_extract),
    Stage("ingest", 9, "Ingest", "Enrich + merge collated CSV into the website database",
          _ingest_argv, [Param("input_csv", "str", "", help="collated_results_*.csv to ingest"),
                         Param("workers", "int", 4, 1, 8, "parallel enrichment"),
                         Param("skip_api_calls", "bool", False, help="no metadata enrichment")],
          ["self"], probe_ingest),
]

BY_ID = {s.id: s for s in STAGES}


def stage_dict(s: Stage) -> dict:
    return {
        "id": s.id, "num": s.num, "label": s.label, "description": s.description,
        "mutex_groups": s.mutex_groups,
        "params": [{"name": p.name, "type": p.type, "default": p.default,
                    "min": p.min, "max": p.max, "help": p.help} for p in s.params],
    }


if __name__ == "__main__":
    # Tiny CLI: print each stage's probe status.
    state = _json(Path(config.MEDIA_ROOT / "..")) or {}
    for s in STAGES:
        try:
            pr = s.probe({}) if s.probe else {"state": "?"}
        except Exception as e:
            pr = {"state": "error", "detail": str(e)}
        print(f"{s.num}. {s.id:14s} {pr.get('state',''):8s} {pr.get('detail','')}")
