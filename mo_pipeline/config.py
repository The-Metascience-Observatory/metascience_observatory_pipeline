"""
Unified configuration for the Metascience Observatory replication pipeline.

Single source of truth for every path and tunable used by the discover (stages
1-6), extract (stage 8), and ingest (stage 9) modules. Consolidates the three
previously-independent config surfaces:
  - pull_replication_studies/config.py          (discover stages)
  - claude_code_replications/extract.py globals  (extract stage)
  - data_ingestor.py SCRIPT_DIR-relative paths   (ingest stage)

Environment overrides (for scratch-dir smoke tests without touching live data):
  MO_DATA_DIR      -> DATA_DIR      (candidate CSVs, in-repo)
  MO_PROGRESS_DIR  -> PROGRESS_DIR  (search/classify checkpoints, in-repo)
  MO_MEDIA_ROOT    -> MEDIA_ROOT    (external PDF drive)

Run `python -m mo_pipeline.config` for a self-check that prints every path and
warns on anything that does not exist.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# ── Anchors ──────────────────────────────────────────────────────────────────
# REPO_ROOT = mo_pipeline/ (this file lives at mo_pipeline/mo_pipeline/config.py)
REPO_ROOT = Path(__file__).resolve().parent.parent
OBSERVATORY_ROOT = REPO_ROOT.parent  # /home/dan/Dropbox/AAA_METASCIENCE_OBSERVATORY

MEDIA_ROOT = Path(os.environ.get(
    "MO_MEDIA_ROOT", "/media/dan/500Gb/metascience_observatory_pdfs"))
DATA_DIR = Path(os.environ.get("MO_DATA_DIR", REPO_ROOT / "data"))
PROGRESS_DIR = Path(os.environ.get("MO_PROGRESS_DIR", REPO_ROOT / "progress"))
PROMPTS_DIR = REPO_ROOT / "prompts"

# Back-compat alias: the discover scripts historically imported BASE_DIR.
BASE_DIR = REPO_ROOT

# ── External PDF drive layout (new corpus organization — see mo_pipeline.corpus) ─
CATALOG_PATH = MEDIA_ROOT / "corpus.sqlite"
INBOX_DIR = MEDIA_ROOT / "inbox"          # stage-6 output: inbox/{doi}/{doi}.* awaiting conversion
PAPERS_DIR = MEDIA_ROOT / "papers"        # the corpus: one folder per DOI
SPECIAL_DIR = MEDIA_ROOT / "special"      # pre-pipeline corpora (acm_AIS, questionable)
LEGACY_DIR = MEDIA_ROOT / "legacy"        # dup losers / oddities from migration

# Root of the already-ingested corpus (legacy layout, pre-reorg). This is the
# **ingested** dir, NOT the historical "have_been_ingested" typo that several
# scripts referenced — fixing it means the ingested-scan actually finds papers
# and correctly shrinks the download queue.
INGESTED_ROOT = MEDIA_ROOT / "ingested"

# The batch dir stage-6 downloads write to today (pre-reorg). Post-reorg this
# becomes INBOX_DIR; kept for the transition.
CURRENT_BATCH_DIR = MEDIA_ROOT / "8th_batch"

# ── API settings ─────────────────────────────────────────────────────────────
ENTREZ_EMAIL = "delton17@gmail.com"
NCBI_DELAY = 0.34          # seconds between NCBI API calls (their rate limit)
OPENALEX_DELAY = 0.15      # seconds between OpenAlex calls
EUROPEPMC_DELAY = 0.2      # seconds between Europe PMC calls
CROSSREF_DELAY = 0.25      # seconds between Crossref calls (polite pool, ~4 req/s)
OSF_DELAY = 0.7            # seconds between OSF API calls (~85 req/min)
S2_DELAY = 3.0             # seconds between Semantic Scholar calls (unauthenticated)
S2_API_KEY = ""            # optional Semantic Scholar API key

# ── LLM / Claude Code CLI ────────────────────────────────────────────────────
# Uses `claude -p` (non-interactive Claude Code CLI) with the Max subscription.
# Model aliases: "haiku" (Haiku 4.5), "sonnet" (Sonnet 4.6), "opus" (Opus 4.6)
LLM_MODEL = "haiku"
LLM_TIMEOUT_SEC = 120

# ── Stage-4 screening backend ────────────────────────────────────────────────
# The Claude CLI is free on the Max plan but rate-budgeted (~10k screening
# calls/week) and shares the `claude_cli` mutex group with extraction, so a
# large sweep starves the rest of the pipeline. `openrouter` trades money for
# throughput: ~$46 per 1M screens on gpt-5-nano, and it does not touch the
# Claude rate limit. Both are overridable per-run via --provider/--model, and
# by env vars of the same name. See discover/screening_backend.py.
# Benchmarked 2026-08-09 on 60 known FLoRa replications + 60 Haiku-negatives +
# 8 adversarial non-replications (see benchmarking/README.md, "Screening-model
# eval" section — the numbers below are the only surviving record of that run):
#   ling-2.6-flash  100% recall, 0/8 false positives, 0 hallucinated flags, $1.28/100k
#   haiku-4.5       100% recall but $169/100k via OpenRouter, and >=18% of its
#                   own high-confidence negatives are demonstrably wrong
#   gpt-5.6-luna     93% recall at $20/100k — worst of the set
# Claude CLI remains free-but-rate-limited (~10k/week, shares the claude_cli mutex).
SCREENING_PROVIDER = "openrouter"          # "claude_cli" | "openrouter"
SCREENING_MODEL = "inclusionai/ling-2.6-flash"

# ── Stage-8 core-fields extractor (extract/extract_core.py) ─────────────────
# Single-shot, no-tools sibling of extract.py that records NO statistics. One
# model call per paper: Python assembles abstract + full text + reference list
# and parses the JSON reply. Defaults to the Claude CLI on the Max plan (free,
# rate-limited, holds the `claude_cli` mutex); `--provider openrouter --model
# <slug>` moves it off the Claude rate pool. Overridable per run via CLI flags
# and by env vars of the same name.
EXTRACT_CORE_PROVIDER = "claude_cli"      # "claude_cli" | "openrouter"
EXTRACT_CORE_MODEL = "sonnet"             # claude_cli alias; OpenRouter needs an explicit --model
EXTRACT_CORE_TIMEOUT_SEC = 600            # one call carries a whole paper; LLM_TIMEOUT_SEC is too short
EXTRACT_CORE_MAX_INPUT_CHARS = 350_000    # ~90k tokens; the p90 paper is 75 KB, so rarely hit
# OpenRouter max_tokens. 16k, not 8k: a reasoning model spends most of its budget
# before it emits anything, and a parallel session measured 8000 still hitting
# `finish_reason: length` on a comparable coding task. It is a cap, not a target,
# so the cost of being generous is nothing and the cost of being tight is a
# silently truncated extraction.
EXTRACT_CORE_MAX_OUTPUT_TOKENS = 16384

# ── OpenAlex biomedical concept IDs ──────────────────────────────────────────
BIOMED_CONCEPT_IDS = [
    "C86803240",   # Biology
    "C71924100",   # Medicine
    "C126322002",  # Biochemistry
    "C185592680",  # Pharmacology
    "C159110408",  # Neuroscience
    "C54355233",   # Clinical Medicine
]

# ── Discover-stage output files (data/) ──────────────────────────────────────
CANDIDATES_RAW_CSV = DATA_DIR / "candidates_raw.csv"
CANDIDATES_DEDUP_CSV = DATA_DIR / "candidates_dedup.csv"
CANDIDATES_FILTERED_CSV = DATA_DIR / "candidates_filtered.csv"
CLASSIFIED_CSV = DATA_DIR / "classified.csv"
CONFIRMED_REPLICATIONS_CSV = DATA_DIR / "confirmed_replications.csv"
DOWNLOAD_STATUS_CSV = DATA_DIR / "download_status.csv"
PROCESSED_MANIFEST_CSV = DATA_DIR / "processed_manifest.csv"
DIRECT_REPLICATIONS_CSV = DATA_DIR / "direct_replications.csv"
CITATION_MINED_CSV = DATA_DIR / "citation_mined_candidates.csv"

# ── Keyword yield stats (derived, cached — see discover/keyword_stats.py) ────
KEYWORD_STATS_JSON = DATA_DIR / "keyword_stats.json"
KEYWORD_STATS_HISTORY_JSONL = DATA_DIR / "keyword_stats_history.jsonl"

# ── DOI-list runs (dashboard "run pipeline on a set of DOIs") ────────────────
# One folder per named run: dois.csv (normalized list), include_list.txt
# (paper-folder names for extract --include-list), meta.json.
DOI_RUNS_DIR = DATA_DIR / "doi_runs"

# ── Discover-stage progress checkpoints (progress/) ──────────────────────────
SEARCH_PROGRESS_FILE = PROGRESS_DIR / "search_progress.json"
CLASSIFY_PROGRESS_FILE = PROGRESS_DIR / "classify_progress.json"
CITATION_MINE_PROGRESS_FILE = PROGRESS_DIR / "citation_mine_progress.json"

# ── PDF cache locations ──────────────────────────────────────────────────────
# Local scratch cache of downloaded PDFs (referenced by absolute path; stays in
# pull_replication_studies/ even after code consolidation).
PDF_DIR = Path("/home/dan/downloaded_pdfs")
DIRECT_REPLICATIONS_PDF_DIR = PDF_DIR / "very_likely_direct_replications"

# ONE canonical search list, replacing 4 divergent copies. Dead entries dropped
# (7th_batch, pdfgrep_batch no longer exist at MEDIA_ROOT; pull_replication_studies/
# downloaded_pdfs + manually_classified_PDFs archived — redundant with the corpus).
# Order = priority.
PDF_SEARCH_DIRS = [
    PDF_DIR,
    OBSERVATORY_ROOT / "PDFs",
    OBSERVATORY_ROOT / "agent_for_replications" / "ground_truth_dataset_PDFs",
    OBSERVATORY_ROOT / "pull_long_covid_papers" / "pdfs",
    CURRENT_BATCH_DIR,
]

# ── Website integration (extract reads the ontology; the DB lives here) ──────
# Ingestion itself runs manually from metascience_observatory_website/
# data_ingestor/ — the pipeline only reads these paths (ontology, latest-DB
# lookup for --dontcheck style skips). MO_WEBSITE_DATA_DIR override lets
# tests/dry-runs redirect to a scratch copy instead of the live site.
WEBSITE_ROOT = OBSERVATORY_ROOT / "metascience_observatory_website"
WEBSITE_DATA_DIR = Path(os.environ.get(
    "MO_WEBSITE_DATA_DIR", WEBSITE_ROOT / "data"))
ONTOLOGY_PATH = WEBSITE_DATA_DIR / "metascience_observatory_topic_ontology.json"
VERSION_HISTORY_PATH = WEBSITE_DATA_DIR / "version_history.txt"


def latest_replications_db() -> Path | None:
    """The newest `replications_database_*.csv`, or None if it cannot be found.

    Resolved at call time, never pinned to a constant. The ingestor writes a new
    dated CSV on every ingest, so any constant goes stale the moment one lands --
    and a stale one fails silently, because every caller guards it with an
    `exists()` check. That is exactly what happened: this used to be
    LEGACY_REPLICATIONS_DB, pinned to a 2026-01-28 snapshot in a directory that
    no longer exists, which disabled stage 5's already-published exclusion
    without a word for months.

    version_history.txt lists one filename per line, newest last, with optional
    `#` comments recording what each ingest changed.
    """
    if not VERSION_HISTORY_PATH.exists():
        return None
    latest = None
    for line in VERSION_HISTORY_PATH.read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            latest = line
    if not latest:
        return None
    path = WEBSITE_DATA_DIR / latest
    return path if path.exists() else None

# ── Benchmarking (extraction accuracy + discovery recall harnesses) ──────────
# See benchmarking/README.md. Ground truth lives in gold/ (adjudicated) and
# silver/ (external label sets); results/ holds dated evaluation runs; cache/
# holds the LLM-matcher decision cache (gitignored, safe to delete).
BENCHMARKING_DIR = REPO_ROOT / "benchmarking"
BENCH_GOLD_DIR = BENCHMARKING_DIR / "gold"
BENCH_SILVER_DIR = BENCHMARKING_DIR / "silver"
BENCH_RESULTS_DIR = BENCHMARKING_DIR / "results"
BENCH_CACHE_DIR = BENCHMARKING_DIR / "cache"
MATCH_PROMPT_FILE = PROMPTS_DIR / "prompt_match.md"
MATCH_VERSION_FILE = PROMPTS_DIR / "version_match.txt"

# ── Prompts (extract stage) ──────────────────────────────────────────────────
# Normal mode is "full". "base" is prompt_full.md rendered with its statistics
# blocks stripped (the agentic control arm for extract_core); "core" is the
# single-shot extract_core.py prompt, also stat-free. Dead "mid" was dropped
# (nonexistent prompt_mid.md). See extract._render_mode for the
# <!-- mode:X --> markers the renderer honours.
PROMPT_FILES = {
    "full": PROMPTS_DIR / "prompt_full.md",
    "base": PROMPTS_DIR / "prompt_full.md",
    "core": PROMPTS_DIR / "prompt_core.md",
    "pdf_only": PROMPTS_DIR / "prompt_full_pdf_only.md",
    "html": PROMPTS_DIR / "prompt_full_html.md",
    "xml": PROMPTS_DIR / "prompt_full_xml.md",
}
PROMPT_SHARED_CORE = PROMPTS_DIR / "prompt_shared_core.md"
SCREEN_PROMPT_FILE = PROMPTS_DIR / "prompt_screen.md"
EXTRACTOR_VERSION_FILE = PROMPTS_DIR / "version.txt"

# ── Secrets ──────────────────────────────────────────────────────────────────
ENV_FILE = REPO_ROOT / ".env.local"


def _self_check() -> None:
    """Print every configured path and flag ones that do not exist."""
    groups = {
        "Anchors": [
            ("REPO_ROOT", REPO_ROOT), ("OBSERVATORY_ROOT", OBSERVATORY_ROOT),
            ("MEDIA_ROOT", MEDIA_ROOT), ("DATA_DIR", DATA_DIR),
            ("PROGRESS_DIR", PROGRESS_DIR), ("PROMPTS_DIR", PROMPTS_DIR),
        ],
        "Corpus (drive)": [
            ("CATALOG_PATH", CATALOG_PATH), ("INBOX_DIR", INBOX_DIR),
            ("PAPERS_DIR", PAPERS_DIR), ("SPECIAL_DIR", SPECIAL_DIR),
            ("LEGACY_DIR", LEGACY_DIR), ("INGESTED_ROOT", INGESTED_ROOT),
            ("CURRENT_BATCH_DIR", CURRENT_BATCH_DIR),
        ],
        "Discover outputs": [
            ("CANDIDATES_RAW_CSV", CANDIDATES_RAW_CSV),
            ("CANDIDATES_DEDUP_CSV", CANDIDATES_DEDUP_CSV),
            ("CANDIDATES_FILTERED_CSV", CANDIDATES_FILTERED_CSV),
            ("CLASSIFIED_CSV", CLASSIFIED_CSV),
            ("CONFIRMED_REPLICATIONS_CSV", CONFIRMED_REPLICATIONS_CSV),
            ("PROCESSED_MANIFEST_CSV", PROCESSED_MANIFEST_CSV),
        ],
        "Checkpoints": [
            ("SEARCH_PROGRESS_FILE", SEARCH_PROGRESS_FILE),
            ("CLASSIFY_PROGRESS_FILE", CLASSIFY_PROGRESS_FILE),
        ],
        "Website integration": [
            ("WEBSITE_DATA_DIR", WEBSITE_DATA_DIR),
            ("ONTOLOGY_PATH", ONTOLOGY_PATH),
            ("VERSION_HISTORY_PATH", VERSION_HISTORY_PATH),
            ("latest_replications_db()", latest_replications_db() or
             Path("<unresolved: stage 5 will not exclude published papers>")),
        ],
        "Prompts": [
            ("PROMPT_SHARED_CORE", PROMPT_SHARED_CORE),
            ("SCREEN_PROMPT_FILE", SCREEN_PROMPT_FILE),
            ("EXTRACTOR_VERSION_FILE", EXTRACTOR_VERSION_FILE),
            *[(f"PROMPT_FILES[{k}]", v) for k, v in PROMPT_FILES.items()],
        ],
        "Secrets": [("ENV_FILE", ENV_FILE)],
    }
    # Paths that are legitimately absent (created on demand, or retired by the
    # corpus reorg — INGESTED_ROOT/CURRENT_BATCH_DIR were drained into papers/).
    expected_absent = {
        "CATALOG_PATH", "INBOX_DIR", "PAPERS_DIR", "SPECIAL_DIR", "LEGACY_DIR",
        "CITATION_MINED_CSV",
        "INGESTED_ROOT", "CURRENT_BATCH_DIR",
    }
    missing = 0
    for group, items in groups.items():
        print(f"\n{group}")
        for name, path in items:
            ok = Path(path).exists()
            mark = "ok " if ok else ("--  " if name in expected_absent else "!! ")
            if not ok and name not in expected_absent:
                missing += 1
            print(f"  [{mark}] {name:28s} {path}")
    print(f"\n{'OK — all expected paths present' if missing == 0 else f'{missing} unexpected missing path(s)'}")


if __name__ == "__main__":
    _self_check()
