#!/usr/bin/env python3
"""
Phase 3: Download PDFs for all confirmed replications that are:
  (a) not already ingested
  (b) not already downloaded anywhere

Uses the specialized `fetch_pdf_from_doi` package (installed at
/home/dan/Dropbox/AAA_METASCIENCE_OBSERVATORY/fetch_pdf_from_doi) which
supports 18 download strategies (OSF, SSRN, Figshare, PsychArchives, PMC,
Unpaywall, Crossref, EuropePMC, Semantic Scholar, OpenAlex, CORE, Sci-Hub
via Playwright, ResearchGate, etc.) with parallel batch processing.

Downloads to a batch folder, prioritized by replication type:
  direct > close > conceptual > systematic > multi-site > other

Usage:
  python download_all_confirmed.py                            # all types
  python download_all_confirmed.py --type direct              # direct only
  python download_all_confirmed.py --type direct,close        # multiple types
  python download_all_confirmed.py --limit 100                # first 100
  python download_all_confirmed.py --workers 4                # 4 parallel workers
"""

import argparse
import csv
import os
import sys
from collections import Counter
from pathlib import Path

csv.field_size_limit(sys.maxsize)

from fetch_pdf_from_doi import batch_fetch_pdfs

from mo_pipeline.config import (
    CONFIRMED_REPLICATIONS_CSV as CONFIRMED_CSV,
    INGESTED_ROOT as INGESTED_DIR,
    CURRENT_BATCH_DIR as OUTPUT_DIR,
    PDF_SEARCH_DIRS as _BASE_PDF_SEARCH_DIRS,
)

# ── Configuration ────────────────────────────────────────────────────────────

# Include our own output dir so --resume-style restarts skip already-downloaded
# files. (INGESTED_DIR is scanned separately below.)
PDF_SEARCH_DIRS = list(_BASE_PDF_SEARCH_DIRS) + [OUTPUT_DIR]

TYPE_PRIORITY = ["direct", "close", "conceptual", "systematic", "multi-site"]


def _scan_pdfs_into(root, stems):
    """Recursively walk `root` with os.scandir and add lowercase PDF stems.

    os.scandir is significantly faster than pathlib.Path.rglob because it
    avoids Path object construction and reuses cached stat info from the
    DirEntry objects. Uses an explicit stack to avoid recursion overhead.

    Emits a heartbeat line every ~2 seconds so large scans (e.g. 4000+
    ingested folders on a slow external drive) don't look hung.
    """
    import time as _time
    stack = [str(root)]
    files_seen = 0
    start = _time.time()
    last_heartbeat = start
    while stack:
        current = stack.pop()
        try:
            it = os.scandir(current)
        except (PermissionError, FileNotFoundError, OSError):
            continue
        with it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        files_seen += 1
                        name = entry.name
                        if len(name) > 4 and name[-4:].lower() == ".pdf":
                            # stem = filename without ".pdf" extension, lowercased
                            stems.add(name[:-4].lower())
                except (PermissionError, FileNotFoundError, OSError):
                    continue
        now = _time.time()
        if now - last_heartbeat > 2.0:
            print(
                f"    ... {files_seen:,} files scanned, "
                f"{len(stems):,} PDFs found "
                f"({now - start:.0f}s, {len(stack)} dirs queued)",
                flush=True,
            )
            last_heartbeat = now


def build_existing_stems():
    """Build set of all DOI stems that exist somewhere (ingested or downloaded)."""
    import time as _time
    stems = set()

    t0 = _time.time()
    print(f"  scanning {INGESTED_DIR}...", flush=True)
    if INGESTED_DIR.exists():
        _scan_pdfs_into(INGESTED_DIR, stems)
    print(f"    ✓ {len(stems)} total after ingested ({_time.time()-t0:.1f}s)", flush=True)

    for d in PDF_SEARCH_DIRS:
        if not d.exists():
            print(f"  skipping {d} (does not exist)", flush=True)
            continue
        t0 = _time.time()
        before = len(stems)
        print(f"  scanning {d}...", flush=True)
        _scan_pdfs_into(d, stems)
        print(f"    ✓ +{len(stems)-before} new (total {len(stems)}, {_time.time()-t0:.1f}s)", flush=True)

    return stems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--type",
        help="Filter by replication type. Single value or comma-separated list "
             "(e.g. 'direct', 'direct,close', 'direct,close,conceptual')",
    )
    parser.add_argument("--limit", type=int, help="Max number of DOIs to attempt")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers (default: 4)")
    parser.add_argument("--delay", type=float, default=0.2, help="Delay per worker (default: 0.2s)")
    parser.add_argument("--legalonly", action="store_true", help="Only use legal download sources (no Sci-Hub)")
    args = parser.parse_args()

    type_filter = None
    if args.type:
        type_filter = {t.strip() for t in args.type.split(",") if t.strip()}
        print(f"Filtering to replication types: {sorted(type_filter)}")

    print("Building index of existing PDFs...")
    existing = build_existing_stems()
    print(f"  {len(existing)} PDFs already exist (ingested + downloaded)")

    # Read confirmed replications
    with open(CONFIRMED_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        confirmed = list(reader)

    # Filter to papers that need downloading
    to_download = []
    for row in confirmed:
        doi = row.get("doi", "").strip().lower()
        if not doi:
            continue
        stem = doi.replace("/", "--")
        if stem in existing:
            continue
        rtype = row.get("replication_type", "other")
        if type_filter and rtype not in type_filter:
            continue
        to_download.append((doi, rtype, row.get("title", "")[:80]))

    # Sort by type priority
    def sort_key(item):
        try:
            return TYPE_PRIORITY.index(item[1])
        except ValueError:
            return len(TYPE_PRIORITY)

    to_download.sort(key=sort_key)

    if args.limit:
        to_download = to_download[:args.limit]

    print(f"\nDOIs to download: {len(to_download)}")
    types = Counter(t[1] for t in to_download)
    for rtype, count in types.most_common():
        print(f"  {rtype}: {count}")

    if not to_download:
        print("Nothing to download.")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Extract just the DOIs for batch_fetch_pdfs
    dois = [doi for doi, rtype, title in to_download]

    print(f"\n🚀 Starting batch download with {args.workers} parallel workers...")
    print(f"   Output: {OUTPUT_DIR}")
    print(f"   Delay per worker: {args.delay}s")
    if args.legalonly:
        print(f"   Legal sources only (--legalonly)")
    print()

    # Use the specialized package's batch_fetch_pdfs with parallel workers
    results = batch_fetch_pdfs(
        dois=dois,
        output_dir=str(OUTPUT_DIR),
        verbose=True,
        workers=args.workers,
        delay=args.delay,
        create_missing_report=True,
        legalonly=args.legalonly,
    )

    # Summary
    successes = sum(1 for _, success, _ in results if success)
    failures = len(results) - successes

    print(f"\n{'='*60}")
    print(f"Results: {successes} succeeded, {failures} failed")
    print(f"Success rate: {successes/len(results)*100:.1f}%")
    print(f"Output: {OUTPUT_DIR}")
    if failures:
        print(f"Missing PDFs report: {OUTPUT_DIR}/missing_pdfs.html")


if __name__ == "__main__":
    main()
