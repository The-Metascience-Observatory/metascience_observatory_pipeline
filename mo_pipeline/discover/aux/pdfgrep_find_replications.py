#!/usr/bin/env python3
"""
Phase 4: Use pdfgrep to scan all available PDFs for replication-specific
language, then filter out already-ingested papers.

Copies matches to a batch folder for ingestion.
"""

import subprocess
import shutil
from pathlib import Path
from collections import defaultdict

# ── Configuration ────────────────────────────────────────────────────────────

INGESTED_DIR = Path("/media/dan/500Gb/metascience_observatory_pdfs/ingested")
OUTPUT_DIR = Path("/media/dan/500Gb/metascience_observatory_pdfs/pdfgrep_batch")

# Directories to search (excluding have_been_ingested itself)
SEARCH_DIRS = [
    Path("/home/dan/Dropbox/AAA_METASCIENCE_OBSERVATORY/PDFs"),
    Path("/home/dan/downloaded_pdfs"),
    Path("/home/dan/Dropbox/AAA_METASCIENCE_OBSERVATORY/pull_replication_studies/downloaded_pdfs"),
    Path("/home/dan/Dropbox/AAA_METASCIENCE_OBSERVATORY/pull_long_covid_papers/pdfs"),
    Path("/home/dan/Dropbox/AAA_METASCIENCE_OBSERVATORY/agent_for_replications/ground_truth_dataset_PDFs"),
]

# Search terms ordered by specificity (most specific first)
SEARCH_TERMS = [
    "direct replication of",
    "close replication",
    "exact replication",
    "replication attempt",
    "failed to replicate",
    "failure to replicate",
    "we replicated",
    "registered replication report",
    "conceptual replication of",
    "attempted to replicate",
    "replicate the findings",
    "reproduce the findings",
]


def build_ingested_set():
    """Build set of stems already ingested."""
    stems = set()
    for pdf in INGESTED_DIR.rglob("*.pdf"):
        stems.add(pdf.stem.lower())
    return stems


def build_already_batched_set():
    """Build set of stems already in output dir."""
    stems = set()
    if OUTPUT_DIR.exists():
        for pdf in OUTPUT_DIR.glob("*.pdf"):
            stems.add(pdf.stem.lower())
    # Also check 7th_batch
    batch7 = Path("/media/dan/500Gb/metascience_observatory_pdfs/7th_batch")
    if batch7.exists():
        for pdf in batch7.glob("*.pdf"):
            stems.add(pdf.stem.lower())
    return stems


def pdfgrep_search(directory, term):
    """Run pdfgrep on a directory, return list of matching PDF paths."""
    if not directory.exists():
        return []

    try:
        result = subprocess.run(
            ["pdfgrep", "-i", "-l", "-r", term, str(directory)],
            capture_output=True, text=True, timeout=300
        )
        paths = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
        return [Path(p) for p in paths if p.endswith(".pdf")]
    except (subprocess.TimeoutExpired, Exception) as e:
        print(f"  ⚠️ Error searching {directory}: {e}")
        return []


def main():
    print("Building ingested + already-batched sets...")
    ingested = build_ingested_set()
    batched = build_already_batched_set()
    skip_stems = ingested | batched
    print(f"  {len(ingested)} ingested, {len(batched)} already batched")
    print(f"  Total to skip: {len(skip_stems)}")

    # Track all matches: stem -> (path, set of matching terms)
    matches = {}  # stem -> (path, terms)

    for term in SEARCH_TERMS:
        print(f"\nSearching: \"{term}\"")
        term_hits = 0
        for d in SEARCH_DIRS:
            if not d.exists():
                continue
            hits = pdfgrep_search(d, term)
            for pdf_path in hits:
                stem = pdf_path.stem.lower()
                if stem in skip_stems:
                    continue
                if stem not in matches:
                    matches[stem] = (pdf_path, set())
                matches[stem][1].add(term)
                term_hits += 1
        print(f"  → {term_hits} new (not-ingested) matches")

    print(f"\n{'='*60}")
    print(f"Total unique PDFs found (not ingested): {len(matches)}")

    if not matches:
        print("No new PDFs to copy.")
        return

    # Show breakdown by number of matching terms
    by_term_count = defaultdict(list)
    for stem, (path, terms) in matches.items():
        by_term_count[len(terms)].append(stem)

    print("\nMatches by number of search terms hit:")
    for count in sorted(by_term_count.keys(), reverse=True):
        print(f"  {count} terms: {len(by_term_count[count])} PDFs")

    # Copy to output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    copied = 0
    failed = 0
    for stem, (src_path, terms) in matches.items():
        dest = OUTPUT_DIR / src_path.name
        if dest.exists():
            continue
        try:
            shutil.copy2(src_path, dest)
            copied += 1
        except OSError:
            # Try sanitized name
            safe_name = src_path.name.replace(":", "_")
            dest = OUTPUT_DIR / safe_name
            try:
                shutil.copy2(src_path, dest)
                copied += 1
            except OSError as e:
                print(f"  ⚠️ Could not copy {src_path.name}: {e}")
                failed += 1

    print(f"\n✅ Copied {copied} new PDFs to {OUTPUT_DIR}")
    if failed:
        print(f"   ⚠️ {failed} failed to copy")


if __name__ == "__main__":
    main()
