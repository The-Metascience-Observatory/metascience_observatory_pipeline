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
  Level 2: DOI matches data/processed_manifest.csv → already ingested, auto-mark True
  Level 1: DOI/PMID matches prior data/classified.csv result → reuse without LLM call

Input:  data/candidates_filtered.csv (from prefilter step)
Output: data/classified.csv, data/confirmed_replications.csv
"""

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from config import (
    DATA_DIR, PROGRESS_DIR,
    CANDIDATES_FILTERED_CSV, CLASSIFIED_CSV, CONFIRMED_REPLICATIONS_CSV,
    PROCESSED_MANIFEST_CSV,
    LLM_MODEL, LLM_TIMEOUT_SEC,
)

csv.field_size_limit(sys.maxsize)

PROGRESS_FILE = PROGRESS_DIR / "classify_progress.json"
DEFAULT_WORKERS = 20           # default concurrent subprocess calls per batch

CLASSIFICATION_FIELDS = [
    "is_replication", "confidence", "replication_type",
    "reasoning", "original_study_mentioned", "original_study_doi",
]

SYSTEM_PROMPT = (
    "You are a biomedical literature classifier. You return ONLY a raw JSON "
    "object — no markdown, no code fences, no explanation, no preamble."
)

SCREENING_PROMPT = """Analyze this biomedical paper's title and abstract. Determine whether this paper reports an experimental replication -- meaning the authors conducted an experiment specifically to test whether the findings of a previously published study can be reproduced.

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


def _strip_code_fences(text):
    """Remove markdown code fences like ```json ... ``` if present."""
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("\n", 1)
        text = parts[1] if len(parts) > 1 else ""
        if "```" in text:
            text = text.rsplit("```", 1)[0]
    return text.strip()


def _extract_json(text):
    """Best-effort JSON extraction from LLM output."""
    text = _strip_code_fences(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _norm_doi(doi):
    return (doi or "").strip().lower()


def _load_ingested_dois():
    """Load DOI set from processed_manifest.csv (Level 2 dedup)."""
    dois = set()
    if not PROCESSED_MANIFEST_CSV.exists():
        return dois
    with open(PROCESSED_MANIFEST_CSV, "r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            doi = _norm_doi(row.get("doi", ""))
            if doi:
                dois.add(doi)
    return dois


def _load_prior_classified():
    """Load prior classified.csv into DOI and PMID lookup dicts (Level 1 dedup)."""
    by_doi = {}
    by_pmid = {}
    if not CLASSIFIED_CSV.exists():
        return by_doi, by_pmid
    with open(CLASSIFIED_CSV, "r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            doi = _norm_doi(row.get("doi", ""))
            pmid = (row.get("pmid") or "").strip()
            if doi:
                by_doi[doi] = row
            if pmid:
                by_pmid[pmid] = row
    return by_doi, by_pmid


def classify_one(idx, title, abstract):
    """Classify one paper via `claude -p`. Returns (idx, parsed_dict_or_None)."""
    prompt = SCREENING_PROMPT.format(
        title=title or "(no title)",
        abstract=abstract or "(no abstract available)",
    )

    cmd = [
        "claude", "-p",
        "--model", LLM_MODEL,
        "--output-format", "json",
        "--tools", "",
        "--system-prompt", SYSTEM_PROMPT,
        "--no-session-persistence",
    ]

    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=LLM_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        print(f"  [{idx}] TIMEOUT after {LLM_TIMEOUT_SEC}s")
        return idx, None
    except Exception as e:
        print(f"  [{idx}] subprocess error: {e}")
        return idx, None

    if result.returncode != 0:
        err = (result.stderr or "")[:300]
        print(f"  [{idx}] CLI exit {result.returncode}: {err}")
        return idx, None

    try:
        outer = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"  [{idx}] outer JSON parse error: {e}")
        return idx, None

    if outer.get("is_error"):
        print(f"  [{idx}] CLI reported error: {outer.get('result', '')[:200]}")
        return idx, None

    raw = outer.get("result", "")
    inner = _extract_json(raw)
    if inner is None:
        print(f"  [{idx}] inner JSON parse error. Raw: {raw[:200]}")
        return idx, None

    return idx, inner


def _auto_row(row, is_replication, reasoning):
    """Build a classified row without an LLM call (Level 2 dedup or empty rows)."""
    out_row = dict(row)
    for field in CLASSIFICATION_FIELDS:
        out_row[field] = ""
    out_row["is_replication"] = str(is_replication)
    out_row["confidence"] = "high"
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

    parser = argparse.ArgumentParser(description="Classify replication study candidates via Claude CLI")
    parser.add_argument(
        "-w", "--workers", type=int, default=DEFAULT_WORKERS,
        help=f"Number of concurrent claude subprocess calls (default: {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Only classify the first N unclassified rows (useful for prompt testing)",
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

        doi = _norm_doi(row.get("doi", ""))
        pmid = (row.get("pmid") or "").strip()
        title = row.get("title", "")
        abstract = row.get("abstract", "")

        if not title and not abstract:
            classified_rows.append(_auto_row(row, False, "No title or abstract available"))
            already_done.add(idx)
            continue

        # Level 2: already ingested into the corpus — definitely a confirmed replication
        if doi and doi in ingested_dois:
            classified_rows.append(_auto_row(row, True, "Already ingested into corpus"))
            already_done.add(idx)
            skipped_ingested += 1
            continue

        # Level 1: classified in a prior run — reuse result without LLM call
        prior = prior_by_doi.get(doi) if doi else None
        if prior is None and pmid:
            prior = prior_by_pmid.get(pmid)
        if prior is not None:
            classified_rows.append(_reuse_prior_row(row, prior))
            already_done.add(idx)
            skipped_prior += 1
            continue

        work.append((idx, row))

    if args.limit:
        work = work[:args.limit]

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

    # Process in batches of concurrent subprocess calls; checkpoint after each batch
    for batch_start in range(0, len(work), batch_size):
        batch = work[batch_start:batch_start + batch_size]

        with ThreadPoolExecutor(max_workers=batch_size) as executor:
            futures = {}
            for idx, row in batch:
                title = row.get("title", "")
                abstract = row.get("abstract", "")
                future = executor.submit(classify_one, idx, title, abstract)
                futures[future] = (idx, row)

            for future in as_completed(futures):
                idx, row = futures[future]
                result_idx, result = future.result()

                if result is None:
                    print(f"  [{idx}] FAILED — will retry on next run")
                    continue

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
