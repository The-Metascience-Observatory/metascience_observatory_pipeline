"""
Build a manifest of DOIs already in the corpus.

Writes data/processed_manifest.csv (columns: doi, ingested_path,
contains_replications). Stage 4 (classify) reads it for Level-2 dedup: a DOI
whose corpus extraction found replications (1) is auto-confirmed, one whose
extraction found none (0) is auto-rejected, and anything not yet extracted
(blank) is classified normally. Until 2026-10-01 every paper on the drive was
auto-confirmed, which put 2,282 known non-replications into the confirmed set.

The production database (config.latest_replications_db(), the newest ingested
replications_database_*.csv) is a second source: every replication_url DOI in it
is a published replication paper, verdict 1, whether or not its folder is on the
drive and whatever a stale extraction says.

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
from mo_pipeline.corpus.models import folder_to_doi


DOI_FOLDER_RE = re.compile(r"^10\.\d+--")


def _rows_from_catalog():
    """(doi, folder, contains_replications) for every catalog paper, or None if no catalog yet."""
    if not CATALOG_PATH.exists():
        return None
    from mo_pipeline.corpus import catalog
    conn = catalog.connect()
    try:
        rows = [(r["doi"], r["folder"], "" if r["contains_replications"] is None
                 else str(int(r["contains_replications"]))) for r in
                conn.execute("SELECT doi, folder, contains_replications FROM papers ORDER BY doi")]
    finally:
        conn.close()
    return rows


def _dois_in_production_db():
    """Replication-paper DOIs in the newest ingested database CSV (empty if none)."""
    from mo_pipeline.shared.production_db import published_dois
    dois, path = published_dois()
    if path is None:
        print("WARNING: no production replications database found; using the catalog only",
              file=sys.stderr)
    return dois, path


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
            doi = folder_to_doi(paper_dir.name)
            if doi.lower() in seen:
                continue
            seen.add(doi.lower())
            rows.append((doi, str(paper_dir), ""))  # legacy layout: verdict unknown
    print(f"Scanned {total} folders under {INGESTED_ROOT} (skipped {skipped} non-DOI)")
    return rows


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    rows = _rows_from_catalog()
    source = "catalog"
    if rows is None:
        rows = _rows_from_legacy_scan()
        source = "legacy scan"
    db_dois, db_path = _dois_in_production_db()
    by_doi = {r[0].lower(): list(r) for r in rows}
    for doi in db_dois:
        if doi in by_doi:
            by_doi[doi][2] = "1"
        else:
            by_doi[doi] = [doi, "", "1"]
    rows = sorted((tuple(r) for r in by_doi.values()), key=lambda r: r[0].lower())
    if db_path:
        print(f"Production DB {db_path.name}: {len(db_dois)} replication DOIs marked 1")
    with open(PROCESSED_MANIFEST_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["doi", "ingested_path", "contains_replications"])
        writer.writerows(rows)
    n_yes = sum(1 for r in rows if r[2] == "1")
    n_no = sum(1 for r in rows if r[2] == "0")
    print(f"Wrote {len(rows)} DOIs to {PROCESSED_MANIFEST_CSV} (source: {source}): "
          f"{n_yes} with replications, {n_no} without, {len(rows) - n_yes - n_no} not yet extracted")


if __name__ == "__main__":
    main()
