"""
The 7-stage pipeline registry + per-stage progress probes.

Ingestion into the website database is no longer a stage; it is run manually
from metascience_observatory_website/data_ingestor/ against the collated CSV.

Each Stage declares how to launch it (argv builder), what parameters its run
form exposes, which mutex groups it belongs to, and a probe() that reports cheap
read-only progress. Probes never launch anything; they stat files / query the
catalog. The runner (runner.py) owns process lifecycle; this module owns "what
is a stage and how far along is it".
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from mo_pipeline import config

PY = sys.executable
REPO = str(config.REPO_ROOT)


# ── small probe helpers ──────────────────────────────────────────────────────
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
def _search_total_queries() -> int | None:
    """Effective query total across the per-API fan-out (overlay-aware): it
    re-reads keywords.json, so dashboard keyword edits reflect immediately."""
    try:
        from mo_pipeline.discover import keywords as kw
        return kw.total_effective_queries() or None
    except Exception:
        return None


def probe_search(_state) -> dict:
    p = _json(config.SEARCH_PROGRESS_FILE) or {}
    done = len(p.get("completed_queries", []))
    raw = _csv_rows(config.CANDIDATES_RAW_CSV)
    if not done:
        return _prog("idle", detail="no search progress yet")
    total = _search_total_queries()
    return _prog("done" if total and done >= total else "partial",
                 done, total,
                 detail=f"{done}/{total or '?'} queries, {raw or 0} raw candidates")


def probe_dedup(_state) -> dict:
    out = config.CANDIDATES_DEDUP_CSV
    if not out.exists():
        return _prog("idle", detail="no dedup output")
    n = _csv_rows(out)
    return _prog("done", n, n, detail=f"{n} unique candidates")


def probe_prefilter(_state) -> dict:
    out = config.CANDIDATES_FILTERED_CSV
    if not out.exists():
        return _prog("idle", detail="no prefilter output")
    n = _csv_rows(out)
    return _prog("done", n, n, detail=f"{n} kept for classification")


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


def _inbox_count(inbox, pattern: str) -> int:
    """Files matching `pattern` in the inbox: one folder per record, plus the
    root for flat leftovers from before `corpus inbox-subfolders`."""
    if not inbox.exists():
        return 0
    return len(list(inbox.glob(f"*/{pattern}"))) + len(list(inbox.glob(pattern)))


def probe_download(_state) -> dict:
    inbox = config.INBOX_DIR
    have = _inbox_count(inbox, "*.pdf")
    # Stage 5 also retrieves a structured copy per record; only the PDFs are
    # what stage 6 converts, so they alone drive the count and the status.
    structured = _inbox_count(inbox, "*.xml") + _inbox_count(inbox, "*.fulltext.html")
    # The markdown renditions of that structured half are what stage 7 reads as
    # its primary full text, so a structured count without them overstates what
    # extraction can actually use.
    rendered = _inbox_count(inbox, "*_from_xml.md") + _inbox_count(inbox, "*_from_html.md")
    confirmed = _csv_rows(config.CONFIRMED_REPLICATIONS_CSV) or 0
    # fetchpdf appends failures to the output dir it was given, i.e. the inbox.
    failed = _csv_rows(inbox / "failed_dois.csv") or 0
    return _prog("partial" if have else "idle", have, confirmed or None,
                 detail=f"{have} PDFs in inbox awaiting conversion; "
                        f"{structured} XML/HTML alongside ({rendered} rendered to markdown); "
                        f"{failed} known failures")


def probe_convert(_state) -> dict:
    inbox = config.INBOX_DIR
    pending = _inbox_count(inbox, "*.pdf")
    mem = _mem_available_gb()
    from .grobid import state as _grobid_state
    grobid_state = _grobid_state()
    detail = (f"{pending} PDFs in inbox; MemAvailable {mem:.0f}GB; "
              f"GROBID {grobid_state.replace('_', ' ')}")
    return _prog("partial" if pending else "idle", 0, pending, detail=detail)


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
    """Kept as the name main.py imports; the implementation lives in grobid.py."""
    from .grobid import is_up
    return is_up()


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
    if params.get("doi_csv"):
        argv += ["--doi-csv", str(params["doi_csv"])]
    if params.get("type"):
        argv += ["--type", str(params["type"])]
    if params.get("limit"):
        argv += ["--limit", str(params["limit"])]
    argv += ["--workers", str(params.get("workers", 4))]
    if params.get("legalonly"):
        argv += ["--legalonly"]
    if params.get("include_published"):
        argv += ["--include-published"]
    if params.get("cookies"):
        argv += ["--cookies", str(Path(str(params["cookies"])).expanduser())]
    if params.get("no_cookies"):
        argv += ["--no-cookies"]
    if params.get("cookies_only"):
        argv += ["--cookies-only"]
    return argv


def _convert_argv(params, state):
    workers = min(int(params.get("workers", 4)), 4)
    return ["pdf4llm", "batch", str(config.INBOX_DIR), "-o", str(config.PAPERS_DIR),
            "--mode", "full-grobid", "--workers", str(workers), "--movepdf", "--resume"]


def _extract_argv(params, state):
    tag = params.get("tag") or (state or {}).get("tag")
    if params.get("core"):
        # Single-shot core-fields extractor: no statistics, one no-tools model
        # call per paper (mo_pipeline/extract/extract_core.py). Same tag/include
        # -list/model/collate flags; --provider is core-only.
        argv = [PY, "-m", "mo_pipeline.extract.extract_core", str(config.PAPERS_DIR),
                "--workers", str(params.get("workers", 4))]
        if params.get("provider"):
            argv += ["--provider", str(params["provider"])]
    else:
        argv = [PY, "-m", "mo_pipeline.extract.extract", str(config.PAPERS_DIR),
                "--batch", "--level", str(params.get("level", "full")), "--workers", str(params.get("workers", 4))]
        if params.get("usecodex"):
            argv += ["--usecodex"]
    if tag:
        argv += ["--tag", tag]
    if params.get("include_list"):
        argv += ["--include-list", str(params["include_list"])]
    if params.get("model"):
        argv += ["--model", str(params["model"])]
    if params.get("collate_only"):
        argv += ["--collate-only"]
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
    Stage("classify", 4, "Classify", "LLM screen each candidate abstract: replication or not, and its type",
          _classify_argv, [Param("workers", "int", 20, 1, 40, "concurrent claude calls"),
                           Param("limit", "int", None, help="only first N unclassified")],
          ["self", "claude_cli"], probe_classify),
    Stage("download", 5, "Download", "Fetch full text for confirmed replications into inbox/",
          _download_argv, [Param("type", "str", ",".join(config.DOWNLOAD_REPLICATION_TYPES),
                                 help="replication types, comma-separated ('all' = no filter)"),
                           Param("include_published", "bool", False,
                                 help="also fetch papers already in the published database"),
                           Param("limit", "int", None, help="max DOIs to attempt"),
                           Param("workers", "int", 4, 1, 8, "parallel downloads"),
                           Param("legalonly", "bool", False, help="skip Sci-Hub"),
                           Param("doi_csv", "str", "", help="CSV of DOIs to fetch instead "
                                 "of confirmed set (see DOI runs page)"),
                           Param("cookies", "str", "", help="cookie file instead of the "
                                 "`get-cookies setup` access"),
                           Param("no_cookies", "bool", False, help="skip institutional access"),
                           Param("cookies_only", "bool", False, help="institutional access "
                                 "only, for DOIs that already failed")],
          # `inbox`: convert moves PDFs out of inbox/ (--movepdf) while download
          # writes into it; the two must never overlap.
          ["self", "inbox"], probe_download),
    Stage("convert", 6, "Convert (pdf4llm)", "PDF -> abstract/body/refs into papers/",
          _convert_argv, [Param("workers", "int", 4, 1, 4, "capped at 4 (RAM)")],
          ["self", "heavy_ram", "inbox"], probe_convert),
    Stage("extract", 7, "Extract", "LLM extract structured replication records (agentic, or single-shot core)",
          _extract_argv, [Param("tag", "str", "", help="run tag (per-run resume)"),
                          Param("workers", "int", 4, 1, 8, "parallel papers"),
                          Param("include_list", "str", "", help="path to include-list file"),
                          Param("model", "str", "sonnet", help="model ID for the selected CLI/provider"),
                          Param("level", "str", "full", help="agentic extraction: full or base (no statistics)"),
                          Param("usecodex", "bool", False, help="agentic extraction via Codex CLI; set model explicitly"),
                          Param("collate_only", "bool", False,
                                help="only collate existing results for the tag into a CSV"),
                          Param("core", "bool", False,
                                help="core fields only: single-shot extractor, no statistics "
                                     "(extract_core.py)"),
                          Param("provider", "str", "",
                                help="core only: claude_cli (default) or openrouter "
                                     "(then set model to an OpenRouter slug)")],
          ["self", "claude_cli"], probe_extract),
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
    for s in STAGES:
        try:
            pr = s.probe({}) if s.probe else {"state": "?"}
        except Exception as e:
            pr = {"state": "error", "detail": str(e)}
        print(f"{s.num}. {s.id:14s} {pr.get('state',''):8s} {pr.get('detail','')}")
