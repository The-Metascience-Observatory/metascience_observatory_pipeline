"""
Build a manifest of DOIs already ingested into the corpus.

Scans INGESTED_ROOT for DOI-named paper folders (convention: `/` → `--`)
and writes data/processed_manifest.csv. Used by download_pdfs.py to skip
the full fetch cascade for papers that already exist.

Usage:
    python build_processed_manifest.py

Re-run after new batches are moved into `ingested/`.
"""

import csv
import re
import sys

from config import DATA_DIR, INGESTED_ROOT, PROCESSED_MANIFEST_CSV


# Matches a DOI-named folder: "10.1016--j.biopsycho.2018.08.007"
DOI_FOLDER_RE = re.compile(r"^10\.\d+--")


def scan_ingested():
    """Yield (doi, path) for every DOI-named folder in INGESTED_ROOT/*/*/."""
    for batch_dir in sorted(INGESTED_ROOT.iterdir()):
        if not batch_dir.is_dir():
            continue
        for paper_dir in batch_dir.iterdir():
            if not paper_dir.is_dir():
                continue
            name = paper_dir.name
            if not DOI_FOLDER_RE.match(name):
                continue
            doi = name.replace("--", "/")
            yield doi, str(paper_dir)


def main():
    if not INGESTED_ROOT.exists():
        print(f"INGESTED_ROOT does not exist: {INGESTED_ROOT}", file=sys.stderr)
        sys.exit(1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    seen = set()
    rows = []
    total_folders = 0
    skipped_non_doi = 0

    for batch_dir in sorted(INGESTED_ROOT.iterdir()):
        if not batch_dir.is_dir():
            continue
        for paper_dir in batch_dir.iterdir():
            if not paper_dir.is_dir():
                continue
            total_folders += 1
            name = paper_dir.name
            if not DOI_FOLDER_RE.match(name):
                skipped_non_doi += 1
                continue
            doi = name.replace("--", "/")
            key = doi.lower()
            if key in seen:
                continue
            seen.add(key)
            rows.append((doi, str(paper_dir)))

    rows.sort(key=lambda r: r[0].lower())

    with open(PROCESSED_MANIFEST_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["doi", "ingested_path"])
        writer.writerows(rows)

    print(f"Scanned {total_folders} folders under {INGESTED_ROOT}")
    print(f"  DOI-named folders: {len(rows)}")
    print(f"  Skipped (non-DOI names): {skipped_non_doi}")
    print(f"Wrote {PROCESSED_MANIFEST_CSV}")


if __name__ == "__main__":
    main()
