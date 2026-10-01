"""
Step 3: LLM-based classification of candidate replication studies.

Reads filtered candidates and uses Claude (via Claude Code CLI) to determine:
  - Is this actually a replication study?
  - What type of replication is it?
  - What original study is being replicated?

Uses the `claude -p` non-interactive CLI with the Haiku model, under the
Max subscription (no API key needed). Concurrent subprocess calls for speed.
Checkpoints for resumability.

Two levels of deduplication before LLM classification:
  Level 2: DOI matches data/processed_manifest.csv with a corpus extraction verdict
           → auto-mark True (extraction found replications) or False (found none)
  Level 1: DOI/PMID matches prior data/classified.csv result → reuse without LLM call

Input:  data/candidates_filtered.csv (from prefilter step)
Output: data/classified.csv, data/confirmed_replications.csv
"""

import argparse
import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from mo_pipeline.corpus.models import normalize_doi
from mo_pipeline.discover.screening_backend import BACKENDS, get_backend
from mo_pipeline.config import (
    DATA_DIR, PROGRESS_DIR,
    CANDIDATES_FILTERED_CSV, CLASSIFIED_CSV, CONFIRMED_REPLICATIONS_CSV,
    PROCESSED_MANIFEST_CSV,
)

csv.field_size_limit(sys.maxsize)

PROGRESS_FILE = PROGRESS_DIR / "classify_progress.json"
DEFAULT_WORKERS = 20           # default concurrent subprocess calls per batch

CLASSIFICATION_FIELDS = [
    "is_replication", "confidence", "replication_type",
    "reasoning", "original_study_mentioned", "original_study_doi",
]

SYSTEM_PROMPT = (
    "You are a scientific literature classifier working across all disciplines. "
    "You return ONLY a raw JSON object — no markdown, no code fences, no "
    "explanation, no preamble."
)

SCREENING_PROMPT = """Analyze this paper's title and abstract. Determine whether this paper reports an experimental replication -- meaning the authors conducted an experiment specifically to test whether the findings of a previously published study can be reproduced.

The corpus spans all disciplines -- psychology, economics, education, linguistics, political science, medicine, biology, chemistry, physics, materials science, and computer science. Do not assume a paper is biomedical, and do not treat social-science, humanities-adjacent, or physical-science papers as out of scope.

Title: {title}
Abstract: {abstract}

IMPORTANT DISTINCTIONS:
- A REPLICATION STUDY explicitly attempts to reproduce a specific prior experiment's findings.
- NOT a replication:
  * Papers that merely "replicate an analysis" on a different dataset (reanalysis)
  * Papers that extend prior work without specifically testing reproducibility
  * Papers using "replication" in a statistical sense (e.g., "biological replicates", "technical replicates", "replication of DNA")
  * Papers that mention replication only in passing or in the discussion
  * Commentary/review papers about the "replication crisis" that don't report new experiments
  * Papers about "replication" in the biological sense (DNA/viral replication)
  * Original discovery studies — e.g., the first GWAS to report an association — that do NOT test a specific prior finding
- ARE a replication (include these):
  * GWAS / genetic-association replication cohorts that test a specific prior association in a new, independent, or larger cohort — mark as "close". These are typically close extensions (same effect tested in a different population or expanded sample size) and are legitimate replications of a prior causal claim.
  * Two-stage GWAS designs where the "replication stage" tests loci identified in a discovery stage on an independent sample — mark as "close".
  * Papers that validate a specific prior association in a different ancestry, population, or larger sample — mark as "close".
  * Conceptual replications (testing the same hypothesis with different methods) — mark as "conceptual"
  * Multi-site studies or consortium studies that include replication arms
  * Registered Replication Reports

Return ONLY a raw JSON object (no markdown, no code fences, no other text):
{{"is_replication": true or false, "confidence": "high" or "medium" or "low", "replication_type": "direct" or "close" or "conceptual" or "systematic" or "multi-site" or null, "reasoning": "one sentence explanation of your classification", "original_study_mentioned": "title or description of the original study being replicated, or null if not identifiable", "original_study_doi": "DOI of the original study if mentioned in the abstract, else null"}}"""


def load_progress():
    if PROGRESS_FILE.exists():
        data = json.loads(PROGRESS_FILE.read_text())
        if "input_row_count" not in data:
            data["input_row_count"] = None
        return data
    return {"classified_indices": [], "input_row_count": None}


