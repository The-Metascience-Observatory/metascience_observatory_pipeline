"""
Phase 1: Collect confirmed-replication PDFs that are already downloaded
but have NOT been ingested yet.

Cross-references:
  - confirmed_replications.csv (DOIs of all confirmed replications)
  - have_been_ingested/ (already processed PDFs)
  - PDF_SEARCH_DIRS (all directories where PDFs might exist)

Copies matching PDFs to a new batch folder for ingestion.
"""

import csv
import shutil
import sys
from pathlib import Path

csv.field_size_limit(sys.maxsize)

# ── Configuration ────────────────────────────────────────────────────────────

CONFIRMED_CSV = Path("data/confirmed_replications.csv")
INGESTED_DIR = Path("/media/dan/500Gb/metascience_observatory_pdfs/ingested")
OUTPUT_DIR = Path("/media/dan/500Gb/metascience_observatory_pdfs/7th_batch")

PDF_SEARCH_DIRS = [
    Path("/home/dan/downloaded_pdfs"),
    Path("/home/dan/Dropbox/AAA_METASCIENCE_OBSERVATORY/PDFs"),
    Path("/home/dan/Dropbox/AAA_METASCIENCE_OBSERVATORY/pull_replication_studies/downloaded_pdfs"),
    Path("/home/dan/Dropbox/AAA_METASCIENCE_OBSERVATORY/pull_replication_studies/manually_classified_PDFs"),
    Path("/home/dan/Dropbox/AAA_METASCIENCE_OBSERVATORY/agent_for_replications/ground_truth_dataset_PDFs"),
    Path("/home/dan/Dropbox/AAA_METASCIENCE_OBSERVATORY/pull_long_covid_papers/pdfs"),
]


def main():
    # 1. Build set of ingested DOI stems
    print("Scanning ingested PDFs...")
    ingested_stems = set()
    for pdf in INGESTED_DIR.rglob("*.pdf"):
        ingested_stems.add(pdf.stem.lower())
    print(f"  Found {len(ingested_stems)} ingested PDF stems")

    # 2. Build index of all available PDFs (stem -> path)
    print("Scanning PDF directories...")
    available = {}  # stem -> first path found
    for d in PDF_SEARCH_DIRS:
        if not d.exists():
            continue
        for pdf in d.rglob("*.pdf"):
            stem = pdf.stem.lower()
            if stem not in available:
                available[stem] = pdf
    print(f"  Found {len(available)} available PDFs across all directories")

    # 3. Read confirmed replications
    print("Reading confirmed_replications.csv...")
    with open(CONFIRMED_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        confirmed = list(reader)
    print(f"  {len(confirmed)} confirmed replications")

    # 4. Find papers that are: confirmed + have PDF + not ingested
    to_copy = []
    for row in confirmed:
        doi = row.get("doi", "").strip().lower()
        if not doi:
            continue
        stem = doi.replace("/", "--")
        if stem in ingested_stems:
            continue
        if stem in available:
            rtype = row.get("replication_type", "unknown")
            to_copy.append((stem, available[stem], rtype, doi))

    print(f"\n  Ready to copy: {len(to_copy)} PDFs (confirmed + downloaded + not ingested)")

    if not to_copy:
        print("Nothing to copy.")
        return

    # Show breakdown by type
    from collections import Counter
    types = Counter(t[2] for t in to_copy)
    for rtype, count in types.most_common():
        print(f"    {rtype}: {count}")

    # 5. Copy to output directory (use source filename to avoid invalid chars)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    copied = 0
    for stem, src_path, rtype, doi in to_copy:
        # Use the source file's name since it's already on a filesystem
        dest = OUTPUT_DIR / src_path.name
        if dest.exists():
            continue
        try:
            shutil.copy2(src_path, dest)
            copied += 1
        except OSError as e:
            print(f"  ⚠️ Could not copy {src_path.name}: {e}")

    print(f"\n✅ Copied {copied} PDFs to {OUTPUT_DIR}")
    print(f"   Skipped {len(to_copy) - copied} already present")


if __name__ == "__main__":
    main()
