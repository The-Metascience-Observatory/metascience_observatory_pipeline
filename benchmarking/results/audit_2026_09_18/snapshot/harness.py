#!/usr/bin/env python3
"""Extraction benchmark harness: one evaluator, provenance-guarded ground truth,
LLM-assisted one-to-one matching, bootstrap CIs, and a gold-set build toolchain.

Ground truth tiers (benchmarking/README.md):
  gold    benchmarking/gold/gold_rows.csv + gold_negatives.csv — double-coded and
          adjudicated by humans against benchmarking/codebook.md; the only set
          that supports 4-value result scoring (incl. reversal), replication_type,
          statistics, and paper-level precision.
  silver  benchmarking/silver/*.csv — external label sets (FLoRa, FReD v2.4.2,
          the Feb-2026 FReD-API rows) kept verbatim with a `provenance` column.
  quarantine  archive/legacy_feb2026/ — never scored. Any GT row whose
          provenance starts with `pipeline:` is refused unless --allow-dirty-gt.

Subcommands
  import-flora / import-fred   xlsx -> silver csv (idempotent)
  sample        build the gold_v<N> sampling frame + doi run (Phase 2b sources)
  coding-sheet  blinded per-coder sheet from the frame
  agreement     coder A vs B kappas + adjudication queue (pre-registered ladder)
  adjudicate    interactive adjudication of the queue
  build-gold    sheets + adjudication -> gold_rows.csv / gold_negatives.csv / manifest.json
  status        coverage funnel for a doi run (downloaded / converted / extracted)
  run           launch extraction for a run with --dontcheck always on
  evaluate      score a run's extractions against gold or a silver set
  retest        run-to-run agreement between two tags on the same papers
  match-audit   blinded sheet of LLM match decisions for human audit + scoring

Typical release loop
  python benchmarking/harness.py run --run gold_v1 --tag gold1_p86_sonnet_r1
  python benchmarking/harness.py run --run gold_v1 --tag gold1_p86_sonnet_r2
  python benchmarking/harness.py evaluate --gt gold --run gold_v1 --tags gold1_p86_sonnet_r1 --split dev
  python benchmarking/harness.py retest  --run gold_v1 --tag-a gold1_p86_sonnet_r1 --tag-b gold1_p86_sonnet_r2
  python benchmarking/harness.py evaluate --gt gold --run gold_v1 --tags gold1_p86_sonnet_r1 --split test --release
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import urllib.error
import urllib.request
import math
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH_DIR.parent))
sys.path.insert(1, str(BENCH_DIR))

from mo_pipeline import config  # noqa: E402
from mo_pipeline.corpus.models import doi_to_folder  # noqa: E402
from mo_pipeline.discover.doi_runs import (create_run, delete_run, load_dois,  # noqa: E402
                                           normalize_doi, run_status)
from mo_pipeline.label_centrality.collate import cohen_kappa  # noqa: E402

import matching  # noqa: E402
from matching import (MatchJudge, assign, authors_match, doi_of, fuzzy_match,  # noqa: E402
                      string_ratio, year_match)

csv.field_size_limit(sys.maxsize)

# 1.1 (2026-09-02): ground-truth corrections sidecar, canonical DOI aliases,
# paper-level result aggregation, the wrong-document-on-disk bucket, parallel
# matching.
# 1.3 (2026-09-03): gold may be built by two AI coders with human adjudication, so
# `build-gold` stamps `human:adjudicated` only on rows a human actually ruled on and
# `ai_consensus:<a>+<b>` on the rest; the manifest records the coder ids and whether
# statistics were coded at all. Coding sheets can omit the statistical columns, and
# a report whose GT carries no statistics says so instead of printing zeros.
# 1.2 (2026-09-03): effect-level ground truth declares whether it covers every
# effect in a paper. None of the external sets do -- a coder writes down the
# replication the paper is about, not each of its sub-analyses -- so their entry
# precision is a lower bound, and the per-paper aggregate that paper-level GT
# already got is now reported for them too. Strict numbers are unchanged.
# Bump when scoring semantics change: the value is recorded in every
# metrics.json, so two runs are only comparable at the same harness version.
HARNESS_VERSION = "1.3"
GOLD_DIR = config.BENCH_GOLD_DIR
SILVER_DIR = config.BENCH_SILVER_DIR
RESULTS_DIR = config.BENCH_RESULTS_DIR
CODING_DIR = GOLD_DIR / "coding"
CODEBOOK = BENCH_DIR / "codebook.md"
TEST_LEDGER = RESULTS_DIR / "test_ledger.jsonl"
FLORA_XLSX = BENCH_DIR.parent / "flora_replications_for_extraction_testing.xlsx"
FRED_XLSX = BENCH_DIR.parent / "fred_v2_4_2_replications_for_extraction_testing.xlsx"

RESULT_VALUES = ("success", "failure", "inconclusive", "reversal")
TYPE_VALUES = ("direct", "close experiment", "close extension", "conceptual")
TYPE_ORDER = {t: i for i, t in enumerate(TYPE_VALUES)}
TYPE_COLLAPSE = {"direct or close": "direct_or_close", "close": "close experiment"}
STAT_FIELDS = ["original_n", "original_es", "original_es_type", "original_es_95_CI",
               "original_p_value", "original_p_value_type", "original_p_value_tails",
               "replication_n", "replication_es", "replication_es_type",
               "replication_es_95_CI", "replication_p_value", "replication_p_value_type",
               "replication_p_value_tails"]
NUMERIC_STATS = ("original_n", "original_es", "original_p_value",
                 "replication_n", "replication_es", "replication_p_value")
CODED_FIELDS = ["result", "replication_type", "original_url", "original_title",
                "original_authors", "original_year", "original_journal", "description",
                "citation_sentence"] + STAT_FIELDS
RESULT_SUFFIXES = ("_result_xml.json", "_result_html.json", "_result_pdf_only.json",
                   "_result_full.json", "_result_mid.json", "_result.json",
                   "_result_core.json")   # stat-free core extractor (--level core)

DISCIPLINE_GROUPS = {
    "psych": {"psychology"},
    "biomed": {"medical fields", "neuroscience", "biology", "microbiology",
               "sports and exercise science", "biochemistry", "genetics"},
    "socsci": {"economics", "political science", "business & management", "sociology",
               "criminology", "public administration", "anthropology"},
    "lang_edu": {"linguistics", "education", "computer science education"},
}
SEED_DEFAULT = 20260902
SPLIT_TEST_FRAC = 0.6

# Columns a blinded coding sheet may contain. Anything else is a blinding leak.
SHEET_ALLOWED = {"row_id", "replication_doi", "paper_folder", "external_row_id", "original_hint",
                 "is_replication_paper", "why_negative", "gt_ambiguity", "notes",
                 *CODED_FIELDS}


# ── small utils ──────────────────────────────────────────────────────────────
def read_csv(p: Path) -> list[dict]:
    with open(p, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(p: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(rows[0].keys()) if rows else []
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


LEGACY_GT_PAPERS = BENCH_DIR / "ground_truth_data_filtered_PDFs"


def _has_fulltext(d: Path) -> bool:
    return d.is_dir() and (any(d.glob("*_from_xml.md")) or any(d.glob("*_from_html.md"))
                           or (d / "body.md").exists() or any(d.glob("*.pdf"))
                           or any(d.glob("*.xml")))


def paper_dir_for(folder: str) -> Path | None:
    """Where a paper is readable, or None. The corpus first, then the frozen
    legacy ground-truth corpus: the 27 main_gt_human papers were never migrated
    into `papers/`, so looking only at PAPERS_DIR silently drops the one stratum
    with human coding already attached to it.
    """
    for root in (config.PAPERS_DIR, LEGACY_GT_PAPERS):
        d = root / folder
        if _has_fulltext(d):
            return d
    return None


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest() if Path(p).exists() else ""


def norm_label(v) -> str:
    return (str(v) if v is not None else "").strip().lower()


def to_float(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def discipline_group(discipline: str) -> str:
    d = norm_label(discipline)
    for g, members in DISCIPLINE_GROUPS.items():
        if d in members:
            return g
    return "other" if d else "unknown"


def year_bucket(y) -> str:
    v = to_float(y)
    if v is None:
        return "unknown"
    return "<=2018" if v <= 2018 else ("2019-2022" if v <= 2022 else "2023+")


def split_of(doi: str, salt: str, frac_test: float = SPLIT_TEST_FRAC) -> str:
    h = hashlib.sha256(f"{salt}:{doi}".encode()).hexdigest()[:8]
    return "test" if int(h, 16) / 2**32 < frac_test else "dev"


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=config.REPO_ROOT, capture_output=True,
                              text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


# ── ground-truth loading ─────────────────────────────────────────────────────
def _gt_row(raw: dict, *, source: str, granularity: str, has_reversal_class: bool,
            effects_complete: bool = False, split: str = "all") -> dict:
    rep = normalize_doi(raw.get("replication_doi_norm") or raw.get("replication_doi")
                        or raw.get("replication_url") or "") or ""
    orig = normalize_doi(raw.get("original_doi_norm") or raw.get("original_doi")
                         or raw.get("original_url") or "") or ""
    rtype = norm_label(raw.get("replication_type"))
    row = {
        "row_id": raw.get("row_id") or hashlib.sha1(
            "\x1f".join([raw.get("original_url", ""), raw.get("replication_url", ""),
                         raw.get("description", "")]).encode()).hexdigest()[:10],
        "replication_doi": rep,
        "paper_folder": raw.get("paper_folder") or (doi_to_folder(rep) if rep else ""),
        "source": raw.get("source_bucket") or source,
        "provenance": raw.get("provenance") or "",
        "split": raw.get("split") or split,
        "granularity": raw.get("granularity") or granularity,
        "has_reversal_class": has_reversal_class,
        "effects_complete": effects_complete,
        "discipline_group": raw.get("discipline_group") or discipline_group(
            raw.get("discipline") or raw.get("openalex_field") or ""),
        "year_bucket": raw.get("year_bucket") or year_bucket(raw.get("replication_year")),
        "external_row_id": raw.get("external_row_id") or "",
        "gt_ambiguity": norm_label(raw.get("gt_ambiguity")) or "",
        "original_doi": orig,
        "original_url": raw.get("original_url") or "",
        "original_title": raw.get("original_title") or "",
        "original_authors": raw.get("original_authors") or "",
        "original_year": str(raw.get("original_year") or "").replace(".0", ""),
        "original_journal": raw.get("original_journal") or "",
        "description": raw.get("description") or "",
        "citation_sentence": raw.get("citation_sentence") or "",
        "result": norm_label(raw.get("result")),
        "replication_type": TYPE_COLLAPSE.get(rtype, rtype),
        "tier_available": raw.get("tier_available") or "",
    }
    for f in STAT_FIELDS:
        row[f] = (raw.get(f) or "").strip() if raw.get(f) is not None else ""
    return row


# `effects_complete` says the set records EVERY replicated effect in each paper.
# No external set does: FLoRa labels the paper, and the FReD and human coders wrote
# down the replication a paper is about, not each sub-analysis of it. Where it is
# False an extra extracted row may be a real effect nobody coded, so entry
# precision is reported as a lower bound and the per-paper aggregate is shown
# beside the strict score. Only gold, built to the codebook, claims completeness.
SILVER_SPECS = {
    "flora": dict(file="flora.csv", granularity="paper", has_reversal_class=False,
                  effects_complete=False),
    "fred_v242": dict(file="fred_v2_4_2.csv", granularity="effect", has_reversal_class=False,
                      effects_complete=False),
    "main_gt_fred_api": dict(file="main_gt_fred_api.csv", granularity="effect", has_reversal_class=False,
                             effects_complete=False),
    "main_gt_human": dict(file="main_gt_human.csv", granularity="effect", has_reversal_class=False,
                          effects_complete=False),
}


def apply_gt_corrections(raws: list[dict], path: Path) -> tuple[list[dict], int]:
    """Apply a corrections sidecar to raw GT rows, before _gt_row normalises them.

    An external ground-truth CSV is a verbatim import and stays that way: the
    corrections live beside it, each with the quote from the paper that justifies
    it, so they are auditable and reversible. `set` overwrites one field, `drop`
    removes a row (a duplicate, say), `flag` marks a row ambiguous so reporting
    can show a clear slice without changing any score. Corrected rows keep their
    external provenance with `;corrected:<date>` appended, so the
    pipeline-provenance guard still sees them for what they are.
    """
    if not path.exists():
        return raws, 0
    by_row: dict[str, list[dict]] = defaultdict(list)
    for c in read_csv(path):
        rid = (c.get("row_id") or "").strip()
        if rid:
            by_row[rid].append(c)
    if not by_row:
        return raws, 0
    out, applied = [], 0
    for raw in raws:
        cs = by_row.get((raw.get("row_id") or "").strip())
        if not cs:
            out.append(raw)
            continue
        r, dropped = dict(raw), False
        for c in cs:
            action = (c.get("action") or "set").strip().lower()
            value = c.get("value") or ""
            if action == "drop":
                dropped = True
            elif action == "flag":
                r["gt_ambiguity"] = value or "boundary"
            elif action == "set":
                field = (c.get("field") or "").strip()
                if not field:
                    continue
                r[field] = value
                # Keep the DOI's two spellings in step; _gt_row reads either.
                if field == "original_url":
                    r["original_doi_norm"] = normalize_doi(value) or ""
                elif field == "original_doi_norm":
                    r["original_url"] = value if value.startswith("http") else f"https://doi.org/{value}"
            else:
                continue
            applied += 1
            stamp = f";corrected:{(c.get('date') or '').strip()}".rstrip(":")
            if stamp not in (r.get("provenance") or ""):
                r["provenance"] = (r.get("provenance") or "") + stamp
        if not dropped:
            out.append(r)
    return out, applied


def load_ground_truth(spec: str, split: str = "all", allow_dirty: bool = False,
                      gt_path: Path | None = None,
                      allow_paper_level: bool = False) -> tuple[list[dict], list[dict], dict]:
    """Returns (positive rows, negative papers, info). `spec` is gold | silver:<name> | a csv path."""
    negatives: list[dict] = []
    info = {"spec": spec, "files": {}}
    if spec == "gold":
        p = GOLD_DIR / "gold_rows.csv"
        if not p.exists():
            sys.exit(f"ERROR: {p} not found — build the gold set first (harness.py build-gold).")
        raws = read_csv(p)
        rows = [_gt_row(r, source=r.get("source") or "gold", granularity="effect",
                        has_reversal_class=True, effects_complete=True) for r in raws]
        np_ = GOLD_DIR / "gold_negatives.csv"
        if np_.exists():
            negatives = [r for r in read_csv(np_) if norm_label(r.get("label")) in ("negative", "no")]
        info["files"] = {str(p.relative_to(BENCH_DIR)): sha256_file(p),
                         str(np_.relative_to(BENCH_DIR)): sha256_file(np_)}
        man = GOLD_DIR / "manifest.json"
        if man.exists():
            info["manifest_sha256"] = sha256_file(man)
            manifest = json.loads(man.read_text())
            for rel, h in manifest.get("files", {}).items():
                actual = sha256_file(BENCH_DIR / rel)
                if actual != h and not allow_dirty:
                    sys.exit(f"ERROR: {rel} does not match gold manifest hash — rebuild the gold set "
                             f"or pass --allow-dirty-gt (marks the run dirty).")
                if actual != h:
                    info["gt_dirty"] = True
    elif spec.startswith("silver:"):
        name = spec.split(":", 1)[1]
        if name not in SILVER_SPECS:
            sys.exit(f"ERROR: unknown silver set {name!r}; known: {sorted(SILVER_SPECS)}")
        s = SILVER_SPECS[name]
        p = SILVER_DIR / s["file"]
        info["files"] = {str(p.relative_to(BENCH_DIR)): sha256_file(p)}
        corr = SILVER_DIR / f"{name}_corrections.csv"
        raws, n_corr = apply_gt_corrections(read_csv(p), corr)
        if n_corr:
            info["files"][str(corr.relative_to(BENCH_DIR))] = sha256_file(corr)
            info["corrections_applied"] = n_corr
        rows = [_gt_row(r, source=f"external:{name}", granularity=s["granularity"],
                        has_reversal_class=s["has_reversal_class"],
                        effects_complete=s.get("effects_complete", False)) for r in raws]
    else:
        p = Path(gt_path or spec)
        raws = read_csv(p)
        rows = [_gt_row(r, source=r.get("source") or p.stem, granularity="effect",
                        has_reversal_class=False) for r in raws]
        info["files"] = {str(p): sha256_file(p)}
    if not rows and not negatives:
        sys.exit("ERROR: ground truth is empty")
    for r in rows:
        if not r["provenance"]:
            sys.exit(f"ERROR: GT row {r['row_id']} has no `provenance` column/value; refusing to score.")
    # Paper-level ground truth cannot score an effect-level extractor. FLoRa records
    # one verdict per paper (518 of its 524 papers have exactly one row) and no count
    # of how many replications a paper contains, so where a paper reports several it
    # cannot say whether the row the matcher picked is the one that verdict describes.
    # Measured on base_87_r1: 83.3% accuracy where the extractor found one effect,
    # 50.0% at two, 60.0% at three or more. Restricting to single-effect papers was
    # considered and rejected -- the only available filter is the extractor's own
    # output, which would select the benchmark set using the system under test.
    # FLoRa keeps two honest jobs: the discovery-recall answer key (recall_harness.py,
    # where "did search find this paper" is paper-level by nature) and a pool of
    # diverse papers for gold coding.
    paper_level = [r for r in rows if r.get("granularity") == "paper"]
    if paper_level and not allow_paper_level:
        sys.exit(f"ERROR: {spec} is paper-level ground truth ({len(paper_level)} rows): one verdict per "
                 f"paper, no per-effect labels, so it cannot score which effect the extractor got right. "
                 f"Retired as an extraction-scoring source 2026-09-04 (see benchmarking/README.md). "
                 f"Use --gt gold or --gt silver:fred_v242. (--allow-paper-level-gt to override for "
                 f"forensic re-scoring; the report will say the number is not effect-level.)")
    if paper_level:
        info["paper_level_gt"] = len(paper_level)
    dirty = [r for r in rows if r["provenance"].startswith("pipeline:")]
    if dirty and not allow_dirty:
        sys.exit(f"ERROR: {len(dirty)} GT rows carry pipeline provenance (e.g. {dirty[0]['provenance']}); "
                 f"they were written by the system under test. Refusing. (--allow-dirty-gt to override; "
                 f"the report will be marked contaminated.)")
    if dirty:
        info["gt_dirty"] = True
        info["n_pipeline_rows"] = len(dirty)
    if split != "all":
        rows = [r for r in rows if r["split"] in (split, "all")]
        negatives = [n for n in negatives if (n.get("split") or "all") in (split, "all")]
    return rows, negatives, info


# ── extraction loading ───────────────────────────────────────────────────────
def find_result_json(folder: str, tags: list[str], papers_dir: Path) -> Path | None:
    for tag in tags:
        tag_dir = papers_dir / folder / tag
        if not tag_dir.is_dir():
            continue
        for suffix in RESULT_SUFFIXES:
            c = tag_dir / f"{folder}{suffix}"
            if c.exists():
                return c
    return None


def _primary_model(model_usage, fallback: str) -> str:
    try:
        from mo_pipeline.discover.screening_backend import primary_model
        return primary_model(model_usage, fallback)
    except Exception:
        pass
    if isinstance(model_usage, str):
        try:
            import ast
            model_usage = ast.literal_eval(model_usage)
        except Exception:
            return fallback
    if not isinstance(model_usage, dict) or not model_usage:
        return fallback
    return max(model_usage.items(),
               key=lambda kv: (kv[1].get("outputTokens", 0) if isinstance(kv[1], dict) else 0))[0]


def _distinctive(text: str) -> set:
    """Content words of a title, long enough to be worth matching on."""
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 4}


def _document_identity(paper_dir: Path, data: dict, gt_title: str = "") -> str:
    """Why the document on disk is not the paper it is filed under, or "".

    Stage 6 has occasionally stored the wrong file under a DOI -- a citing PhD
    thesis, a publisher advertisement. Conversion faithfully converts whatever it
    is handed, extraction reads it and honestly reports no replications, and the
    paper is then charged to the extractor as a false negative. This is consulted
    ONLY for a negative extraction, so a real paper's positive result can never
    be overridden by it.
    """
    expected = ((data.get("replication_metadata") or {}).get("title") or gt_title or "").strip()
    body_path, abstract_path = paper_dir / "body.md", paper_dir / "abstract.md"
    body_head = ""
    if body_path.exists():
        try:
            body_head = body_path.read_text(errors="replace")[:5000]
        except OSError:
            pass
    heading = ""
    if abstract_path.exists():
        try:
            for line in abstract_path.read_text(errors="replace").splitlines():
                line = line.strip().lstrip("#").strip()
                if line and line != paper_dir.name and not line.startswith("<!--"):
                    heading = line
                    break
        except OSError:
            pass
    want = _distinctive(expected)
    if want:
        seen = _distinctive(heading) | _distinctive(body_head)
        if len(want & seen) < min(2, len(want)):
            return (f"expected {expected[:70]!r} but the text on disk starts "
                    f"{(heading or body_head[:70]).strip()[:70]!r}")
    pages = None
    pdfs = sorted(paper_dir.glob("*.pdf"))
    if pdfs:
        try:
            import fitz
            with fitz.open(str(pdfs[0])) as doc:
                pages = doc.page_count
        except Exception:
            pages = None
    body_size = body_path.stat().st_size if body_path.exists() else 0
    if pages is not None and pages <= 1 and body_size < 2000:
        return f"a {pages}-page PDF and a {body_size}-byte body.md: no article text on disk"
    return ""


def load_extraction(folder: str, tags: list[str], papers_dir: Path, gt_title: str = "") -> dict:
    paper_dir = papers_dir / folder
    if not paper_dir.is_dir():
        return {"status": "not_converted"}
    rj = find_result_json(folder, tags, papers_dir)
    if rj is None:
        return {"status": "not_extracted"}
    try:
        data = json.loads(rj.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "bad_json", "path": str(rj)}
    tag_dir = rj.parent
    model, tier, prov = "", "unknown", {}
    dl = tag_dir / "debug_log.json"
    if dl.exists():
        try:
            d = json.loads(dl.read_text())
            # The CLI's modelUsage lists an internal Haiku helper (~15 output tokens)
            # before the working model; extract.py recorded that first key as
            # `model` until 2026-09-02. Attribute the run to the model that did the
            # work (largest outputTokens), never to the recorded first key.
            model = _primary_model(d.get("modelUsage"), d.get("model") or "")
        except Exception:
            pass
    pv = tag_dir / "provenance.json"
    if pv.exists():
        try:
            prov = json.loads(pv.read_text())
            tier = prov.get("primary_tier") or tier
            model = model or prov.get("model_id") or ""   # debug_log's modelUsage wins (sidecars before 2026-09-02 may hold the helper id)
        except Exception:
            pass
    rows = data.get("replications") or []
    for r in rows:
        r.setdefault("citation_sentence", "")
    ai_version = next((str(r.get("ai_version")) for r in rows if r.get("ai_version") not in (None, "")), "")
    # Sidecar contract (extract.py / extract_core.py): prompt_version is the prompt
    # family ("8.7"), prompt_level says which rendering ran ("full" | "base" | "core").
    # Row-level ai_version is "<family>[-<level>]"; derive it when rows lack one.
    level = prov.get("prompt_level") or ""
    if not ai_version and prov.get("prompt_version"):
        ai_version = str(prov["prompt_version"]) + (f"-{level}" if level and level != "full" else "")
    contains = bool(data.get("contains_replications")) and bool(rows)
    if not contains:
        why = _document_identity(paper_dir, data, gt_title)
        if why:
            return {"status": "wrong_document_on_disk", "path": str(rj), "detail": why,
                    "tag": tag_dir.name, "model": model, "tier": tier,
                    "ai_version": ai_version, "prompt_level": level, "provenance": prov}
    return {"status": "ok", "path": str(rj), "tag": tag_dir.name, "data": data, "rows": rows,
            "contains": bool(data.get("contains_replications")) and bool(rows),
            "model": model, "tier": tier, "ai_version": ai_version, "prompt_level": level,
            "provenance": prov}


# ── scoring ──────────────────────────────────────────────────────────────────
def _type_ok(gt_t: str, ext_t: str) -> dict:
    ext_t = TYPE_COLLAPSE.get(ext_t, ext_t)
    if not gt_t:
        return {"type_scored": False}
    if gt_t == "direct_or_close":          # FReD collapse: only a 2-class judgment is possible
        return {"type_scored": True, "type_two_class": True,
                "type_ok": ext_t in ("direct", "close experiment", "close extension"),
                "type_adjacent_ok": None}
    ok = ext_t == gt_t
    adj = (ok or (gt_t in TYPE_ORDER and ext_t in TYPE_ORDER
                  and abs(TYPE_ORDER[gt_t] - TYPE_ORDER[ext_t]) == 1))
    two = (gt_t == "conceptual") == (ext_t == "conceptual")
    return {"type_scored": True, "type_two_class": False, "type_ok": ok,
            "type_adjacent_ok": adj, "type_two_ok": two}


def _score_stat(field: str, gt: dict, ext: dict) -> dict:
    """Both-present denominators; tolerance tiers; type-aware for effect sizes."""
    g, e = to_float(gt.get(field)), to_float(ext.get(field))
    out = {"gt_has": g is not None, "ext_has": e is not None, "both": g is not None and e is not None}
    if not out["both"]:
        return out
    if field.endswith("_es"):
        gt_type, ext_type = norm_label(gt.get(field + "_type")), norm_label(ext.get(field + "_type"))
        if gt_type and ext_type and gt_type != ext_type:
            out["type_mismatch"] = True
            return out
        d = abs(g - e)
        out.update({"tier1": d <= 0.01, "tier2": d <= 0.05, "tier3": d <= 0.10})
    elif field.endswith("_n"):
        d = abs(g - e)
        out.update({"tier1": d < 0.5, "tier2": d <= 0.05 * abs(g), "tier3": d <= 0.20 * abs(g)})
    else:  # p-value
        same_side = (g < 0.05) == (e < 0.05)
        gt_pt, ext_pt = norm_label(gt.get(field + "_type")), norm_label(ext.get(field + "_type"))
        out.update({"tier1": abs(g - e) <= 1e-4 and (not gt_pt or not ext_pt or gt_pt == ext_pt),
                    "tier2": abs(g - e) <= 1e-4, "tier3": same_side})
    return out


def _cit_ok(gt: dict, ext: dict) -> dict:
    cit = ext.get("citation_sentence") or ""
    if not cit.strip():
        return {"cit_present": False}
    names = matching.extract_last_names(gt.get("original_authors"))
    yr = str(gt.get("original_year") or "")
    author_found = any(n and n in cit.lower() for n in names)
    year_found = bool(yr) and yr in cit
    return {"cit_present": True, "cit_author_year_ok": author_found and year_found,
            "cit_overlap_ok": string_ratio(cit, gt.get("citation_sentence")) >= 0.6
            if gt.get("citation_sentence") else None}


def score_pair(gt: dict, ext: dict, m: matching.Match, paper: dict) -> dict:
    ext_res_raw = norm_label(ext.get("result"))
    collapse = (not gt["has_reversal_class"]) and ext_res_raw == "reversal"
    ext_res = "failure" if collapse else ext_res_raw
    rec = {
        "row_id": gt["row_id"], "replication_doi": gt["replication_doi"], "source": gt["source"],
        "split": gt["split"], "discipline_group": gt["discipline_group"], "year_bucket": gt["year_bucket"],
        "granularity": gt["granularity"], "gt_ambiguity": gt["gt_ambiguity"], "tier": paper.get("tier", "unknown"),
        "effects_complete": gt.get("effects_complete", False),
        "provenance": gt["provenance"],
        "model": paper.get("model", ""), "match_method": m.method, "match_relation": m.relation,
        "match_confidence": m.confidence, "match_cost": m.cost,
        "gt_result": gt["result"], "ext_result": ext_res, "ext_result_raw": ext_res_raw,
        "collapse_applied": collapse, "result_ok": bool(gt["result"]) and gt["result"] == ext_res,
        "gt_type": gt["replication_type"], "ext_type": TYPE_COLLAPSE.get(norm_label(ext.get("replication_type")),
                                                                        norm_label(ext.get("replication_type"))),
        "doi_gt": gt["original_doi"], "doi_ext": doi_of(ext, "original_url"),
        "gt_description": gt["description"][:200], "ext_description": (ext.get("description") or "")[:200],
        "ext_explanation": (ext.get("explanation") or "")[:400],
    }
    # Canonical forms: a preprint, a JSTOR alias or a Registered Report is the
    # same work as what it stands in for (matching.canonical_doi).
    rec["doi_ok"] = bool(rec["doi_gt"]) and (
        matching.canonical_doi(rec["doi_gt"]) == matching.canonical_doi(rec["doi_ext"]))
    rec["doi_alias_used"] = rec["doi_ok"] and rec["doi_gt"] != rec["doi_ext"]
    rec["doi_scored"] = bool(rec["doi_gt"])
    rec["doi_missing_ext"] = bool(rec["doi_gt"]) and not rec["doi_ext"]
    rec["title_ok"] = fuzzy_match(ext.get("original_title"), gt["original_title"], 0.75)
    rec["authors_ok"] = authors_match(ext.get("original_authors"), gt["original_authors"])
    rec["year_ok"] = year_match(ext.get("original_year"), gt["original_year"])
    rec["journal_ok"] = fuzzy_match(ext.get("original_journal"), gt["original_journal"], 0.75)
    rec["bib_scored"] = bool(gt["original_title"])
    rec.update(_type_ok(gt["replication_type"], rec["ext_type"]))
    rec.update(_cit_ok(gt, ext))
    rec["stats"] = {f: _score_stat(f, gt, ext) for f in NUMERIC_STATS}
    return rec


def paper_aggregate(labels: list[str]) -> str:
    """One paper-level verdict from several effect-level result labels.

    Paper-level ground truth (FLoRa) records the replication authors' verdict for
    the whole paper, while the pipeline emits one row per replicated effect. A
    paper whose effects went both ways is exactly what "inconclusive" describes,
    so: a single distinct label carries; success and failure together are
    inconclusive; success alongside inconclusive stays success only while the
    successes are not outnumbered.
    """
    labs = [l for l in labels if l]
    if not labs:
        return ""
    uniq = set(labs)
    if len(uniq) == 1:
        return labs[0]
    if {"success", "failure"} <= uniq or "reversal" in uniq:
        return "inconclusive"
    polar = [l for l in labs if l != "inconclusive"]
    return polar[0] if len(polar) >= len(labs) - len(polar) else "inconclusive"


def result_metrics(pairs: list[tuple[str, str]], classes=RESULT_VALUES) -> dict:
    n = len(pairs)
    if n == 0:
        return {"n": 0}
    acc = sum(a == b for a, b in pairs) / n
    per, f1s = {}, []
    for c in classes:
        tp = sum(1 for a, b in pairs if a == c and b == c)
        gt_n = sum(1 for a, _ in pairs if a == c)
        pr_n = sum(1 for _, b in pairs if b == c)
        p = tp / pr_n if pr_n else None
        r = tp / gt_n if gt_n else None
        f1 = (2 * p * r / (p + r)) if p and r and (p + r) else (0.0 if (gt_n or pr_n) else None)
        per[c] = {"support": gt_n, "predicted": pr_n, "tp": tp, "precision": p, "recall": r, "f1": f1}
        if gt_n:
            f1s.append(f1 or 0.0)
    cm = {a: {b: 0 for b in classes} for a in classes}
    for a, b in pairs:
        if a in cm and b in cm[a]:
            cm[a][b] += 1
    return {"n": n, "accuracy": acc, "cohen_kappa": cohen_kappa(pairs),
            "macro_f1": sum(f1s) / len(f1s) if f1s else None, "per_class": per, "confusion": cm}


def _rate(recs, key, cond=lambda r: True):
    xs = [bool(r[key]) for r in recs if cond(r) and r.get(key) is not None]
    return (sum(xs) / len(xs) if xs else None), len(xs)


def summarize(records: list[dict], funnel: Counter, entry: dict, paper_level: dict) -> dict:
    """Headline metrics from scored records. Pure function so it can be bootstrapped."""
    scored = [r for r in records if r["gt_result"]]
    # An extra extracted row is only a false positive if the GT covers every effect
    # in the paper. No external set does, so say so rather than printing a precision
    # that reads as over-extraction.
    partial = any(r.get("granularity") != "paper" and not r.get("effects_complete") for r in records)
    out: dict = {"n_matched_rows": len(records), "entry": {**entry, "precision_is_lower_bound": partial},
                 "paper_level": paper_level, "funnel": dict(funnel)}
    out["result_3class"] = result_metrics([(r["gt_result"], r["ext_result"]) for r in scored],
                                          ("success", "failure", "inconclusive"))
    # Paper-level GT (FLoRa) labels the whole paper, so also score the pipeline's
    # rows aggregated per paper. Reported beside the strict number, never instead.
    pap = [r for r in scored if r.get("ext_result_paper")]
    out["result_3class_paper"] = result_metrics([(r["gt_result"], r["ext_result_paper"]) for r in pap],
                                                ("success", "failure", "inconclusive")) if pap else {"n": 0}
    clear = [r for r in scored if (r.get("gt_ambiguity") or "") != "boundary"]
    out["result_3class_clear"] = result_metrics([(r["gt_result"], r["ext_result"]) for r in clear],
                                                ("success", "failure", "inconclusive")) if clear else {"n": 0}
    out["boundary_rows_excluded"] = len(scored) - len(clear)
    strict = [r for r in scored if not r.get("collapse_applied") and r["source"] and r.get("granularity") != "paper"]
    gold_like = [r for r in strict if r["source"].startswith("gold") or r["source"].startswith("human")]
    out["result_4class"] = result_metrics([(r["gt_result"], r["ext_result_raw"]) for r in (gold_like or [])])
    out["result_all_rows_raw"] = result_metrics([(r["gt_result"], r["ext_result_raw"]) for r in scored])
    out["collapse_applied_n"] = sum(1 for r in scored if r.get("collapse_applied"))
    t = [r for r in records if r.get("type_scored")]
    out["replication_type"] = {
        "n": len(t),
        "accuracy_4class": _rate([r for r in t if not r.get("type_two_class")], "type_ok")[0],
        "n_4class": sum(1 for r in t if not r.get("type_two_class")),
        "adjacent_accuracy": _rate([r for r in t if not r.get("type_two_class")], "type_adjacent_ok")[0],
        "kappa_4class": cohen_kappa([(r["gt_type"], r["ext_type"]) for r in t if not r.get("type_two_class")])
        if any(not r.get("type_two_class") for r in t) else None,
        "accuracy_2class_direct_or_close": _rate([r for r in t if r.get("type_two_class")], "type_ok")[0],
        "n_2class": sum(1 for r in t if r.get("type_two_class")),
    }
    d = [r for r in records if r.get("doi_scored")]
    out["original_doi"] = {"n": len(d), "accuracy": _rate(d, "doi_ok")[0],
                           "ext_missing_rate": _rate(d, "doi_missing_ext")[0]}
    b = [r for r in records if r.get("bib_scored")]
    out["bibliographic"] = {"n": len(b), **{k: _rate(b, k)[0] for k in ("title_ok", "authors_ok", "year_ok", "journal_ok")}}
    out["citation_sentence"] = {"n": len(records), "present_rate": _rate(records, "cit_present")[0],
                                "author_year_ok_rate": _rate([r for r in records if r.get("cit_present")], "cit_author_year_ok")[0]}
    st = {}
    for f in NUMERIC_STATS:
        xs = [r["stats"][f] for r in records]
        both = [x for x in xs if x.get("both")]
        st[f] = {"gt_has": sum(1 for x in xs if x["gt_has"]), "ext_has": sum(1 for x in xs if x["ext_has"]),
                 "both_present": len(both),
                 "gt_has_ext_missing": sum(1 for x in xs if x["gt_has"] and not x["ext_has"]),
                 "ext_has_gt_missing": sum(1 for x in xs if x["ext_has"] and not x["gt_has"]),
                 "type_mismatch_unscored": sum(1 for x in both if x.get("type_mismatch")),
                 "tier1": _rate([x for x in both if not x.get("type_mismatch")], "tier1")[0],
                 "tier2": _rate([x for x in both if not x.get("type_mismatch")], "tier2")[0],
                 "tier3": _rate([x for x in both if not x.get("type_mismatch")], "tier3")[0]}
    out["statistics"] = st
    return out


def cluster_bootstrap(records: list[dict], funnel, entry, paper_level, n_boot: int = 2000,
                      seed: int = 0) -> dict:
    """Paper-level resampling of the matched records for headline metrics (percentile 95% CI)."""
    by_paper = defaultdict(list)
    for r in records:
        by_paper[r["replication_doi"]].append(r)
    papers = sorted(by_paper)
    if len(papers) < 2:
        return {}
    rng = random.Random(seed)
    keys = {
        "result_3class.accuracy": lambda s: s["result_3class"].get("accuracy"),
        "result_3class.cohen_kappa": lambda s: s["result_3class"].get("cohen_kappa"),
        "result_3class.macro_f1": lambda s: s["result_3class"].get("macro_f1"),
        "result_3class.inconclusive_recall": lambda s: (s["result_3class"].get("per_class", {}).get("inconclusive") or {}).get("recall"),
        "result_4class.accuracy": lambda s: s["result_4class"].get("accuracy"),
        "result_4class.reversal_recall": lambda s: (s["result_4class"].get("per_class", {}).get("reversal") or {}).get("recall"),
        "replication_type.accuracy_4class": lambda s: s["replication_type"].get("accuracy_4class"),
        "original_doi.accuracy": lambda s: s["original_doi"].get("accuracy"),
    }
    samples = {k: [] for k in keys}
    for _ in range(n_boot):
        draw = [r for p in rng.choices(papers, k=len(papers)) for r in by_paper[p]]
        s = summarize(draw, Counter(), {}, {})
        for k, fn in keys.items():
            v = fn(s)
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                samples[k].append(v)
    out = {}
    for k, xs in samples.items():
        if len(xs) >= 100:
            xs.sort()
            out[k] = {"lo": xs[int(0.025 * len(xs))], "hi": xs[int(0.975 * len(xs)) - 1], "n_boot": len(xs)}
    return out


# ── provenance block ─────────────────────────────────────────────────────────
def provenance_block(papers: dict, gt_info: dict, judge: MatchJudge | None, args) -> dict:
    prompt_files = {"prompt_shared_core.md": config.PROMPT_SHARED_CORE, **{p.name: p for p in config.PROMPT_FILES.values()}}
    try:
        cli_version = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception:
        cli_version = ""
    models = Counter(p.get("model") or "unknown" for p in papers.values() if p.get("status") == "ok")
    tiers = Counter(p.get("tier") or "unknown" for p in papers.values() if p.get("status") == "ok")
    versions = Counter(p.get("ai_version") or "unknown" for p in papers.values() if p.get("status") == "ok")
    levels = Counter(p.get("prompt_level") or "unknown" for p in papers.values() if p.get("status") == "ok")
    try:
        from mo_pipeline import version as mo_version
        pipeline = mo_version.manifest()
    except Exception:
        pipeline = {}
    return {
        "harness_version": HARNESS_VERSION,
        "pipeline_version": pipeline.get("pipeline_version", ""),
        "code_fingerprint": pipeline.get("code_fingerprint", ""),
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gt": gt_info, "split": args.split, "run": args.run,
        # A list, not the raw comma string: ",".join() over a string spells it letter by letter.
        "tags": [t for t in (args.tags or "").split(",") if t],
        "papers_dir": str(args.papers_dir),
        "model_ids": dict(models), "ai_versions_in_results": dict(versions), "prompt_levels": dict(levels),
        "tier_counts": dict(tiers),
        "prompt_version_now": config.EXTRACTOR_VERSION_FILE.read_text().strip() if config.EXTRACTOR_VERSION_FILE.exists() else "",
        "prompt_sha256": {k: sha256_file(v) for k, v in prompt_files.items()},
        "codebook_sha256": sha256_file(CODEBOOK),
        "git_commit": git("rev-parse", "HEAD"), "git_dirty_prompts": bool(git("status", "--porcelain", "prompts/")),
        "git_dirty_pipeline": bool(git("status", "--porcelain", "mo_pipeline/")),
        "claude_cli_version": cli_version,
        "matcher": ({"mode": "llm", "provider": judge.provider, "model": judge.model, "prompt_version": judge.version,
                     "calls": judge.calls, "cache_hits": judge.cache_hits, "failures": judge.failures,
                     "offline": judge.offline} if judge else {"mode": "deterministic"}),
        "bootstrap": {"n": args.bootstrap, "seed": 0},
    }


# ── report ───────────────────────────────────────────────────────────────────
def _pct(v, nd=1):
    return "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{100*v:.{nd}f}%"


def _ci(cis, key):
    c = cis.get(key)
    return f" [{_pct(c['lo'])}, {_pct(c['hi'])}]" if c else ""


def render_report(m: dict, cis: dict, prov: dict, breakdowns: dict, matcher_stats: dict) -> str:
    L = [f"# Extraction benchmark — {prov['run'] or prov['gt']['spec']} / tags {','.join(prov['tags'])} / split {prov['split']}", ""]
    L += ["## Provenance", "",
          f"- models: {prov['model_ids']}  | ai_version in results: {prov['ai_versions_in_results']}",
          f"- prompt version now: {prov['prompt_version_now']} (prompts dirty in git: {prov['git_dirty_prompts']}); git {prov['git_commit'][:10]}",
          f"- claude CLI: {prov['claude_cli_version'] or 'n/a'} | harness {prov['harness_version']} | {prov['timestamp_utc']}",
          f"- ground truth: {prov['gt']['spec']} files {list(prov['gt']['files'])}"
          + (f"; {prov['gt']['corrections_applied']} corrections applied" if prov['gt'].get('corrections_applied') else "")
          + ("  **CONTAMINATED (pipeline-authored rows allowed by --allow-dirty-gt)**" if prov['gt'].get('gt_dirty') else "")
          + (f"  **PAPER-LEVEL GT ({prov['gt']['paper_level_gt']} rows): one verdict per paper, so the "
             f"per-row numbers below are not effect-level accuracy**" if prov['gt'].get('paper_level_gt') else ""),
          f"- matcher: {prov['matcher']}", f"- input tiers: {prov['tier_counts']}", ""]
    f = m["funnel"]
    L += ["## Coverage funnel", "",
          f"papers in GT: {f.get('papers_total',0)}; not downloaded/converted: {f.get('not_converted',0)}; "
          f"not extracted under these tags: {f.get('not_extracted',0)}; bad json: {f.get('bad_json',0)}; "
          f"pipeline said no replications: {f.get('no_replications',0)}; "
          f"wrong document on disk: {f.get('wrong_document_on_disk',0)}; scored: {f.get('scored',0)}", ""]
    e = m["entry"]
    L += ["## Entry-level matching (effect-level GT only)", "",
          f"TP {e.get('tp',0)}  FN {e.get('fn',0)}  FP {e.get('fp',0)}  → precision {_pct(e.get('precision'))}, "
          f"recall {_pct(e.get('recall'))}, F1 {_pct(e.get('f1'))}",
          f"FN breakdown: {e.get('fn_different_original',0)} judged different_original, "
          f"{e.get('fn_no_candidate',0)} no candidate row, "
          f"{e.get('fn_no_replications',0)} paper reported no replications",
          f"granularity misses (same original, other effect): {e.get('granularity_misses',0)}; "
          f"wrong-original errors flagged by judge: {e.get('wrong_original',0)}; "
          f"paper-level GT extra rows (unpenalized): {e.get('extra_rows_paper_level',0)}", ""]
    if e.get("precision_is_lower_bound"):
        same, other = e.get("fp_same_original", 0), e.get("fp_other_original", 0)
        L += ["Precision here is a **lower bound**: this ground truth codes the replication each "
              "paper is about, not every sub-analysis of it, so an extra extracted row may be a real "
              "effect nobody coded. Recall and the FN breakdown are unaffected.",
              f"Of the {e.get('fp',0)} penalized extras, {same} cite an original a matched row already "
              f"names (per-effect splits of a recorded replication, which the prompt asks for) and "
              f"{other} name an original the GT does not record at all — only the second group can "
              f"contain a wrong-original error. See `same_original_as_a_matched_row` in extra_rows.csv.", ""]
    pl = m["paper_level"]
    if pl:
        L += ["## Paper-level (contains_replications)", "",
              f"positives: TP {pl.get('tp',0)} FN {pl.get('fn',0)} | negatives: TN {pl.get('tn',0)} FP {pl.get('fp',0)} "
              f"(not evaluated: {pl.get('neg_not_evaluated',0)}) → precision {_pct(pl.get('precision'))}, "
              f"negative-set FPR {_pct(pl.get('fpr'))}", ""]
    r3 = m["result_3class"]
    L += ["## Result classification (3-value; reversal→failure where the GT source lacks the class)", ""]
    if r3.get("n"):
        L += [f"n={r3['n']}  accuracy {_pct(r3['accuracy'])}{_ci(cis,'result_3class.accuracy')}  "
              f"κ {r3['cohen_kappa']:.3f}{_ci(cis,'result_3class.cohen_kappa') if False else ''}  macro-F1 {_pct(r3['macro_f1'])}  "
              f"(collapse applied on {m['collapse_applied_n']} rows)", "",
              "| class | support | predicted | recall | precision | F1 |", "|---|---|---|---|---|---|"]
        for c, d in r3["per_class"].items():
            L.append(f"| {c} | {d['support']} | {d['predicted']} | {_pct(d['recall'])} | {_pct(d['precision'])} | {_pct(d['f1'])} |")
        L += ["", "confusion (rows = GT, cols = pipeline):", "", "| | " + " | ".join(r3["confusion"]) + " |", "|---|" + "---|" * len(r3["confusion"])]
        for a, row in r3["confusion"].items():
            L.append(f"| **{a}** | " + " | ".join(str(row[b]) for b in r3["confusion"]) + " |")
        L.append("")
    rp = m.get("result_3class_paper") or {}
    if rp.get("n"):
        L += ["### Same rows, aggregated per original study", "",
              "Where the ground truth carries one verdict for a paper (FLoRa) or codes one effect of an "
              "original the pipeline split into several rows, this compares that verdict with the "
              "aggregate of every pipeline row about the same original, rather than the single row the "
              "assignment picked. The strict number above stays the headline.",
              f"n={rp['n']}  accuracy {_pct(rp['accuracy'])}  κ {rp['cohen_kappa']:.3f}  macro-F1 {_pct(rp['macro_f1'])}", ""]
    rc = m.get("result_3class_clear") or {}
    if rc.get("n") and m.get("boundary_rows_excluded"):
        L += [f"Clear slice ({m['boundary_rows_excluded']} rows flagged as a convention boundary in the GT "
              f"corrections excluded): n={rc['n']}  accuracy {_pct(rc['accuracy'])}  κ {rc['cohen_kappa']:.3f}", ""]
    r4 = m["result_4class"]
    if r4.get("n"):
        L += ["## Result classification (4-value, strict, gold/human rows only)", "",
              f"n={r4['n']}  accuracy {_pct(r4['accuracy'])}{_ci(cis,'result_4class.accuracy')}  κ {r4['cohen_kappa']:.3f}  macro-F1 {_pct(r4['macro_f1'])}", ""]
        rv = r4["per_class"].get("reversal", {})
        L += [f"reversal: support {rv.get('support',0)}, predicted {rv.get('predicted',0)}, recall {_pct(rv.get('recall'))}, precision {_pct(rv.get('precision'))}", ""]
    ra = m["result_all_rows_raw"]
    if ra.get("n"):
        rv = ra["per_class"].get("reversal", {})
        L += [f"Pipeline `reversal` across all scored rows (raw, before collapse): predicted {rv.get('predicted',0)}; "
              f"GT reversal support {rv.get('support',0)}.", ""]
    t = m["replication_type"]
    L += ["## Replication type", "",
          f"4-class n={t['n_4class']}: accuracy {_pct(t['accuracy_4class'])}{_ci(cis,'replication_type.accuracy_4class')}, "
          f"adjacent-or-exact {_pct(t['adjacent_accuracy'])}, κ {t['kappa_4class'] if t['kappa_4class'] is None else round(t['kappa_4class'],3)}; "
          f"2-class (FReD 'direct or close' vs conceptual) n={t['n_2class']}: {_pct(t['accuracy_2class_direct_or_close'])}", ""]
    if not t["n_4class"] and t["n_2class"]:
        L += ["Only the 2-class number exists here: this ground truth labels every row "
              "\"direct or close\", so it can say whether the pipeline called a replication conceptual "
              "but never distinguishes direct from close experiment from close extension. Not comparable "
              "with a 4-class figure from a gold-based arm.", ""]
    d, b, c = m["original_doi"], m["bibliographic"], m["citation_sentence"]
    L += ["## Original study identification", "",
          f"original DOI exact (n={d['n']}): {_pct(d['accuracy'])}{_ci(cis,'original_doi.accuracy')}; pipeline left DOI empty on {_pct(d['ext_missing_rate'])}",
          f"bibliographic (n={b['n']}): title {_pct(b['title_ok'])}, authors {_pct(b['authors_ok'])}, year {_pct(b['year_ok'])}, journal {_pct(b['journal_ok'])}",
          f"citation_sentence: present {_pct(c['present_rate'])}; author+year of GT original found in it {_pct(c['author_year_ok_rate'])}", ""]
    # A stat-free arm (--level base / extract_core) emits no statistics at all, so
    # the tier table would be a grid of n/a against however many values the GT
    # holds. That is the mode working as designed, and it is the one measurement
    # that really separates base from full -- so say it in words, with the GT
    # coverage it was measured against, instead of printing zeros a reader will
    # read as a regression.
    levels = set((prov.get("prompt_levels") or {}))
    stat_free = bool(levels) and levels <= {"base", "core"}
    ext_empty = all(s["ext_has"] == 0 for s in m["statistics"].values())
    gt_empty = all(s["gt_has"] == 0 for s in m["statistics"].values())
    if (stat_free and ext_empty) or gt_empty:
        gt_cov = ", ".join(f"{f_} {s['gt_has']}" for f_, s in m["statistics"].items() if s["gt_has"])
        ext_n = sum(s["ext_has"] for s in m["statistics"].values())
        if ext_empty and gt_empty:
            why = ("Not scored: neither side carries statistics. This arm ran stat-free"
                   f" ({'/'.join(sorted(levels)) or 'no level recorded'}) and the ground truth was coded "
                   "without them, so there is nothing to compare. Blank here means never measured, "
                   "not measured as zero.")
        elif ext_empty:
            why = (f"Not scored: this arm ran stat-free ({'/'.join(sorted(levels))}), which emits none of "
                   "the 14 statistical fields by construction, so there is nothing to compare and no tier "
                   "table is printed. This is the mode working as designed, not an extraction failure. "
                   f"For reference, the ground truth carries: {gt_cov}. Scoring those requires a full-mode arm.")
        else:
            why = ("Not scored: this ground truth was coded without statistics (a stat-free gold set), so "
                   f"the {ext_n} value(s) the extractor did emit have nothing to be checked against. "
                   "Score statistics against a set that codes them, such as silver:fred_v242.")
        L += ["## Statistics", "", why, ""]
    else:
        L += ["## Statistics (denominator = both present; tiers: N exact/±5%/±20%, ES |Δ|≤.01/.05/.10 same type, p ≤1e-4 same type / ≤1e-4 / same side of .05)", "",
              "| field | GT has | ext has | both | GT-only (miss) | ext-only | type mismatch | tier1 | tier2 | tier3 |", "|---|---|---|---|---|---|---|---|---|---|"]
        for f_, s in m["statistics"].items():
            L.append(f"| {f_} | {s['gt_has']} | {s['ext_has']} | {s['both_present']} | {s['gt_has_ext_missing']} | {s['ext_has_gt_missing']} | "
                     f"{s['type_mismatch_unscored']} | {_pct(s['tier1'])} | {_pct(s['tier2'])} | {_pct(s['tier3'])} |")
        L.append("")
    if matcher_stats:
        L += ["## Matcher", "", f"{matcher_stats}", ""]
    L += ["## Breakdowns (3-value result accuracy)", ""]
    for key, table in breakdowns.items():
        L += [f"**by {key}**", "", "| value | n rows | n papers | accuracy | DOI acc |", "|---|---|---|---|---|"]
        for v, s in table.items():
            L.append(f"| {v} | {s['n']} | {s['papers']} | {_pct(s['accuracy'])} | {_pct(s['doi_accuracy'])} |")
        L.append("")
    return "\n".join(L)


# ── evaluate ─────────────────────────────────────────────────────────────────
def _breakdowns(records: list[dict]) -> dict:
    out = {}
    for key in ("source", "provenance", "discipline_group", "gt_type", "tier", "year_bucket", "split", "gt_ambiguity", "match_method"):
        groups = defaultdict(list)
        for r in records:
            groups[str(r.get(key) or "")].append(r)
        table = {}
        for v, rs in sorted(groups.items()):
            sc = [r for r in rs if r["gt_result"]]
            table[v or "(blank)"] = {"n": len(rs), "papers": len({r["replication_doi"] for r in rs}),
                                     "accuracy": (sum(r["result_ok"] for r in sc) / len(sc)) if sc else None,
                                     "doi_accuracy": _rate(rs, "doi_ok", lambda r: r.get("doi_scored"))[0]}
        out[key] = table
    return out


def cmd_evaluate(args) -> None:
    tags = [t for t in args.tags.split(",") if t]
    papers_dir = Path(args.papers_dir) if args.papers_dir else config.PAPERS_DIR
    args.papers_dir = papers_dir
    rows, negatives, gt_info = load_ground_truth(args.gt, args.split, args.allow_dirty_gt,
                                                 allow_paper_level=args.allow_paper_level_gt)
    if args.run:
        run_dois = set(load_dois(args.run))
        rows = [r for r in rows if r["replication_doi"] in run_dois]
        negatives = [n for n in negatives if normalize_doi(n.get("replication_doi") or n.get("replication_url") or "") in run_dois]
    if args.split == "test":
        _release_gate(args, tags)

    judge = None
    log_path = None
    out_dir = Path(args.out_dir) if args.out_dir else RESULTS_DIR / f"{time.strftime('%Y-%m-%d')}_{'-'.join(tags)}_{args.gt.replace(':','-')}_{args.split}"
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.matcher == "llm":
        log_path = out_dir / "match_log.jsonl"
        if log_path.exists():
            log_path.unlink()
        judge = MatchJudge(provider=args.provider, model=args.match_model, log_path=log_path, offline=args.offline)

    by_paper: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_paper[r["replication_doi"]].append(r)
    funnel = Counter(papers_total=len(by_paper))
    papers: dict[str, dict] = {}
    records, unmatched, extra_rows, per_paper = [], [], [], []
    # Seeded so every count is present in metrics.json even at zero: a Counter
    # only materialises keys it increments, and a missing key reads as a stale
    # run rather than as "none of these occurred".
    entry = Counter({k: 0 for k in ("tp", "fn", "fp", "granularity_misses", "wrong_original",
                                    "extra_rows_paper_level", "fn_no_candidate",
                                    "fn_different_original", "fn_no_replications",
                                    "fp_same_original", "fp_other_original")})
    # The judge is the slow part (one CLI call per GT row the DOI did not
    # settle), so papers are loaded and matched concurrently; the tally below
    # then walks them in sorted order, so the outputs are deterministic.
    def _prepare(doi, gt_rows):
        folder = gt_rows[0]["paper_folder"] or doi_to_folder(doi)
        px = load_extraction(folder, tags, papers_dir,
                             gt_title=gt_rows[0].get("replication_title") or "")
        outcome = None
        if px["status"] == "ok" and px["contains"]:
            for g in gt_rows:
                g["source"] = g["source"] or args.gt
            outcome = assign(gt_rows, px["rows"], judge=judge, granularity=gt_rows[0]["granularity"],
                             allow_fuzzy=not args.no_fuzzy)
        return px, outcome
    prepared = _parallel_map(_prepare, sorted(by_paper.items()), args.judge_workers, "matching")
    for doi, gt_rows in sorted(by_paper.items()):
        px, outcome = prepared[doi]
        papers[doi] = px
        if px["status"] != "ok":
            funnel[px["status"]] += 1
            unmatched += [{"row_id": g["row_id"], "replication_doi": doi, "reason": px["status"],
                           "judge_reason": px.get("detail", ""),
                           "original_title": g["original_title"][:80], "gt_result": g["result"]} for g in gt_rows]
            per_paper.append({"replication_doi": doi, "status": px["status"], "n_gt": len(gt_rows),
                              "detail": px.get("detail", "")})
            continue
        if not px["contains"]:
            funnel["no_replications"] += 1
            entry["fn"] += len(gt_rows)
            entry["fn_no_replications"] += len(gt_rows)
            unmatched += [{"row_id": g["row_id"], "replication_doi": doi, "reason": "pipeline says no replications",
                           "original_title": g["original_title"][:80], "gt_result": g["result"]} for g in gt_rows]
            per_paper.append({"replication_doi": doi, "status": "no_replications", "n_gt": len(gt_rows), "n_ext": 0})
            continue
        funnel["scored"] += 1
        gran = gt_rows[0]["granularity"]
        for m in outcome.matches:
            g = gt_rows[m.gt_index]
            rec = score_pair(g, px["rows"][m.ext_index], m, px)
            # FLoRa labels the paper, not the effect: compare its verdict with the
            # aggregate of every pipeline row about the same original, instead of
            # the one row the assignment happened to pick. Effect-level GT that
            # codes only some of a paper's effects has the same mismatch, so it
            # gets the same treatment -- but only where the aggregate is
            # unambiguous: one GT row for this original, and extracted rows that
            # actually name it (no falling back to the whole paper, which would
            # mix in other originals).
            gt_doi = matching.canonical_doi(g["original_doi"])
            same = [r for r in px["rows"]
                    if gt_doi and matching.canonical_doi(doi_of(r, "original_url")) == gt_doi]
            sole = gt_doi and sum(1 for o in gt_rows
                                  if matching.canonical_doi(o["original_doi"]) == gt_doi) == 1
            pool = (same or px["rows"]) if gran == "paper" else (
                same if (same and sole and not g.get("effects_complete")) else [])
            if pool:
                rec["ext_result_paper"] = paper_aggregate([norm_label(r.get("result")) for r in pool])
                rec["result_paper_ok"] = bool(rec["gt_result"]) and rec["gt_result"] == rec["ext_result_paper"]
                rec["n_rows_aggregated"] = len(pool)
            records.append(rec)
        entry["tp"] += len(outcome.matches)
        entry["fn"] += len(outcome.gt_unmatched)
        entry["granularity_misses"] += len(outcome.granularity_misses)
        entry["wrong_original"] += len(outcome.wrong_original)
        if gran == "paper":
            entry["extra_rows_paper_level"] += len(outcome.ext_unmatched)
        else:
            entry["fp"] += len(outcome.ext_unmatched)
        for i in outcome.gt_unmatched:
            g = gt_rows[i]
            j = outcome.judged.get(i, {})
            entry["fn_different_original" if j.get("relation") == "different_original"
                  else "fn_no_candidate"] += 1
            unmatched.append({"row_id": g["row_id"], "replication_doi": doi, "reason": "no matching extracted row",
                              "judge_relation": j.get("relation", ""), "judge_reason": j.get("reason", ""),
                              "original_title": g["original_title"][:80], "gt_result": g["result"],
                              "n_ext_rows_in_paper": len(px["rows"])})
        orphaned = {j for _, j in outcome.wrong_original}
        # Not all extra rows are alike. One that cites an original a matched GT row
        # already names is a per-effect split of a replication the coder recorded
        # once -- the prompt asks for exactly that. One naming an original nowhere
        # in the GT is a different claim: possibly a replication the coder skipped,
        # possibly a wrong original. Counting them together hides which.
        matched_originals = {matching.canonical_doi(gt_rows[m.gt_index]["original_doi"])
                             for m in outcome.matches} - {""}
        for j in outcome.ext_unmatched:
            e = px["rows"][j]
            same_orig = matching.canonical_doi(doi_of(e, "original_url")) in matched_originals
            if gran != "paper":
                entry["fp_same_original" if same_orig else "fp_other_original"] += 1
            extra_rows.append({"replication_doi": doi, "penalized": gran != "paper", "ext_index": j,
                               "same_original_as_a_matched_row": same_orig,
                               "orphaned_by": "wrong_original" if j in orphaned else "",
                               "original_url": e.get("original_url", ""), "original_title": (e.get("original_title") or "")[:80],
                               "description": (e.get("description") or "")[:160], "result": e.get("result", "")})
        per_paper.append({"replication_doi": doi, "status": "scored", "n_gt": len(gt_rows), "n_ext": len(px["rows"]),
                          "n_matched": len(outcome.matches), "tier": px["tier"], "model": px["model"]})
    tp, fp, fn = entry["tp"], entry["fp"], entry["fn"]
    entry_d = dict(entry)
    entry_d["precision"] = tp / (tp + fp) if (tp + fp) else None
    entry_d["recall"] = tp / (tp + fn) if (tp + fn) else None
    entry_d["f1"] = (2 * entry_d["precision"] * entry_d["recall"] / (entry_d["precision"] + entry_d["recall"])
                     if entry_d["precision"] and entry_d["recall"] else None)

    # paper-level: positives = GT papers; negatives = gold_negatives
    pl = Counter()
    for doi, px in papers.items():
        if px["status"] == "ok":
            pl["tp" if px["contains"] else "fn"] += 1
    for n in negatives:
        doi = normalize_doi(n.get("replication_doi") or n.get("replication_url") or "") or ""
        folder = n.get("paper_folder") or (doi_to_folder(doi) if doi else "")
        px = load_extraction(folder, tags, papers_dir) if folder else {"status": "not_converted"}
        if px["status"] != "ok":
            pl["neg_not_evaluated"] += 1
        else:
            pl["fp" if px["contains"] else "tn"] += 1
    pl_d = dict(pl)
    if pl:
        pl_d["precision"] = pl["tp"] / (pl["tp"] + pl["fp"]) if (pl["tp"] + pl["fp"]) else None
        pl_d["fpr"] = pl["fp"] / (pl["fp"] + pl["tn"]) if (pl["fp"] + pl["tn"]) else None

    m = summarize(records, funnel, entry_d, pl_d)
    cis = cluster_bootstrap(records, funnel, entry_d, pl_d, n_boot=args.bootstrap) if args.bootstrap else {}
    prov = provenance_block(papers, gt_info, judge, args)
    if args.split == "test":
        fam = prov["prompt_version_now"]
        bad = {v for v in prov["ai_versions_in_results"] if not str(v).startswith(fam)}
        if bad:
            sys.exit(f"ERROR: results carry ai_version {sorted(bad)} but prompts/version.txt is {fam}; "
                     f"a release evaluation must score extractions made with the released prompt.")
        if len(prov["model_ids"]) != 1:
            sys.exit(f"ERROR: results mix model ids {prov['model_ids']}; a release evaluation needs a single model.")
    breakdowns = _breakdowns(records)
    matcher_stats = {"methods": dict(Counter(r["match_method"] for r in records)),
                     "relations": dict(Counter(r["match_relation"] for r in records)),
                     "judge_calls": judge.calls if judge else 0, "cache_hits": judge.cache_hits if judge else 0,
                     "judge_failures": judge.failures if judge else 0}
    metrics = {"provenance": prov, "metrics": m, "ci95": cis, "breakdowns": breakdowns, "matcher": matcher_stats}
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    report = render_report(m, cis, prov, breakdowns, matcher_stats)
    (out_dir / "report.md").write_text(report)
    flat = [{k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in r.items()} for r in records]
    if flat:
        write_csv(out_dir / "matched_rows.csv", flat)
        write_csv(out_dir / "disagreements.csv", [r for r in flat if not r["result_ok"]] or [flat[0]][:0], list(flat[0].keys()))
    if unmatched:
        write_csv(out_dir / "unmatched.csv", unmatched, sorted({k for u in unmatched for k in u}))
    if extra_rows:
        write_csv(out_dir / "extra_rows.csv", extra_rows)
    write_csv(out_dir / "per_paper.csv", per_paper, sorted({k for p in per_paper for k in p}))
    try:
        _confusion_png(records, out_dir / "confusion_result.png", f"{'/'.join(tags)} result")
    except Exception as exc:  # matplotlib optional
        print(f"(confusion png skipped: {exc})")
    if args.split == "test":
        with open(TEST_LEDGER, "a") as f:
            f.write(json.dumps({"ts": prov["timestamp_utc"], "tags": tags, "gt": args.gt, "run": args.run,
                                "prompt_version": prov["prompt_version_now"], "git": prov["git_commit"][:10],
                                "models": prov["model_ids"], "result_3class_accuracy": m["result_3class"].get("accuracy"),
                                "result_3class_kappa": m["result_3class"].get("cohen_kappa"),
                                "entry_precision": entry_d["precision"], "entry_recall": entry_d["recall"],
                                "out_dir": str(out_dir)}) + "\n")
        n_prev = sum(1 for ln in TEST_LEDGER.read_text().splitlines() if json.loads(ln).get("prompt_version") == prov["prompt_version_now"])
        print(f"TEST LEDGER: prompt version {prov['prompt_version_now']} has now been evaluated on the test split {n_prev} time(s).")
    print(report)
    print(f"\nOutputs -> {out_dir}/")


def _confusion_png(records, path, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    classes = list(RESULT_VALUES)
    cm = [[sum(1 for r in records if r["gt_result"] == a and r["ext_result_raw"] == b) for b in classes] for a in classes]
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.imshow(cm, cmap="Blues")
    ax.set_xticks(range(4), classes); ax.set_yticks(range(4), classes)
    for i in range(4):
        for j in range(4):
            ax.text(j, i, str(cm[i][j]), ha="center", va="center", fontsize=14, fontweight="bold" if i == j else "normal")
    ax.set_xlabel("pipeline"); ax.set_ylabel("ground truth"); ax.set_title(title)
    fig.savefig(path, bbox_inches="tight", dpi=130); plt.close(fig)


def _release_gate(args, tags) -> None:
    if not args.release:
        sys.exit("ERROR: --split test requires --release (the test split is evaluated once per released prompt version).")
    if git("status", "--porcelain", "prompts/"):
        sys.exit("ERROR: prompts/ has uncommitted changes; commit the prompt before a release evaluation.")
    if args.allow_dirty_gt:
        sys.exit("ERROR: --allow-dirty-gt is not permitted on the test split.")


# ── retest (run-to-run agreement) ────────────────────────────────────────────
def cmd_retest(args) -> None:
    papers_dir = Path(args.papers_dir) if args.papers_dir else config.PAPERS_DIR
    dois = load_dois(args.run) if args.run else None
    folders = [doi_to_folder(d) for d in dois] if dois else sorted(p.name for p in papers_dir.iterdir() if p.is_dir())
    judge = MatchJudge(provider=args.provider, model=args.match_model, offline=args.offline) if args.matcher == "llm" else None
    pairs_res, pairs_type, rowcount, both, only_a, only_b, doi_ok = [], [], [], 0, 0, 0, []

    def _prepare(folder, _):
        a = load_extraction(folder, [args.tag_a], papers_dir)
        b = load_extraction(folder, [args.tag_b], papers_dir)
        if a["status"] != "ok" or b["status"] != "ok" or not a["rows"] or not b["rows"]:
            return a, b, None
        ga = [{"row_id": f"a{i}", "replication_doi": folder, "original_url": r.get("original_url", ""),
               "original_title": r.get("original_title", ""), "original_authors": r.get("original_authors", ""),
               "original_year": r.get("original_year", ""), "original_journal": r.get("original_journal", ""),
               "description": r.get("description", ""), "source": "retest"} for i, r in enumerate(a["rows"])]
        return a, b, assign(ga, b["rows"], judge=judge)
    prepared = _parallel_map(_prepare, [(f, None) for f in folders], args.judge_workers, "matching")
    for folder in folders:
        a, b, o = prepared[folder]
        if a["status"] != "ok" or b["status"] != "ok":
            continue
        both += 1
        rowcount.append((len(a["rows"]), len(b["rows"])))
        if o is None:
            continue
        for m in o.matches:
            ra, rb = a["rows"][m.gt_index], b["rows"][m.ext_index]
            pairs_res.append((norm_label(ra.get("result")), norm_label(rb.get("result"))))
            pairs_type.append((norm_label(ra.get("replication_type")), norm_label(rb.get("replication_type"))))
            doi_ok.append(doi_of(ra, "original_url") == doi_of(rb, "original_url"))
        only_a += len(o.gt_unmatched); only_b += len(o.ext_unmatched)
    n = len(pairs_res)
    same_count = sum(1 for x, y in rowcount if x == y)
    out = {"tag_a": args.tag_a, "tag_b": args.tag_b, "papers_in_both": both,
           "same_row_count_rate": same_count / len(rowcount) if rowcount else None,
           "rows_matched": n, "rows_only_a": only_a, "rows_only_b": only_b,
           "result_agreement": sum(x == y for x, y in pairs_res) / n if n else None,
           "result_kappa": cohen_kappa(pairs_res) if n else None,
           "type_agreement": sum(x == y for x, y in pairs_type) / n if n else None,
           "original_doi_agreement": sum(doi_ok) / n if n else None,
           "result_disagreements": dict(Counter(f"{x}->{y}" for x, y in pairs_res if x != y))}
    print(json.dumps(out, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2))


# ── status / run ─────────────────────────────────────────────────────────────
def cmd_status(args) -> None:
    st = run_status(args.run)
    tags = [t for t in (args.tags or args.run).split(",") if t]
    papers_dir = Path(args.papers_dir) if args.papers_dir else config.PAPERS_DIR
    extracted, remaining = [], []
    for doi in load_dois(args.run):
        folder = doi_to_folder(doi)
        if find_result_json(folder, tags, papers_dir) is not None:
            extracted.append(folder)
        elif (papers_dir / folder).is_dir():
            remaining.append(folder)
    print(f"run {args.run}: {st['n_dois']} DOIs | in corpus {st['in_corpus']} | converted {st['converted']} | "
          f"inbox pending {st['inbox_pending']} | missing {st['missing']}")
    print(f"  extracted under {tags}: {len(extracted)} | extractable remaining: {len(remaining)}")
    rp = config.DOI_RUNS_DIR / args.run / "include_list_remaining.txt"
    rp.write_text("\n".join(remaining) + ("\n" if remaining else ""))
    print(f"  -> {rp}")


def _progress(iterable, total: int | None = None, desc: str = ""):
    """tqdm when installed, a plain iterator otherwise (stderr, so stdout stays parseable)."""
    try:
        from tqdm import tqdm
        return tqdm(iterable, total=total, desc=desc, unit="paper", file=sys.stderr, dynamic_ncols=True)
    except ImportError:
        return iterable


def _parallel_map(fn, items, workers: int, desc: str) -> dict:
    """{key: fn(key, value)} over (key, value) items, `workers` at a time, with a
    progress bar. Completion order is irrelevant: callers iterate their own
    sorted keys afterwards, so results stay deterministic."""
    out = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(fn, k, v): k for k, v in items}
        for fut in _progress(as_completed(futs), total=len(futs), desc=desc):
            out[futs[fut]] = fut.result()
    return out


USAGE_URL = "https://api.anthropic.com/api/oauth/usage"


def claude_usage() -> dict | None:
    """Plan usage as Claude Code's /usage screen shows it: the 5-hour session
    window and the weekly window in percent, plus any model-scoped weekly limit.
    Reads the OAuth token from ~/.claude/.credentials.json (the CLI has no
    non-interactive usage command). None when no token; {"error": ...} on a
    request failure. The pool is shared by every Claude Code session on the
    account, so a before/after delta is an upper bound on one run's share.
    """
    try:
        cred = json.loads((Path.home() / ".claude" / ".credentials.json").read_text())
        tok = (cred.get("claudeAiOauth") or {}).get("accessToken")
    except Exception:
        return None
    if not tok:
        return None
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {tok}", "anthropic-beta": "oauth-2025-04-20",
        "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.loads(r.read())
    except Exception as e:
        return {"error": str(e)[:120]}
    out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    for key, label in (("five_hour", "session_5h"), ("seven_day", "weekly")):
        blk = body.get(key) or {}
        out[label] = blk.get("utilization")
        out[label + "_resets_at"] = blk.get("resets_at")
    for lim in body.get("limits") or []:
        if lim.get("kind") == "weekly_scoped":
            name = (((lim.get("scope") or {}).get("model") or {}).get("display_name") or "scoped").lower()
            out[f"weekly_{name}"] = lim.get("percent")
    return out


def usage_line(label: str, u: dict | None) -> str:
    if not u:
        return f"{label}: unavailable (no Claude Code OAuth token)"
    if "error" in u:
        return f"{label}: unavailable ({u['error']})"
    parts = [f"session 5h {u.get('session_5h')}% (resets {u.get('session_5h_resets_at')})",
             f"weekly {u.get('weekly')}% (resets {u.get('weekly_resets_at')})"]
    parts += [f"weekly[{k[len('weekly_'):]}] {v}%" for k, v in u.items()
              if k.startswith("weekly_") and not k.endswith("_resets_at")]
    return f"{label} @ {u.get('ts')}: " + "; ".join(parts)


def usage_delta(before: dict | None, after: dict | None) -> str:
    ok = lambda u, k: isinstance((u or {}).get(k), (int, float))
    if not (ok(before, "session_5h") and ok(after, "session_5h")):
        return "claude usage delta: unavailable"
    # resets_at drifts by a fraction of a second between calls (and can cross a
    # minute boundary doing so); a new window moves it by hours.
    def _ts(u):
        from datetime import datetime
        try:
            return datetime.fromisoformat(str(u.get("session_5h_resets_at")))
        except (TypeError, ValueError):
            return None
    tb, ta = _ts(before), _ts(after)
    reset = " (5h window reset during the run)" if tb and ta and abs((ta - tb).total_seconds()) > 60 else ""
    return (f"claude usage delta: session 5h {after['session_5h'] - before['session_5h']:+.0f} pp{reset}; "
            f"weekly {after.get('weekly', 0) - before.get('weekly', 0):+.0f} pp "
            f"(account-wide pool: an upper bound on this run's share)")


def cmd_run(args) -> None:
    papers_dir = Path(args.papers_dir) if args.papers_dir else config.PAPERS_DIR
    include = config.DOI_RUNS_DIR / args.run / ("include_list_remaining.txt" if args.remaining else "include_list.txt")
    if not include.exists():
        sys.exit(f"ERROR: {include} not found (create the run first: harness.py sample, or doi_runs.create_run)")
    cmd = [sys.executable, "-m", "mo_pipeline.extract.extract", str(papers_dir), "--batch", "--level", args.level,
           "--dontcheck", "--model", args.model, "--workers", str(args.workers), "--tag", args.tag,
           "--include-list", str(include)]
    if args.limit:
        cmd += ["--limit", str(args.limit)]
    if args.force_tier:
        cmd += ["--force-tier", args.force_tier]
    dirty = git("status", "--porcelain", "prompts/")
    if dirty:
        print("WARNING: prompts/ has uncommitted changes — this run's ai_version will not correspond to a commit.")
    print("argv:", " ".join(cmd))
    before = claude_usage()
    print(usage_line("claude usage before run", before))
    if args.dry_run:
        return
    log = RESULTS_DIR / f"run_{args.tag}_{args.run}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w") as lf:
        lf.write(usage_line("claude usage before run", before) + "\n")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=config.REPO_ROOT)
        skipped_db = 0
        for line in proc.stdout:
            lf.write(line); sys.stdout.write(line)
            if "Already in dataset" in line:
                skipped_db += 1
        proc.wait()
        after = claude_usage()
        for line in (usage_line("claude usage after run", after), usage_delta(before, after)):
            lf.write(line + "\n"); print(line)
    print(f"\nextract exit {proc.returncode}; log -> {log}")
    if skipped_db:
        sys.exit(f"ERROR: {skipped_db} papers were skipped as 'Already in dataset' despite --dontcheck — investigate before scoring.")


# ── silver importers ─────────────────────────────────────────────────────────
def _load_xlsx(xlsx: Path) -> tuple[list[str], list[dict]]:
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    header = [str(h) for h in next(it)]
    rows = []
    for r in it:
        if any(v is not None and str(v).strip() for v in r):
            rows.append({h: ("" if v is None else str(v).strip()) for h, v in zip(header, r)})
    return header, rows


def _import_xlsx(xlsx: Path, out: Path, prefix: str, provenance: str) -> None:
    header, raw = _load_xlsx(xlsx)
    kept, skipped, seen = [], [], set()
    for i, row in enumerate(raw, start=2):
        rep = normalize_doi(row.get("replication_url", ""))
        orig = normalize_doi(row.get("original_url", ""))
        if rep is None:
            skipped.append({**row, "skip_reason": "replication_url is not a DOI", "xlsx_line": i}); continue
        pair = (orig or row.get("original_url", "").lower(), rep, norm_label(row.get("description")))
        if pair in seen:
            skipped.append({**row, "skip_reason": "duplicate (original, replication, description)", "xlsx_line": i}); continue
        seen.add(pair)
        kept.append({**row, "row_id": f"{prefix}_{i}", "replication_doi_norm": rep, "original_doi_norm": orig or "",
                     "provenance": provenance})
    write_csv(out, kept, header + ["row_id", "replication_doi_norm", "original_doi_norm", "provenance"])
    if skipped:
        write_csv(out.with_name(out.stem + "_skipped.csv"), skipped, header + ["skip_reason", "xlsx_line"])
    print(f"{xlsx.name}: kept {len(kept)}, skipped {len(skipped)} -> {out}")


def cmd_import_flora(args) -> None:
    _import_xlsx(Path(args.xlsx), SILVER_DIR / "flora.csv", "flora", "external:flora")


def cmd_import_fred(args) -> None:
    _import_xlsx(Path(args.xlsx), SILVER_DIR / "fred_v2_4_2.csv", "fred", "external:fred_v242")


# ── sampling frame (Phase 2b) ────────────────────────────────────────────────
def _catalog() -> dict[str, dict]:
    con = sqlite3.connect(config.CATALOG_PATH)
    try:
        cols = [r[1] for r in con.execute("pragma table_info(papers)")]
        return {r[cols.index("doi")].lower(): dict(zip(cols, r)) for r in con.execute("select * from papers")}
    finally:
        con.close()


def _latest_db_rows() -> list[dict]:
    from mo_pipeline.label_centrality.common import latest_csv_path
    rows = read_csv(latest_csv_path())
    for r in rows:
        r["_rep"] = normalize_doi(r.get("replication_url", "")) or ""
    return rows


def _paper_mode(rows: list[dict], key: str) -> str:
    c = Counter(norm_label(r.get(key)) for r in rows if norm_label(r.get(key)))
    return c.most_common(1)[0][0] if c else ""


def cmd_sample(args) -> None:
    rng = random.Random(args.seed)
    cat = _catalog()
    def converted(doi: str) -> bool:
        c = cat.get(doi)
        return bool(c) and c.get("status") in ("converted", "screened", "extracted", "ingested")

    def on_disk(doi: str) -> bool:
        """Is the paper actually readable right now? The catalog is an index, not
        truth, so this asks the filesystem. The v1 frame sampled 45 FLoRa papers
        of which 38 never downloaded (that pull landed 24%), sending coders at
        folders that do not exist while 73 other FLoRa papers sat on the drive.
        """
        d = config.PAPERS_DIR / doi_to_folder(doi)
        return d.is_dir() and (any(d.glob("*_from_xml.md")) or any(d.glob("*_from_html.md"))
                               or (d / "body.md").exists() or any(d.glob("*.pdf"))
                               or any(d.glob("*.xml")))
    frame: list[dict] = []
    taken: set[str] = set()

    # Loaded before the first stratum, not just for the DB-derived ones: it is also
    # the discipline of last resort. The main_gt_human stratum reads its group from
    # `openalex_field` in the silver CSV, which is empty for 11 of its 27 papers,
    # while the production DB records a discipline for every row it has.
    try:
        db = _latest_db_rows()
    except Exception as exc:
        print(f"WARNING: production DB unavailable ({exc}); skipping DB-derived strata")
        db = []
    dbp = defaultdict(list)
    for r in db:
        if r["_rep"]:
            dbp[r["_rep"]].append(r)

    def _discipline(doi: str, expected: dict | None) -> str:
        # "unknown" counts as missing: discipline_group() returns that string, not
        # "", when its input is empty, so testing truthiness alone never falls back.
        g = (expected or {}).get("discipline_group", "")
        if g in ("", "unknown") and dbp.get(doi):
            g = discipline_group(_paper_mode(dbp[doi], "discipline")) or g
        return g

    def _fine_discipline(doi: str) -> tuple[str, str]:
        """The production DB's own `discipline` / `subdiscipline` for this paper.

        The five-way `discipline_group` is for stratifying results; the frame should
        also carry the vocabulary the database and the website actually use, so a
        breakdown can be read at the resolution the ontology defines. Negative papers
        are not in a replications database at all, so they come back blank here and
        are filled by `enrich_frame_disciplines`.
        """
        hits = dbp.get(doi)
        if not hits:
            return "", ""
        return _paper_mode(hits, "discipline"), _paper_mode(hits, "subdiscipline")

    def add(doi: str, source: str, stratum: str, expected: dict | None = None, note: str = ""):
        if not doi or doi in taken:
            return False
        taken.add(doi)
        frame.append({"replication_doi": doi, "paper_folder": doi_to_folder(doi), "source": source, "stratum": stratum,
                      "in_catalog": doi in cat, "converted": converted(doi),
                      "discipline_group": _discipline(doi, expected),
                      "discipline": _fine_discipline(doi)[0], "subdiscipline": _fine_discipline(doi)[1],
                      "expected_result": (expected or {}).get("result", ""), "expected_type": (expected or {}).get("replication_type", ""),
                      "external_row_ids": (expected or {}).get("external_row_ids", ""), "note": note})
        return True

    def pick(pool: list, n: int, key=lambda x: x) -> list:
        # Every caller passes a list of DOIs, so --require-on-disk filters here and
        # applies to every stratum at once: a quota is then filled from papers a
        # coder can actually open, instead of being spent on absent ones.
        if args.require_on_disk:
            pool = [d for d in pool if on_disk(d)]
        pool = sorted(pool, key=key)
        return rng.sample(pool, min(n, len(pool)))

    # 1. FLoRa: 15/15/15 by result, >=50% non-psychology
    flora = read_csv(SILVER_DIR / "flora.csv") if (SILVER_DIR / "flora.csv").exists() else []
    by_paper = defaultdict(list)
    for r in flora:
        by_paper[r["replication_doi_norm"]].append(r)
    for res in ("success", "failure", "inconclusive"):
        pool = [d for d, rs in by_paper.items() if _paper_mode(rs, "result") == res]
        nonpsych = [d for d in pool if discipline_group(by_paper[d][0].get("discipline")) != "psych"]
        psych = [d for d in pool if d not in set(nonpsych)]
        chosen = pick(nonpsych, args.n_flora // 3 - args.n_flora // 6) + pick(psych, args.n_flora // 6)
        for d in chosen:
            rs = by_paper[d]
            add(d, "flora", f"flora:{res}", {"discipline_group": discipline_group(rs[0].get("discipline")), "result": res,
                                             "external_row_ids": ";".join(r["row_id"] for r in rs)})
    # 2. FReD v2.4.2: 9/8/8 by result, converted preferred
    fred = read_csv(SILVER_DIR / "fred_v2_4_2.csv") if (SILVER_DIR / "fred_v2_4_2.csv").exists() else []
    fp = defaultdict(list)
    for r in fred:
        fp[r["replication_doi_norm"]].append(r)
    for res, n in (("success", 9), ("failure", 8), ("inconclusive", 8)):
        pool = [d for d, rs in fp.items() if _paper_mode(rs, "result") == res]
        chosen = pick([d for d in pool if converted(d)], n) or pick(pool, n)
        for d in chosen:
            rs = fp[d]
            add(d, "fred_v242", f"fred:{res}", {"discipline_group": discipline_group(rs[0].get("discipline")), "result": res,
                                                "external_row_ids": ";".join(r["row_id"] for r in rs[:6])})
    # 3. main-GT human rows: all papers
    human = read_csv(SILVER_DIR / "main_gt_human.csv") if (SILVER_DIR / "main_gt_human.csv").exists() else []
    hp = defaultdict(list)
    for r in human:
        hp[normalize_doi(r["replication_url"]) or ""].append(r)
    for d, rs in hp.items():
        add(d, "main_gt_human", "main_gt_human", {"discipline_group": discipline_group(rs[0].get("openalex_field", "")),
                                                   "result": _paper_mode(rs, "result"), "external_row_ids": ";".join(r["row_id"] for r in rs)},
            note="prior_exposure=yes (Dan has seen pipeline output for these papers)")
    # production DB derived strata
    def db_paper(d):
        rs = dbp[d]
        return {"discipline_group": discipline_group(_paper_mode(rs, "discipline")), "result": _paper_mode(rs, "result"),
                "replication_type": TYPE_COLLAPSE.get(_paper_mode(rs, "replication_type"), _paper_mode(rs, "replication_type"))}
    pipeline_papers = [d for d, rs in dbp.items() if converted(d) and all((r.get("validated") or "").lower() != "yes" for r in rs)]
    # 4. biomedical human-coded initiatives (RP:CB, BRI, RSESR) + Dan's biology rows
    for d, rs in dbp.items():
        tags = {r.get("replication_initiative_tag", "") for r in rs}
        src = " ".join(r.get("source", "") for r in rs)
        if tags & {"RP:CB", "BRI", "RSESR"} or ("Brazilian" in src):
            add(d, "biomed_initiative", f"biomed:{(tags & {'RP:CB','BRI','RSESR'} or {'BRI'}).pop()}", db_paper(d))
    # 5. medical-fields hand-built slice: stratified by subdiscipline
    med = [d for d in pipeline_papers if _paper_mode(dbp[d], "discipline") == "medical fields"]
    by_sub = defaultdict(list)
    for d in med:
        by_sub[_paper_mode(dbp[d], "subdiscipline") or "(none)"].append(d)
    quota = max(1, args.n_medical // max(1, len(by_sub)))
    med_chosen = []
    for sub, pool in sorted(by_sub.items()):
        med_chosen += pick(pool, quota)
    med_chosen += pick([d for d in med if d not in set(med_chosen)], max(0, args.n_medical - len(med_chosen)))
    for d in med_chosen[:args.n_medical]:
        add(d, "medical_slice", f"medical:{_paper_mode(dbp[d], 'subdiscipline') or '(none)'}", db_paper(d))
    # 6. prod slice: 5 discipline groups x 4 types, 3 per cell; ensure >=20 with inconclusive/reversal
    cells = defaultdict(list)
    for d in pipeline_papers:
        e = db_paper(d)
        if e["replication_type"] in TYPE_VALUES:
            cells[(e["discipline_group"], e["replication_type"])].append(d)
    for (g, t), pool in sorted(cells.items()):
        for d in pick(pool, args.per_cell):
            add(d, "prod_slice", f"prod:{g}:{t}", db_paper(d))
    incon = [d for d in pipeline_papers if any(norm_label(r.get("result")) in ("inconclusive", "reversal") for r in dbp[d])]
    have = sum(1 for f in frame if f["source"] == "prod_slice" and f["expected_result"] in ("inconclusive", "reversal"))
    for d in pick([d for d in incon if d not in taken], max(0, 20 - have)):
        add(d, "prod_slice", "prod:inconclusive_or_reversal", db_paper(d))
    # 7. ReplicationWiki economics (human-identified close replications)
    rw = [d for d, rs in dbp.items() if converted(d) and any("replication wiki" in (r.get("validated_person") or "").lower() for r in rs)]
    for d in pick(rw, args.n_repwiki):
        add(d, "replication_wiki", "repwiki:econ", db_paper(d))
    # 8. Curate Science (stats-rich)
    cs = [d for d, rs in dbp.items() if converted(d) and any("curate" in (r.get("source") or "").lower() or "curate" in (r.get("validated_person") or "").lower() for r in rs)]
    for d in pick(cs, args.n_curate):
        add(d, "curate_science", "curate", db_paper(d))
    # 9. reversal stratum: DB papers with a reversal row + 'opposite direction' text without the label
    rev = [d for d, rs in dbp.items() if converted(d) and any(norm_label(r.get("result")) == "reversal" for r in rs)]
    for d in pick(rev, args.n_reversal):
        add(d, "reversal_stratum", "reversal:labelled", db_paper(d))
    opp = [d for d, rs in dbp.items() if converted(d) and d not in taken and any(
        re.search(r"opposite direction|reversed|reversal", (r.get("explanation") or "") + " " + (r.get("description") or ""), re.I)
        and norm_label(r.get("result")) != "reversal" for r in rs)]
    for d in pick(opp, args.n_reversal // 2):
        add(d, "reversal_stratum", "reversal:opposite_wording_unlabelled", db_paper(d))
    # 10. negatives: random high-confidence screened negatives + adversarial from classified.csv
    neg_pool = [doi for doi, c in cat.items() if str(c.get("contains_replications")) in ("0", "False", "false")
                and (c.get("screened_confidence") or "") == "high" and c.get("status") in ("screened", "extracted")]
    for d in pick(neg_pool, args.n_negative // 2):
        add(d, "negative_random", "negative:random", note="human must confirm non-replication")
    adv = []
    if config.CLASSIFIED_CSV.exists():
        pat = re.compile(r"reproducib|re-?analys|meta-?analy|biological replicate|technical replicate|commentary|within-study", re.I)
        for r in read_csv(config.CLASSIFIED_CSV):
            if str(r.get("is_replication")).lower() == "false" and pat.search((r.get("title") or "") + " " + (r.get("abstract") or "")):
                d = normalize_doi(r.get("doi", ""))
                if d and converted(d):
                    adv.append(d)
    for d in pick(adv, args.n_negative - args.n_negative // 2):
        add(d, "negative_adversarial", "negative:adversarial", note="human must confirm non-replication")

    frame_path = CODING_DIR / f"frame_gold_v{args.gold_version}_UNBLINDED.csv"
    write_csv(frame_path, frame)
    print(f"sampling frame: {len(frame)} papers -> {frame_path}")
    print("by source:", dict(Counter(f['source'] for f in frame)))
    print("by discipline group:", dict(Counter(f['discipline_group'] or '(none)' for f in frame)))
    nodisc = [f for f in frame if not f["discipline_group"]]
    print(f"no discipline: {len(nodisc)}"
          + (f" ({sum(1 for f in nodisc if f['source'].startswith('negative'))} of them negatives, "
             f"which no replications database can cover)" if nodisc else ""))
    print("converted already:", sum(1 for f in frame if f['converted']))
    absent = [f for f in frame if not on_disk(f["replication_doi"])]
    print(f"not readable on disk: {len(absent)}"
          + (" (--require-on-disk was set; these came from strata that add without pick)" if args.require_on_disk and absent else ""))
    slug = f"gold_v{args.gold_version}"
    if (config.DOI_RUNS_DIR / slug).exists():
        if args.force:
            delete_run(slug)
        else:
            print(f"doi run {slug} exists; --force to recreate"); return
    meta = create_run(slug, csv_text="doi\n" + "\n".join(f["replication_doi"] for f in frame) + "\n")
    print(f"doi run {slug}: {meta['n_dois']} DOIs -> {config.DOI_RUNS_DIR / slug}")


# ── coding sheets / agreement / adjudication / build ─────────────────────────
def _frame(gv: int) -> list[dict]:
    p = CODING_DIR / f"frame_gold_v{gv}_UNBLINDED.csv"
    if not p.exists():
        sys.exit(f"ERROR: {p} not found — run `harness.py sample --gold-version {gv}` first")
    return read_csv(p)


def _external_rows(row_ids: set[str]) -> dict[str, dict]:
    out = {}
    for name in ("flora.csv", "fred_v2_4_2.csv", "main_gt_human.csv"):
        p = SILVER_DIR / name
        if p.exists():
            for r in read_csv(p):
                if r.get("row_id") in row_ids:
                    out[r["row_id"]] = r
    return out


def cmd_coding_sheet(args) -> None:
    frame = _frame(args.gold_version)
    # A paper that is not on the drive cannot be coded, and listing it only earns
    # blank rows an adjudicator has to explain later. Some strata add without
    # going through `pick`, so --require-on-disk at sample time does not catch all.
    absent = [f for f in frame if paper_dir_for(f["paper_folder"]) is None]
    frame = [f for f in frame if paper_dir_for(f["paper_folder"]) is not None]
    if absent:
        print(f"skipping {len(absent)} paper(s) not readable on disk: "
              + ", ".join(f["paper_folder"] for f in absent[:5])
              + (" ..." if len(absent) > 5 else ""))
    ext_ids = {i for f in frame for i in (f.get("external_row_ids") or "").split(";") if i}
    ext = _external_rows(ext_ids)
    sheet = []
    for f in frame:
        neg = f["source"].startswith("negative")
        ids = [i for i in (f.get("external_row_ids") or "").split(";") if i]
        if neg:
            sheet.append({"row_id": f"{f['paper_folder']}#neg", "replication_doi": f["replication_doi"], "paper_folder": f["paper_folder"],
                          "external_row_id": "", "original_hint": "", "is_replication_paper": "", "why_negative": ""})
            continue
        if ids:
            for i in ids:
                e = ext.get(i, {})
                hint = f"{e.get('original_title','')} ({e.get('original_year','')})".strip()
                sheet.append({"row_id": f"{f['paper_folder']}#{i}", "replication_doi": f["replication_doi"], "paper_folder": f["paper_folder"],
                              "external_row_id": i, "original_hint": hint, "is_replication_paper": "yes"})
        else:
            sheet.append({"row_id": f"{f['paper_folder']}#1", "replication_doi": f["replication_doi"], "paper_folder": f["paper_folder"],
                          "external_row_id": "", "original_hint": "", "is_replication_paper": "yes"})
    # The statistical columns are optional by codebook ("lower priority than the
    # identification and classification fields") and worthless against a stat-free
    # extractor, which emits none of them. Dropping them takes the sheet from 23
    # coded columns to 9 -- the difference between a tractable coding job and an
    # untractable one -- and `build-gold` records that they were never coded so a
    # later reader cannot mistake blank for measured.
    coded = [f for f in CODED_FIELDS if f not in set(STAT_FIELDS)] if args.no_stats else CODED_FIELDS
    cols = ["row_id", "replication_doi", "paper_folder", "external_row_id", "original_hint", "is_replication_paper",
            "why_negative", *coded, "gt_ambiguity", "notes"]
    for r in sheet:
        for c in cols:
            r.setdefault(c, "")
    bad = set(cols) - SHEET_ALLOWED
    assert not bad, f"blinding violation: sheet would contain {bad}"
    out = CODING_DIR / f"sheet_gold_v{args.gold_version}_{args.coder}.csv"
    if out.exists() and not args.force:
        sys.exit(f"{out} exists (coding in progress?) — use --force to overwrite")
    write_csv(out, sheet, cols)
    print(f"blinded coding sheet: {len(sheet)} rows, {len(coded)} coded fields"
          + (" (statistics omitted)" if args.no_stats else "") + f" -> {out}")
    print("Coders: add rows for extra entries with a blank row_id; never open <tag>/ subfolders (see codebook.md).")


def _read_sheet(gv: int, coder: str) -> list[dict]:
    p = CODING_DIR / f"sheet_gold_v{gv}_{coder}.csv"
    if not p.exists():
        sys.exit(f"ERROR: {p} not found")
    rows = read_csv(p)
    for k, r in enumerate(rows):
        if not r.get("row_id"):
            r["row_id"] = f"{r.get('paper_folder','')}#new{k}"
    return rows


def _within(a, b, tol: float) -> bool | None:
    x, y = to_float(a), to_float(b)
    if x is None or y is None:
        return None
    return abs(x - y) <= tol * max(abs(x), 1e-9)


def pair_extra_entries(A: dict, B: dict) -> list[tuple[str, str]]:
    """Match up entries the two coders each added, within a paper.

    An anchored row has the same `row_id` in both sheets, so it compares directly.
    An entry a coder ADDS gets a synthetic id from its position in that coder's
    sheet, which carries no meaning across sheets -- so without this, two coders
    who both found the same third effect look like two one-sided rows and the
    comparison silently shrinks to the anchored subset. On the 15-paper pilot that
    was 13 rows compared out of 32 and 47 coded.

    Pairs within a paper only, best-first: an identical original DOI is decisive,
    otherwise the description and original title must be recognisably the same
    entry. Anything left unpaired is genuinely one-sided and still goes to the
    adjudicator as `row_exists`.
    """
    def key(r):
        return matching.canonical_doi(doi_of(r, "original_url"))

    def sim(a, b) -> float:
        if key(a) and key(a) == key(b):
            return 1.0
        t = string_ratio(a.get("original_title", ""), b.get("original_title", ""))
        d = string_ratio(a.get("description", ""), b.get("description", ""))
        return max(t, d, (t + d) / 2)

    by_paper: dict[str, list] = defaultdict(lambda: [[], []])
    for i, r in A.items():
        by_paper[r.get("paper_folder", "")][0].append(i)
    for i, r in B.items():
        by_paper[r.get("paper_folder", "")][1].append(i)

    pairs: list[tuple[str, str]] = []
    for _, (ia, ib) in by_paper.items():
        cand = sorted(((sim(A[i], B[j]), i, j) for i in ia for j in ib), reverse=True,
                      key=lambda t: (t[0], t[1], t[2]))
        used_a: set[str] = set()
        used_b: set[str] = set()
        for score, i, j in cand:
            if score < 0.6 or i in used_a or j in used_b:
                continue
            used_a.add(i); used_b.add(j)
            pairs.append((i, j))
    return pairs


def cmd_agreement(args) -> None:
    A = {r["row_id"]: r for r in _read_sheet(args.gold_version, args.coder_a)}
    B = {r["row_id"]: r for r in _read_sheet(args.gold_version, args.coder_b)}
    coded = lambda d, i: bool(norm_label(d[i].get("result")))
    # Anchored rows pair by id; entries the coders added pair by content.
    anchored = [(i, i) for i in A if i in B]
    unanchored_a = {i: r for i, r in A.items() if i not in B and not i.endswith("#neg")}
    unanchored_b = {i: r for i, r in B.items() if i not in A and not i.endswith("#neg")}
    extra_pairs = pair_extra_entries(unanchored_a, unanchored_b)
    shared = [(i, j) for i, j in anchored + extra_pairs if coded(A, i) and coded(B, j)]
    matched_a = {i for i, _ in extra_pairs}
    matched_b = {j for _, j in extra_pairs}
    res = [(norm_label(A[i]["result"]), norm_label(B[j]["result"])) for i, j in shared]
    typ = [(norm_label(A[i]["replication_type"]), norm_label(B[j]["replication_type"])) for i, j in shared
           if norm_label(A[i].get("replication_type")) and norm_label(B[j].get("replication_type"))]
    doi = [(doi_of(A[i], "original_url") == doi_of(B[j], "original_url")) for i, j in shared
           if doi_of(A[i], "original_url") or doi_of(B[j], "original_url")]
    n5 = [v for i, j in shared for v in (_within(A[i].get("replication_n"), B[j].get("replication_n"), 0.05),) if v is not None]
    negs = [i for i in A if i in B and i.endswith("#neg") and norm_label(A[i].get("is_replication_paper")) and norm_label(B[i].get("is_replication_paper"))]
    neg_pairs = [(norm_label(A[i]["is_replication_paper"]), norm_label(B[i]["is_replication_paper"])) for i in negs]
    k_res = cohen_kappa(res) if res else float("nan")
    k_typ = cohen_kappa(typ) if typ else float("nan")
    print(f"rows coded by both: {len(shared)} "
          f"({sum(1 for i, j in shared if i == j)} anchored + {len(extra_pairs)} paired by content)")
    print(f"result:            agreement {sum(a==b for a,b in res)/len(res) if res else float('nan'):.1%}  kappa {k_res:.3f}")
    print(f"replication_type:  agreement {sum(a==b for a,b in typ)/len(typ) if typ else float('nan'):.1%}  kappa {k_typ:.3f} (n={len(typ)})")
    print(f"original DOI exact: {sum(doi)/len(doi) if doi else float('nan'):.1%} (n={len(doi)})")
    print(f"replication_n within 5%: {sum(n5)/len(n5) if n5 else float('nan'):.1%} (n={len(n5)})")
    if neg_pairs:
        print(f"negative papers: agreement {sum(a==b for a,b in neg_pairs)/len(neg_pairs):.1%} (n={len(neg_pairs)})")
    verdict = ("PROCEED" if k_res >= 0.70 else "REFINE codebook and re-code 20 papers" if k_res >= 0.55 else "STOP: not reliable as specified")
    print(f"pre-registered ladder (result kappa): {verdict}; replication_type target >=0.60 -> "
          f"{'ok' if k_typ >= 0.60 else 'below target'}; DOI target >=0.95 -> {'ok' if doi and sum(doi)/len(doi) >= 0.95 else 'below target'}")
    # adjudication queue: every disagreement on result/type/DOI + seeded 20% of agreements
    rng = random.Random(args.seed)
    queue = []
    for i, j in shared:
        a, b = A[i], B[j]
        fields = []
        if norm_label(a["result"]) != norm_label(b["result"]):
            fields.append("result")
        if norm_label(a.get("replication_type")) != norm_label(b.get("replication_type")):
            fields.append("replication_type")
        if doi_of(a, "original_url") != doi_of(b, "original_url"):
            fields.append("original_url")
        reason = "disagreement" if fields else ("audit_sample" if rng.random() < args.audit_frac else "")
        if not reason:
            continue
        for f_ in (fields or ["result"]):
            queue.append({"row_id": i if i == j else f"{i}~{j}",
                          "paper_folder": a.get("paper_folder", ""), "field": f_, "reason": reason,
                          "a_value": a.get(f_, ""), "b_value": b.get(f_, ""), "a_description": a.get("description", "")[:200],
                          "b_description": b.get("description", "")[:200], "adjudicated_value": "", "adjudicator_id": "",
                          "adjudication_note": "", "gt_ambiguity": ""})
    for i in negs:
        if neg_pairs and norm_label(A[i]["is_replication_paper"]) != norm_label(B[i]["is_replication_paper"]):
            queue.append({"row_id": i, "paper_folder": A[i].get("paper_folder", ""), "field": "is_replication_paper", "reason": "disagreement",
                          "a_value": A[i]["is_replication_paper"], "b_value": B[i]["is_replication_paper"], "a_description": "", "b_description": "",
                          "adjudicated_value": "", "adjudicator_id": "", "adjudication_note": "", "gt_ambiguity": ""})
    only_a = [i for i in unanchored_a if i not in matched_a]
    only_b = [i for i in unanchored_b if i not in matched_b]
    for i, who in [(i, "a") for i in only_a] + [(i, "b") for i in only_b]:
        src = A if who == "a" else B
        queue.append({"row_id": i, "paper_folder": src[i].get("paper_folder", ""), "field": "row_exists", "reason": f"only coder {who} listed this entry",
                      "a_value": "present" if who == "a" else "", "b_value": "present" if who == "b" else "",
                      "a_description": src[i].get("description", "")[:200], "b_description": "", "adjudicated_value": "",
                      "adjudicator_id": "", "adjudication_note": "", "gt_ambiguity": ""})
    qp = CODING_DIR / f"adjudication_queue_gold_v{args.gold_version}.csv"
    if qp.exists() and not args.force:
        old = {(r["row_id"], r["field"]): r for r in read_csv(qp)}
        for q in queue:
            o = old.get((q["row_id"], q["field"]))
            if o:
                for k in ("adjudicated_value", "adjudicator_id", "adjudication_note", "gt_ambiguity"):
                    q[k] = o.get(k, "")
    write_csv(qp, queue)
    print(f"adjudication queue: {len(queue)} items ({sum(1 for q in queue if q['reason']=='disagreement')} disagreements, "
          f"{sum(1 for q in queue if q['reason']=='audit_sample')} audit samples) -> {qp}")


def _discussion_excerpt(folder: str, papers_dir: Path, max_chars: int = 2500) -> str:
    for name in ("body.md",) + tuple(p.name for p in (papers_dir / folder).glob("*_from_*.md")) if (papers_dir / folder).is_dir() else ():
        p = papers_dir / folder / name
        if p.exists():
            t = p.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"(?:^|\n)#+\s*(General\s+)?(Discussion|Conclusion)", t, re.I)
            return (t[m.start():m.start() + max_chars] if m else t[-max_chars:])
    return "(no full text found)"


def cmd_adjudicate(args) -> None:
    qp = CODING_DIR / f"adjudication_queue_gold_v{args.gold_version}.csv"
    queue = read_csv(qp)
    papers_dir = Path(args.papers_dir) if args.papers_dir else config.PAPERS_DIR
    todo = [q for q in queue if not q.get("adjudicated_value")]
    print(f"{len(todo)} of {len(queue)} queue items need adjudication")
    for k, q in enumerate(todo, 1):
        print("\n" + "=" * 70 + f"\n[{k}/{len(todo)}] {q['row_id']}  field={q['field']}  ({q['reason']})")
        print(f"  A: {q['a_value']!r}   B: {q['b_value']!r}")
        if q.get("a_description") or q.get("b_description"):
            print(f"  A desc: {q['a_description']}\n  B desc: {q['b_description']}")
        print("-" * 70 + "\n" + _discussion_excerpt(q["paper_folder"], papers_dir) + "\n" + "-" * 70)
        while True:
            ans = input("[a] take A  [b] take B  [v] enter value  [s] skip  [q] quit: ").strip().lower()
            if ans == "q":
                write_csv(qp, queue); return
            if ans == "s":
                break
            if ans in ("a", "b", "v"):
                q["adjudicated_value"] = q["a_value"] if ans == "a" else q["b_value"] if ans == "b" else input("value: ").strip()
                q["adjudicator_id"] = args.adjudicator
                q["adjudication_note"] = input("note (optional): ").strip()
                q["gt_ambiguity"] = "ambiguous" if input("ambiguous? [y/N]: ").strip().lower() == "y" else "clear"
                break
        write_csv(qp, queue)
    print(f"saved -> {qp}")


def row_provenance(row_id: str, adj: dict, coder_a: str, coder_b: str | None) -> str:
    """`human:adjudicated` only where a human actually ruled on this row.

    Coders may be models (a different family from the one under test; see
    benchmarking/README.md). Where two of them agreed and no human ever read the
    row, the row is evidence, not adjudicated truth, and stamping it
    `human:adjudicated` would overstate it in exactly the way that made the
    February 2026 ground truth unusable. Such rows stay scoreable -- they are not
    `pipeline:`-authored, so the provenance guard passes them -- but they say what
    they are, and `evaluate` breaks results down by provenance so the mix is
    visible in every report.
    """
    ruled = any(r_ == row_id and q.get("adjudicated_value") and q.get("adjudicator_id")
                for (r_, _), q in adj.items())
    if ruled:
        return "human:adjudicated"
    return f"ai_consensus:{coder_a}+{coder_b}" if coder_b else f"single_coder:{coder_a}"


def cmd_build_gold(args) -> None:
    gv = args.gold_version
    frame = {f["paper_folder"]: f for f in _frame(gv)}
    A = _read_sheet(gv, args.coder_a)
    B = {r["row_id"]: r for r in _read_sheet(gv, args.coder_b)} if args.coder_b else {}
    qp = CODING_DIR / f"adjudication_queue_gold_v{gv}.csv"
    queue = read_csv(qp) if qp.exists() else []
    adj = {(q["row_id"], q["field"]): q for q in queue}
    pending = [q for q in queue if q["reason"] == "disagreement" and not q.get("adjudicated_value")]
    if pending and not args.allow_unadjudicated:
        sys.exit(f"ERROR: {len(pending)} disagreements are not adjudicated; run `harness.py adjudicate` or pass --allow-unadjudicated (drops them).")
    dropped = {q["row_id"] for q in pending}
    salt = args.salt or f"gold_v{gv}"
    gold, negs = [], []
    for a in A:
        rid = a["row_id"]
        if rid in dropped:
            continue
        f = frame.get(a.get("paper_folder", ""), {})
        doi = a.get("replication_doi") or f.get("replication_doi", "")
        b = B.get(rid, {})
        if rid.endswith("#neg"):
            lab = adj.get((rid, "is_replication_paper"), {}).get("adjudicated_value") or a.get("is_replication_paper", "")
            negs.append({"replication_doi": doi, "paper_folder": a.get("paper_folder", ""), "source": f.get("source", ""),
                         "label": "negative" if norm_label(lab) in ("no", "negative", "n") else "positive_reclassified",
                         "why_negative": a.get("why_negative", ""), "coder_a_id": args.coder_a, "coder_b_id": args.coder_b or "",
                         "a_label": a.get("is_replication_paper", ""), "b_label": b.get("is_replication_paper", ""),
                         "split": split_of(doi, salt),
                         "provenance": row_provenance(rid, adj, args.coder_a, args.coder_b)})
            continue
        if not norm_label(a.get("result")):
            continue
        row = {"row_id": rid, "replication_doi": doi, "paper_folder": a.get("paper_folder", ""), "source": f.get("source", "gold"),
               "provenance": row_provenance(rid, adj, args.coder_a, args.coder_b), "split": split_of(doi, salt),
               "discipline_group": f.get("discipline_group", ""), "year_bucket": "", "tier_available": "",
               "external_label": f.get("expected_result", ""), "external_row_id": a.get("external_row_id", ""),
               "prior_exposure": "yes" if "prior_exposure" in (f.get("note") or "") else "no",
               "coder_a_id": args.coder_a, "coder_b_id": args.coder_b or ""}
        for fld in CODED_FIELDS:
            row[f"a_{fld}"] = a.get(fld, "")
            row[f"b_{fld}"] = b.get(fld, "")
            q = adj.get((rid, fld))
            row[fld] = q["adjudicated_value"] if q and q.get("adjudicated_value") else a.get(fld, "")
        qa = [q for (r_, _), q in adj.items() if r_ == rid and q.get("gt_ambiguity")]
        row["gt_ambiguity"] = "ambiguous" if any(q["gt_ambiguity"] == "ambiguous" for q in qa) else (a.get("gt_ambiguity") or "clear")
        row["adjudicator_id"] = ";".join(sorted({q["adjudicator_id"] for q in qa if q.get("adjudicator_id")}))
        row["adjudication_note"] = " | ".join(q["adjudication_note"] for q in qa if q.get("adjudication_note"))
        row["codebook_version"] = args.codebook_version
        row["coded_at"] = time.strftime("%Y-%m-%d")
        pd_ = config.PAPERS_DIR / row["paper_folder"]
        if pd_.is_dir():
            row["tier_available"] = "xml" if list(pd_.glob("*_from_xml.md")) else "html" if list(pd_.glob("*_from_html.md")) else "grobid" if (pd_ / "body.md").exists() else "pdf"
        gold.append(row)
    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(GOLD_DIR / "gold_rows.csv", gold)
    write_csv(GOLD_DIR / "gold_negatives.csv", negs, ["replication_doi", "paper_folder", "source", "label", "why_negative",
                                                       "coder_a_id", "coder_b_id", "a_label", "b_label", "split", "provenance"])
    files = {str(p.relative_to(BENCH_DIR)): sha256_file(p) for p in
             [GOLD_DIR / "gold_rows.csv", GOLD_DIR / "gold_negatives.csv", CODEBOOK] + sorted(SILVER_DIR.glob("*.csv"))}
    manifest = {"gold_version": gv, "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "seed": SEED_DEFAULT, "salt": salt,
                "codebook_version": args.codebook_version, "codebook_sha256": sha256_file(CODEBOOK), "files": files,
                "n_rows": len(gold), "n_negatives": len(negs),
                "splits": {s: sorted({r["replication_doi"] for r in gold if r["split"] == s}) for s in ("dev", "test")},
                "n_by_source": dict(Counter(r["source"] for r in gold)),
                "n_by_split": dict(Counter(r["split"] for r in gold)),
                "n_result": dict(Counter(norm_label(r["result"]) for r in gold)),
                "coder_a_id": args.coder_a, "coder_b_id": args.coder_b or "",
                # Blank statistics mean "never coded", not "the paper reports none".
                "stats_coded": any((r.get(f) or "").strip() for r in gold for f in STAT_FIELDS),
                "n_by_provenance": dict(Counter(r["provenance"] for r in gold))}
    (GOLD_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"gold_v{gv}: {len(gold)} rows, {len(negs)} negatives; splits {manifest['n_by_split']}; results {manifest['n_result']}")
    print(f"provenance: {manifest['n_by_provenance']}; statistics coded: {manifest['stats_coded']}")
    if not manifest["stats_coded"]:
        print("  (stat-free gold: `evaluate` will say so instead of scoring statistics)")
    print(f"-> {GOLD_DIR}/gold_rows.csv, gold_negatives.csv, manifest.json")


# ── match audit ──────────────────────────────────────────────────────────────
def cmd_match_audit(args) -> None:
    res_dir = Path(args.results)
    log = res_dir / "match_log.jsonl"
    if not log.exists():
        sys.exit(f"ERROR: {log} not found (evaluate with --matcher llm first)")
    decisions = [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]
    rng = random.Random(args.seed)
    picked = [d for d in decisions if d.get("confidence") in ("low", "medium") or rng.random() < args.frac]
    sheet_path = res_dir / "match_audit_sheet.csv"
    if args.score:
        rows = read_csv(sheet_path)
        judged = [r for r in rows if norm_label(r.get("human_verdict")) in ("correct", "wrong")]
        if not judged:
            sys.exit("no human verdicts filled in yet (human_verdict = correct | wrong)")
        pos = [r for r in judged if r["relation"] in matching.MATCH_RELATIONS]
        neg = [r for r in judged if r["relation"] not in matching.MATCH_RELATIONS]
        prec = sum(norm_label(r["human_verdict"]) == "correct" for r in pos) / len(pos) if pos else None
        neg_ok = sum(norm_label(r["human_verdict"]) == "correct" for r in neg) / len(neg) if neg else None
        out = {"n_audited": len(judged), "matcher_precision_on_matches": prec, "n_matches_audited": len(pos),
               "non_match_correct_rate": neg_ok, "n_non_matches_audited": len(neg),
               "by_confidence": {c: {"n": len([r for r in judged if r["confidence"] == c]),
                                     "correct": sum(norm_label(r["human_verdict"]) == "correct" for r in judged if r["confidence"] == c)}
                                 for c in ("high", "medium", "low")}}
        (res_dir / "match_audit_metrics.json").write_text(json.dumps(out, indent=2))
        print(json.dumps(out, indent=2)); return
    rows = [{"key": d["key"], "gt_row_id": d.get("gt_row_id"), "replication_doi": d.get("replication_doi"),
             "n_candidates": d.get("n_candidates"), "match": d.get("match"), "relation": d.get("relation"),
             "confidence": d.get("confidence"), "reason": d.get("reason"), "human_verdict": "", "human_note": ""} for d in picked]
    write_csv(sheet_path, rows)
    print(f"audit sheet: {len(rows)} of {len(decisions)} decisions ({args.frac:.0%} seeded sample + all low/medium) -> {sheet_path}")
    print("Fill human_verdict with correct|wrong, then re-run with --score.")


# ── CLI ──────────────────────────────────────────────────────────────────────
def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("import-flora"); s.add_argument("--xlsx", default=str(FLORA_XLSX)); s.set_defaults(func=cmd_import_flora)
    s = sub.add_parser("import-fred"); s.add_argument("--xlsx", default=str(FRED_XLSX)); s.set_defaults(func=cmd_import_fred)

    s = sub.add_parser("sample", help="build the gold_v<N> sampling frame and doi run")
    s.add_argument("--gold-version", type=int, required=True); s.add_argument("--seed", type=int, default=SEED_DEFAULT)
    s.add_argument("--n-flora", type=int, default=45); s.add_argument("--n-medical", type=int, default=50)
    s.add_argument("--per-cell", type=int, default=3); s.add_argument("--n-repwiki", type=int, default=40)
    s.add_argument("--n-curate", type=int, default=20); s.add_argument("--n-reversal", type=int, default=20)
    s.add_argument("--n-negative", type=int, default=60); s.add_argument("--force", action="store_true")
    s.add_argument("--require-on-disk", action="store_true",
                   help="draw every stratum only from papers whose folder is readable now "
                        "(the v1 frame spent 38 of 45 FLoRa slots on papers that never downloaded)")
    s.set_defaults(func=cmd_sample)

    s = sub.add_parser("coding-sheet"); s.add_argument("--gold-version", type=int, required=True)
    s.add_argument("--coder", required=True); s.add_argument("--force", action="store_true")
    s.add_argument("--no-stats", action="store_true",
                   help="omit the 14 statistical columns: the codebook calls them lower priority "
                        "and a stat-free extractor emits none, so coding them buys nothing")
    s.set_defaults(func=cmd_coding_sheet)

    s = sub.add_parser("agreement"); s.add_argument("--gold-version", type=int, required=True)
    s.add_argument("--coder-a", required=True); s.add_argument("--coder-b", required=True)
    s.add_argument("--audit-frac", type=float, default=0.2); s.add_argument("--seed", type=int, default=SEED_DEFAULT)
    s.add_argument("--force", action="store_true", help="discard existing adjudications in the queue"); s.set_defaults(func=cmd_agreement)

    s = sub.add_parser("adjudicate"); s.add_argument("--gold-version", type=int, required=True)
    s.add_argument("--adjudicator", required=True); s.add_argument("--papers-dir", default=None); s.set_defaults(func=cmd_adjudicate)

    s = sub.add_parser("build-gold"); s.add_argument("--gold-version", type=int, required=True)
    s.add_argument("--coder-a", required=True); s.add_argument("--coder-b", default=None)
    s.add_argument("--codebook-version", default="codebook_v1"); s.add_argument("--salt", default=None)
    s.add_argument("--allow-unadjudicated", action="store_true"); s.set_defaults(func=cmd_build_gold)

    s = sub.add_parser("status"); s.add_argument("--run", required=True); s.add_argument("--tags", default=None)
    s.add_argument("--papers-dir", default=None); s.set_defaults(func=cmd_status)

    s = sub.add_parser("run", help="extract a doi run (always --dontcheck)")
    s.add_argument("--run", required=True); s.add_argument("--tag", required=True); s.add_argument("--model", default="sonnet")
    s.add_argument("--workers", type=int, default=4); s.add_argument("--limit", type=int, default=None)
    s.add_argument("--force-tier", choices=["xml", "html", "grobid", "pdf"], default=None)
    s.add_argument("--level", choices=["full", "base"], default="full",
                   help="extract.py --level: full (normal) or base (same agent, statistics stripped from the prompt)")
    s.add_argument("--remaining", action="store_true", help="use include_list_remaining.txt from `status`")
    s.add_argument("--papers-dir", default=None); s.add_argument("--dry-run", action="store_true"); s.set_defaults(func=cmd_run)

    s = sub.add_parser("evaluate", help="score extractions against ground truth")
    s.add_argument("--gt", default="gold", help="gold | silver:flora | silver:fred_v242 | silver:main_gt_fred_api | silver:main_gt_human | <csv path>")
    s.add_argument("--run", default=None, help="doi run slug; restricts GT to the run's papers")
    s.add_argument("--tags", required=True, help="comma-separated extraction tags (first match wins per paper)")
    s.add_argument("--split", choices=["dev", "test", "all"], default="all")
    s.add_argument("--release", action="store_true"); s.add_argument("--allow-dirty-gt", action="store_true")
    s.add_argument("--allow-paper-level-gt", action="store_true",
                   help="score against paper-level GT (FLoRa) anyway; retired 2026-09-04 because one "
                        "verdict per paper cannot say which effect the extractor got right")
    s.add_argument("--matcher", choices=["llm", "deterministic"], default="llm")
    s.add_argument("--provider", default="claude_cli"); s.add_argument("--match-model", default="haiku")
    s.add_argument("--offline", action="store_true", help="LLM matcher answers only from cache")
    s.add_argument("--no-fuzzy", action="store_true", help="disable the string-similarity fallback")
    s.add_argument("--bootstrap", type=int, default=2000); s.add_argument("--papers-dir", default=None)
    s.add_argument("--out-dir", default=None); s.set_defaults(func=cmd_evaluate)
    s.add_argument("--judge-workers", type=int, default=4, help="papers matched concurrently (each judge call is one claude CLI subprocess)")

    s = sub.add_parser("retest", help="run-to-run agreement between two tags")
    s.add_argument("--run", default=None); s.add_argument("--tag-a", required=True); s.add_argument("--tag-b", required=True)
    s.add_argument("--matcher", choices=["llm", "deterministic"], default="deterministic")
    s.add_argument("--provider", default="claude_cli"); s.add_argument("--match-model", default="haiku")
    s.add_argument("--offline", action="store_true"); s.add_argument("--papers-dir", default=None)
    s.add_argument("--out", default=None); s.set_defaults(func=cmd_retest)
    s.add_argument("--judge-workers", type=int, default=4)

    s = sub.add_parser("match-audit"); s.add_argument("--results", required=True, help="an evaluate output dir")
    s.add_argument("--frac", type=float, default=0.1); s.add_argument("--seed", type=int, default=SEED_DEFAULT)
    s.add_argument("--score", action="store_true"); s.set_defaults(func=cmd_match_audit)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