def save_progress(progress):
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2))


CONFIDENCE_VALUES = {"high", "medium", "low"}
REPLICATION_TYPE_VALUES = {"direct", "close", "conceptual", "systematic", "multi-site"}
# reasoning stamped on Level-2 rows before 2026-10-01, when every corpus paper was
# auto-confirmed whatever its extraction found. Such rows are never reused.
_LEGACY_AUTO_REASON = "Already ingested into corpus"


def _load_ingested_dois():
    """{doi: "1"|"0"} from processed_manifest.csv (Level 2 dedup).

    Only papers whose corpus extraction reached a verdict are returned. A manifest
    without the contains_replications column predates the fix and listed every
    paper on the drive, so it is ignored until rebuilt.
    """
    verdicts = {}
    if not PROCESSED_MANIFEST_CSV.exists():
        return verdicts
    with open(PROCESSED_MANIFEST_CSV, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "contains_replications" not in (reader.fieldnames or []):
            print("WARNING: processed_manifest.csv predates the contains_replications column; "
                  "ignoring it. Rebuild: python -m mo_pipeline.discover.build_processed_manifest")
            return verdicts
        for row in reader:
            doi = (normalize_doi(row.get("doi", "")) or "")
            verdict = (row.get("contains_replications") or "").strip()
            if doi and verdict in ("0", "1"):
                verdicts[doi] = verdict
    return verdicts


def _as_bool(val):
    if isinstance(val, bool):
        return val
    if isinstance(val, str) and val.strip().lower() in ("true", "false"):
        return val.strip().lower() == "true"
    return None


def _validated(result):
    """The model's verdict with enums checked, or None (a failure, retried next run).

    255 rows once carried the replication type in `confidence`, and anything that
    filters on confidence or type then silently changes the confirmed set.
    """
    is_rep = _as_bool(result.get("is_replication"))
    conf = str(result.get("confidence") or "").strip().lower()
    rtype = result.get("replication_type")
    rtype = "" if rtype in (None, "", "null") else str(rtype).strip().lower()
    if is_rep is None or conf not in CONFIDENCE_VALUES or (rtype and rtype not in REPLICATION_TYPE_VALUES):
        return None
    return {**result, "is_replication": is_rep, "confidence": conf, "replication_type": rtype or None}


def _prior_is_valid(prior):
    if (prior.get("reasoning") or "") == _LEGACY_AUTO_REASON:
        return False
    return _validated(prior) is not None


def _load_prior_classified():
    """Load prior classified.csv into DOI and PMID lookup dicts (Level 1 dedup)."""
    by_doi = {}
    by_pmid = {}
    if not CLASSIFIED_CSV.exists():
        return by_doi, by_pmid
    with open(CLASSIFIED_CSV, "r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            doi = (normalize_doi(row.get("doi", "")) or "")
            pmid = (row.get("pmid") or "").strip()
            if doi:
                by_doi[doi] = row
            if pmid:
                by_pmid[pmid] = row
    return by_doi, by_pmid


def classify_one(idx, title, abstract, backend=None):
    """Classify one paper. Returns (idx, parsed_dict_or_None).

    Delegates to the configured screening backend (default `claude -p` with
    Haiku — see discover/screening_backend.py). The backend exists so a large
    sweep can be moved off the Claude rate-limit pool, which is the binding
    constraint here: ~10k calls/week, shared with extraction via the
    `claude_cli` mutex group.
    """
    prompt = SCREENING_PROMPT.format(
        title=title or "(no title)",
        abstract=abstract or "(no abstract available)",
    )
    if backend is None:
        backend = get_backend()
    return backend.screen(idx, SYSTEM_PROMPT, prompt)


def _auto_row(row, is_replication, reasoning, confidence):
    """Build a classified row without an LLM call (Level 2 dedup or empty rows)."""
    out_row = dict(row)
    for field in CLASSIFICATION_FIELDS:
        out_row[field] = ""
    out_row["is_replication"] = str(is_replication)
    out_row["confidence"] = confidence
    out_row["reasoning"] = reasoning
    return out_row


def _reuse_prior_row(row, prior_row):
    """Reuse a prior classification result verbatim (Level 1 dedup)."""
    out_row = dict(row)
    for field in CLASSIFICATION_FIELDS:
        out_row[field] = prior_row.get(field, "")
    return out_row


def main():
    sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(
        description="Classify replication study candidates via a screening LLM")
    parser.add_argument(
        "-w", "--workers", type=int, default=DEFAULT_WORKERS,
        help=f"Number of concurrent screening calls (default: {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Only classify the first N unclassified rows (useful for prompt testing)",
    )
    parser.add_argument(
        "--provider", choices=sorted(BACKENDS), default=None,
        help="Screening backend. Default comes from config.SCREENING_PROVIDER "
             "(claude_cli). Use openrouter to spend money instead of the "
             "rate-limited Claude budget, e.g. for a large sweep.",
    )
    parser.add_argument(
        "--model", default=None,
        help="Override the screening model for the chosen provider "
             "(e.g. 'haiku', or 'openai/gpt-5-nano' for openrouter).",
    )
    parser.add_argument(
        "--max-llm-calls", type=int, default=None,
        help="Hard ceiling on fresh LLM calls this run; stops cleanly when hit. "
             "Use to stay inside the weekly Claude budget (~10k/week).",
    )
    args = parser.parse_args()
    batch_size = args.workers
    print(f"Running with {batch_size} concurrent workers")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)

    progress = load_progress()
    already_done = set(progress["classified_indices"])

    # Read input
    with open(CANDIDATES_FILTERED_CSV, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        input_fieldnames = reader.fieldnames or []
    print(f"Loaded {len(rows)} filtered candidates")
    print(f"Already classified (this run): {len(already_done)}")

    # Guard against resuming with a stale progress file from a different input CSV
    stored_count = progress.get("input_row_count")
    if stored_count is not None and stored_count != len(rows):
        print(
            f"\nERROR: input CSV has {len(rows)} rows but progress file was built "
            f"from {stored_count} rows. The input was regenerated since the last run.\n"
            f"Delete {PROGRESS_FILE} to start fresh:\n"
            f"  rm {PROGRESS_FILE}\n"
        )
        sys.exit(1)
    progress["input_row_count"] = len(rows)

    # Build dynamic output fieldnames: input columns + classification fields (no dupes)
    output_fieldnames = list(input_fieldnames) + [
        f for f in CLASSIFICATION_FIELDS if f not in input_fieldnames
    ]

    # Load dedup sources
    ingested_dois = _load_ingested_dois()
    prior_by_doi, prior_by_pmid = _load_prior_classified()
    print(f"Level 2 dedup: {len(ingested_dois)} ingested DOIs from processed_manifest.csv")
    print(f"Level 1 dedup: {len(prior_by_doi)} DOIs from prior classified.csv")

    # Load partial results for resume (only if this run has already made progress)
    classified_rows = []
    if already_done and CLASSIFIED_CSV.exists():
        with open(CLASSIFIED_CSV, "r", newline="", encoding="utf-8") as f:
            classified_rows = list(csv.DictReader(f))
        print(f"Resuming with {len(classified_rows)} rows from current run's classified.csv")

    # Build work queue
    work = []
    skipped_ingested = 0
    skipped_prior = 0

    for idx, row in enumerate(rows):
        if idx in already_done:
            continue

        doi = (normalize_doi(row.get("doi", "")) or "")
        pmid = (row.get("pmid") or "").strip()
        title = row.get("title", "")
        abstract = row.get("abstract", "")

        if not title and not abstract:
            classified_rows.append(_auto_row(row, False, "No title or abstract available", "low"))
            already_done.add(idx)
            continue

        # Level 2: the corpus extraction already decided this paper
        if doi and doi in ingested_dois:
            found = ingested_dois[doi] == "1"
            classified_rows.append(_auto_row(
                row, found, "Corpus extraction found replications" if found
                else "Corpus extraction found no replications", "high"))
            already_done.add(idx)
            skipped_ingested += 1
            continue

        # Level 1: classified in a prior run — reuse result without LLM call
        prior = prior_by_doi.get(doi) if doi else None
        if prior is None and pmid:
            prior = prior_by_pmid.get(pmid)
        if prior is not None and _prior_is_valid(prior):
            classified_rows.append(_reuse_prior_row(row, prior))
            already_done.add(idx)
            skipped_prior += 1
            continue

        work.append((idx, row))

    if args.limit:
        work = work[:args.limit]

    # Budget ceiling. The Claude screening budget is ~10k calls/week and shared
    # with extraction, so a full pass over the filtered set can silently blow
    # through it — the row count is NOT the call count, since rows already
    # ingested or previously classified are reused above without an LLM call.
    if args.max_llm_calls is not None and len(work) > args.max_llm_calls:
        print(f"Capping this run at {args.max_llm_calls:,} fresh LLM calls "
              f"(of {len(work):,} outstanding) — rerun to continue.", flush=True)
        work = work[:args.max_llm_calls]

    print(f"Skipped — already ingested (Level 2): {skipped_ingested}")
    print(f"Skipped — prior classify result reused (Level 1): {skipped_prior}")
    print(f"To classify via LLM: {len(work)} (in batches of {batch_size})")

    if not work:
        print("Nothing to send to LLM.")
        _write_outputs(classified_rows, output_fieldnames)
        save_progress(progress)
        return

    new_count = 0
    replication_count = sum(1 for r in classified_rows if r.get("is_replication", "").lower() == "true")
    start_time = time.time()

    # One backend for the whole run. Announce it: which provider is in use
    # determines whether this run spends the rate-limited Claude budget
    # (~10k/week, shared with extraction) or paid OpenRouter capacity.
    backend = get_backend(provider=args.provider, model=args.model)
    print(f"Screening backend: {backend.name} (model: {backend.model})", flush=True)

    # Process in batches of concurrent subprocess calls; checkpoint after each batch
    for batch_start in range(0, len(work), batch_size):
        batch = work[batch_start:batch_start + batch_size]

        with ThreadPoolExecutor(max_workers=batch_size) as executor:
            futures = {}
            for idx, row in batch:
                title = row.get("title", "")
                abstract = row.get("abstract", "")
                future = executor.submit(classify_one, idx, title, abstract, backend)
                futures[future] = (idx, row)

            for future in as_completed(futures):
                idx, row = futures[future]
                result_idx, result = future.result()

                if result is None:
                    print(f"  [{idx}] FAILED — will retry on next run")
                    continue
                checked = _validated(result)
                if checked is None:
                    print(f"  [{idx}] INVALID verdict {json.dumps({k: result.get(k) for k in ('is_replication', 'confidence', 'replication_type')})} — will retry on next run")
                    continue
                result = checked

                out_row = dict(row)
                for field in CLASSIFICATION_FIELDS:
                    val = result.get(field)
                    if val is None:
                        out_row[field] = ""
                    elif isinstance(val, bool):
                        out_row[field] = str(val)
                    else:
                        out_row[field] = str(val)
                classified_rows.append(out_row)

                already_done.add(idx)
                new_count += 1

                is_rep = result.get("is_replication", False)
                if is_rep:
                    replication_count += 1
                conf = result.get("confidence", "?")
                rep_type = result.get("replication_type") or "-"
                title_short = row.get("title", "")[:55]
                print(f"  [{idx}] rep={is_rep} ({conf}) type={rep_type} — {title_short}...")

        progress["classified_indices"] = sorted(already_done)
        _write_outputs(classified_rows, output_fieldnames)
        save_progress(progress)

        elapsed = time.time() - start_time
        rate = new_count / elapsed if elapsed > 0 else 0
        remaining = len(work) - (batch_start + len(batch))
        eta_min = (remaining / rate / 60) if rate > 0 else float("inf")
        print(f"  --- checkpoint: {new_count} LLM done, {replication_count} replications, "
              f"{rate:.2f}/sec, ~{remaining} remaining, ETA ~{eta_min:.0f}min ---")

    # Final write
    progress["classified_indices"] = sorted(already_done)
    _write_outputs(classified_rows, output_fieldnames)
    save_progress(progress)

    confirmed = [r for r in classified_rows if r.get("is_replication", "").lower() == "true"]
    print(f"\nDone. {len(classified_rows)} total classified, {len(confirmed)} confirmed replications.")


def _write_outputs(classified_rows, output_fieldnames):
    """Write both classified.csv and confirmed_replications.csv."""
    with open(CLASSIFIED_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_fieldnames)
        writer.writeheader()
        for row in classified_rows:
            writer.writerow({k: row.get(k, "") for k in output_fieldnames})

    confirmed = [r for r in classified_rows if r.get("is_replication", "").lower() == "true"]
    with open(CONFIRMED_REPLICATIONS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_fieldnames)
        writer.writeheader()
        for row in confirmed:
            writer.writerow({k: row.get(k, "") for k in output_fieldnames})


if __name__ == "__main__":
    main()
