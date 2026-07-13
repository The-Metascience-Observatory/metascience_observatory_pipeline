#!/usr/bin/env python3
"""
Phase 6: Citation mining — find new replication studies by looking at papers
that cite the same original studies as known replications.

Strategy:
  1. Extract original_study DOIs from collated_results (known replications)
  2. For each original DOI, find all papers that cite it (via OpenAlex)
  3. Filter citing papers through keyword heuristics
  4. Output candidates for LLM classification

This catches replications that don't use standard "replication" terminology
in their title/abstract.

Usage:
  python citation_mine_replications.py                # all originals
  python citation_mine_replications.py --limit 50     # first 50 originals
"""

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

import requests

csv.field_size_limit(sys.maxsize)

# ── Configuration ────────────────────────────────────────────────────────────

COLLATED_RESULTS = Path("/media/dan/500Gb/metascience_observatory_pdfs/have_been_ingested/collated_results_direct_replications_sonnet_02_07_2026.csv")
OUTPUT_CSV = Path("data/citation_mined_candidates.csv")
PROGRESS_FILE = Path("progress/citation_mine_progress.json")

OPENALEX_DELAY = 0.15  # seconds between API calls

# Keywords that suggest a citing paper might be a replication
REPLICATION_KEYWORDS = [
    r"\breplicat",
    r"\breproducib",
    r"\breproduc\w+\s+(the|original|prior|previous)",
    r"\bfailed?\s+to\s+(replicat|reproduc)",
    r"\breplication\s+(study|attempt|effort|failure|crisis)",
    r"\bdirect\s+replicat",
    r"\bclose\s+replicat",
    r"\bconceptual\s+replicat",
    r"\bregistered\s+replicat",
    r"\bmulti.?site\s+replicat",
    r"\boriginal\s+(study|finding|result|experiment)",
]

FIELDNAMES = [
    "pmid", "doi", "title", "abstract", "authors",
    "journal", "year", "source_api", "source_query",
    "original_doi", "match_count",
]


def load_progress():
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"completed_originals": [], "seen_dois": []}


def save_progress(progress):
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2))


def extract_original_dois():
    """Extract unique original study DOIs from collated results."""
    dois = set()
    with open(COLLATED_RESULTS, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = row.get("original_url", "")
            if "doi.org/" in url:
                doi = url.split("doi.org/", 1)[1].strip().lower()
                if doi:
                    dois.add(doi)
    return sorted(dois)


def get_citing_works(doi, max_results=200):
    """Find papers that cite a given DOI using OpenAlex."""
    base_url = "https://api.openalex.org/works"
    params = {
        "filter": f"cites:{doi}",
        "per_page": 200,
        "cursor": "*",
    }

    results = []
    while True:
        try:
            r = requests.get(base_url, params=params, timeout=30)
        except requests.exceptions.Timeout:
            break
        if r.status_code != 200:
            break
        data = r.json()
        works = data.get("results", [])
        if not works:
            break

        for w in works:
            work_doi = (w.get("doi") or "").replace("https://doi.org/", "")
            abstract = _reconstruct_abstract(w.get("abstract_inverted_index"))
            authors = "; ".join(
                a.get("author", {}).get("display_name", "")
                for a in w.get("authorships", [])
            )
            results.append({
                "doi": work_doi,
                "title": w.get("title", ""),
                "abstract": abstract,
                "authors": authors,
                "journal": ((w.get("primary_location") or {}).get("source") or {}).get("display_name", ""),
                "year": str(w.get("publication_year", "")),
                "pmid": "",
            })

        if len(results) >= max_results:
            break
        next_cursor = data.get("meta", {}).get("next_cursor")
        if not next_cursor:
            break
        params["cursor"] = next_cursor
        time.sleep(OPENALEX_DELAY)

    return results


def _reconstruct_abstract(inverted_index):
    if not inverted_index:
        return ""
    word_positions = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort()
    return " ".join(w for _, w in word_positions)


def has_replication_signal(title, abstract):
    """Check if title or abstract contains replication-related keywords."""
    text = f"{title} {abstract}".lower()
    matches = 0
    for pattern in REPLICATION_KEYWORDS:
        if re.search(pattern, text):
            matches += 1
    return matches


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="Max number of original DOIs to process")
    args = parser.parse_args()

    progress = load_progress()
    seen_dois = set(progress.get("seen_dois", []))
    completed = set(progress.get("completed_originals", []))

    # Load existing candidates from CSV if resuming
    existing_rows = []
    if OUTPUT_CSV.exists():
        with open(OUTPUT_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            existing_rows = list(reader)
            for row in existing_rows:
                doi = row.get("doi", "").strip().lower()
                if doi:
                    seen_dois.add(doi)
        print(f"Loaded {len(existing_rows)} existing candidates, {len(seen_dois)} seen DOIs")

    # Also load already-known DOIs from confirmed_replications
    confirmed_csv = Path("data/confirmed_replications.csv")
    if confirmed_csv.exists():
        with open(confirmed_csv, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                doi = row.get("doi", "").strip().lower()
                if doi:
                    seen_dois.add(doi)
        print(f"Total seen DOIs (including confirmed): {len(seen_dois)}")

    # Get original study DOIs
    original_dois = extract_original_dois()
    print(f"\nFound {len(original_dois)} unique original study DOIs")

    remaining = [d for d in original_dois if d not in completed]
    if args.limit:
        remaining = remaining[:args.limit]
    print(f"Processing {len(remaining)} original DOIs ({len(completed)} already done)")

    new_candidates = []
    for i, orig_doi in enumerate(remaining, 1):
        print(f"\n[{i}/{len(remaining)}] Citing papers for: {orig_doi}")

        citing = get_citing_works(orig_doi)
        print(f"  Found {len(citing)} citing papers")

        hits = 0
        for paper in citing:
            doi = paper.get("doi", "").strip().lower()
            if not doi or doi in seen_dois:
                continue

            match_count = has_replication_signal(paper["title"], paper["abstract"])
            if match_count > 0:
                paper["source_api"] = "citation_mining"
                paper["source_query"] = f"cites:{orig_doi}"
                paper["original_doi"] = orig_doi
                paper["match_count"] = match_count
                new_candidates.append(paper)
                seen_dois.add(doi)
                hits += 1

        print(f"  → {hits} new candidates with replication signals")

        completed.add(orig_doi)
        progress["completed_originals"] = list(completed)
        progress["seen_dois"] = list(seen_dois)

        # Checkpoint every 10 originals
        if i % 10 == 0:
            save_progress(progress)
            _write_csv(existing_rows + new_candidates)
            print(f"  [checkpoint] {len(new_candidates)} new candidates so far")

        time.sleep(OPENALEX_DELAY)

    # Final save
    save_progress(progress)
    all_rows = existing_rows + new_candidates
    _write_csv(all_rows)

    print(f"\n{'='*60}")
    print(f"Citation mining complete.")
    print(f"  Original DOIs processed: {len(completed)}")
    print(f"  New candidates found: {len(new_candidates)}")
    print(f"  Total candidates in CSV: {len(all_rows)}")
    print(f"  Output: {OUTPUT_CSV}")


def _write_csv(rows):
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in FIELDNAMES})


if __name__ == "__main__":
    main()
