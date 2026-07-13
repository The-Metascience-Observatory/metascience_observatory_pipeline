"""
Build a manifest of DOIs already in the corpus.

Writes data/processed_manifest.csv (columns: doi, ingested_path). Stage 4
(classify) reads it for Level-2 dedup — any DOI already processed is marked a
replication without an LLM call.

Two sources, in preference order:
  1. corpus.sqlite catalog (post-reorg) — one query over the flat papers/ corpus.
  2. legacy INGESTED_ROOT scan (pre-reorg) — walk ingested/<batch>/<doi folders>.

The catalog path is used automatically once the drive has been reorganized
(see mo_pipeline.corpus). Until then this falls back to the legacy scan, so
behavior is unchanged during the transition.

Usage:
    python -m mo_pipeline.discover.build_processed_manifest
"""

import csv
import re
import sys

from mo_pipeline.config import DATA_DIR, INGESTED_ROOT, PROCESSED_MANIFEST_CSV, CATALOG_PATH


DOI_FOLDER_RE = re.compile(r"^10\.\d+--")


def _rows_from_catalog():
    """(doi, folder) for every catalog paper, or None if no catalog yet."""
    if not CATALOG_PATH.exists():
        return None
    from mo_pipeline.corpus import catalog
    conn = catalog.connect()
    try:
        rows = [(r["doi"], r["folder"]) for r in
                conn.execute("SELECT doi, folder FROM papers ORDER BY doi")]
    finally:
        conn.close()
    return rows


def _rows_from_legacy_scan():
    """(doi, folder) for every DOI-named folder under INGESTED_ROOT/<batch>/."""
    if not INGESTED_ROOT.exists():
        print(f"INGESTED_ROOT does not exist: {INGESTED_ROOT}", file=sys.stderr)
        sys.exit(1)
    seen, rows, total, skipped = set(), [], 0, 0
    for batch_dir in sorted(INGESTED_ROOT.iterdir()):
        if not batch_dir.is_dir():
            continue
        for paper_dir in batch_dir.iterdir():
            if not paper_dir.is_dir():
                continue
            total += 1
            if not DOI_FOLDER_RE.match(paper_dir.name):
                skipped += 1
                continue
            doi = paper_dir.name.replace("--", "/")
            if doi.lower() in seen:
                continue
            seen.add(doi.lower())
            rows.append((doi, str(paper_dir)))
    print(f"Scanned {total} folders under {INGESTED_ROOT} (skipped {skipped} non-DOI)")
    return rows


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    rows = _rows_from_catalog()
    source = "catalog"
    if rows is None:
        rows = _rows_from_legacy_scan()
        source = "legacy scan"
    rows.sort(key=lambda r: r[0].lower())
    with open(PROCESSED_MANIFEST_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["doi", "ingested_path"])
        writer.writerows(rows)
    print(f"Wrote {len(rows)} DOIs to {PROCESSED_MANIFEST_CSV} (source: {source})")


if __name__ == "__main__":
    main()
