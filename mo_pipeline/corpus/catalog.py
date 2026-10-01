"""
corpus.sqlite — a rebuildable catalog of the paper corpus.

The catalog is an INDEX, never the source of truth: `scan()` rebuilds it from
the on-disk paper folders (reading each folder's status + findings via
`models.scan_folder`). Stages update rows incrementally as they process papers;
`scan()` reconciles from scratch. After a scan, a flat CSV snapshot is exported
into the Dropbox-backed repo so the corpus stays visible even when the corpus
drive is unmounted.
"""
from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

from mo_pipeline import config
from mo_pipeline.corpus.models import Paper, scan_folder, is_doi_folder

csv.field_size_limit(sys.maxsize)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    doi                 TEXT PRIMARY KEY,
    folder              TEXT NOT NULL,
    status              TEXT NOT NULL,
    contains_replications INTEGER,          -- 1/0/NULL
    n_replications      INTEGER DEFAULT 0,
    latest_tag          TEXT,
    ai_version          TEXT,
    source_batch        TEXT,
    screened_confidence TEXT,
    ingested_db_version TEXT,
    tags                TEXT,
    has_pdf             INTEGER DEFAULT 0,
    has_structured      INTEGER DEFAULT 0,
    has_rendition       INTEGER DEFAULT 0,
    updated_at          TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_status ON papers(status);
CREATE INDEX IF NOT EXISTS idx_contains ON papers(contains_replications);
CREATE INDEX IF NOT EXISTS idx_source_batch ON papers(source_batch);
"""

_COLUMNS = ["doi", "folder", "status", "contains_replications", "n_replications",
            "latest_tag", "ai_version", "source_batch", "screened_confidence",
            "ingested_db_version", "tags", "has_pdf", "has_structured",
            "has_rendition"]

#: Columns added after the first schema shipped. CREATE TABLE IF NOT EXISTS is a
#: no-op on an existing corpus.sqlite, so a new column has to be ALTERed in or
#: every write fails with "no such column". Values are backfilled by the next
#: `corpus scan`; the catalog is an index, never truth (see CLAUDE.md).
_ADDED_COLUMNS = (
    ("has_structured", "INTEGER DEFAULT 0"),
    ("has_rendition", "INTEGER DEFAULT 0"),
)

SNAPSHOT_CSV = config.DATA_DIR / "corpus_snapshot.csv"


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or config.CATALOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    # A long scan-write contends with the dashboard's /corpus reads; wait for
    # the lock instead of failing with "database is locked".
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_SCHEMA)
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(papers)")}
    for name, decl in _ADDED_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE papers ADD COLUMN {name} {decl}")
    conn.commit()
    return conn


def _to_row(p: Paper) -> tuple:
    cr = None if p.contains_replications is None else int(bool(p.contains_replications))
    return (p.doi, p.folder, p.status, cr, p.n_replications, p.latest_tag,
            p.ai_version, p.source_batch, p.screened_confidence,
            p.ingested_db_version, ",".join(p.tags), int(p.has_pdf),
            int(p.has_structured), int(p.has_rendition))


def upsert(conn: sqlite3.Connection, p: Paper) -> None:
    placeholders = ",".join("?" * len(_COLUMNS))
    updates = ",".join(f"{c}=excluded.{c}" for c in _COLUMNS if c != "doi")
    conn.execute(
        f"INSERT INTO papers ({','.join(_COLUMNS)}, updated_at) "
        f"VALUES ({placeholders}, datetime('now')) "
        f"ON CONFLICT(doi) DO UPDATE SET {updates}, updated_at=datetime('now')",
        _to_row(p),
    )


def scan(conn: sqlite3.Connection | None = None, papers_dir: Path | None = None,
         verbose: bool = True) -> dict:
    """Rebuild the catalog from disk. Returns summary counts."""
    own = conn is None
    conn = conn or connect()
    papers_dir = papers_dir or config.PAPERS_DIR
    if not papers_dir.exists():
        if verbose:
            print(f"papers dir not found: {papers_dir}", file=sys.stderr)
        return {"scanned": 0}

    n = 0
    seen: set[str] = set()
    folders = [d for d in papers_dir.iterdir() if d.is_dir() and is_doi_folder(d.name)]
    for d in folders:
        p = scan_folder(d)
        upsert(conn, p)
        seen.add(p.doi)
        n += 1
        # Commit in chunks so the write lock is released between batches — lets
        # the dashboard's /corpus reads interleave instead of blocking for minutes.
        if n % 500 == 0:
            conn.commit()
            if verbose:
                print(f"  scanned {n}/{len(folders)}...", flush=True)
    # Drop rows for folders that no longer exist OR whose DOI key changed (e.g.
    # after a decode fix) so no stale/orphan rows linger. Deleting by NOT-IN a
    # temp table avoids a giant parameter list.
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _seen (doi TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM _seen")
    conn.executemany("INSERT OR IGNORE INTO _seen VALUES (?)", [(d,) for d in seen])
    removed = conn.execute("DELETE FROM papers WHERE doi NOT IN (SELECT doi FROM _seen)").rowcount
    conn.commit()
    if verbose and removed:
        print(f"  removed {removed} stale rows", flush=True)
    export_snapshot(conn)
    summary = stats(conn)
    summary["scanned"] = n
    if own:
        conn.close()
    return summary


def stats(conn: sqlite3.Connection) -> dict:
    def one(sql, *args):
        return conn.execute(sql, args).fetchone()[0]
    by_status = {r["status"]: r["c"] for r in conn.execute(
        "SELECT status, COUNT(*) c FROM papers GROUP BY status")}
    return {
        "total": one("SELECT COUNT(*) FROM papers"),
        "by_status": by_status,
        "with_replications": one("SELECT COUNT(*) FROM papers WHERE contains_replications=1"),
        "without_replications": one("SELECT COUNT(*) FROM papers WHERE contains_replications=0"),
        "total_replication_entries": one("SELECT COALESCE(SUM(n_replications),0) FROM papers"),
        "ingested": one("SELECT COUNT(*) FROM papers WHERE ingested_db_version IS NOT NULL"),
        # Extraction's tier ladder: a rendition is the primary full text, so the
        # gap between these two is the backlog for `corpus render-markdown`.
        "with_structured": one("SELECT COUNT(*) FROM papers WHERE has_structured=1"),
        "with_rendition": one("SELECT COUNT(*) FROM papers WHERE has_rendition=1"),
    }


def export_snapshot(conn: sqlite3.Connection, path: Path | None = None) -> Path:
    path = path or SNAPSHOT_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(f"SELECT {','.join(_COLUMNS)} FROM papers ORDER BY doi").fetchall()
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_COLUMNS)
        for r in rows:
            w.writerow([r[c] for c in _COLUMNS])
    return path


def query(conn: sqlite3.Connection, *, status: str | None = None,
          contains_replications: bool | None = None, source_batch: str | None = None,
          not_extracted_tag: str | None = None, limit: int | None = None) -> list[sqlite3.Row]:
    """Flexible filter used by the manifest shim, include-list generator, and API."""
    clauses, args = [], []
    if status:
        clauses.append("status = ?"); args.append(status)
    if contains_replications is not None:
        clauses.append("contains_replications = ?"); args.append(int(contains_replications))
    if source_batch:
        clauses.append("source_batch = ?"); args.append(source_batch)
    if not_extracted_tag:
        # tags is a comma-joined list; exclude papers whose tag set contains it.
        clauses.append("(tags IS NULL OR (',' || tags || ',') NOT LIKE ?)")
        args.append(f"%,{not_extracted_tag},%")
    sql = "SELECT * FROM papers"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY doi"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, args).fetchall()


def coverage_report() -> dict:
    """Print how many published-database replication DOIs have a corpus folder."""
    from mo_pipeline.shared.production_db import published_dois
    db, path = published_dois()
    if path is None:
        raise FileNotFoundError(f"no replications database named in {config.VERSION_HISTORY_PATH}")
    conn = connect()
    try:
        have = {r[0].lower() for r in conn.execute("SELECT doi FROM papers")}
    finally:
        conn.close()
    matched = db & have
    pct = 100 * len(matched) / len(db) if db else 0
    print(f"Database→corpus coverage ({path.name}): {len(matched)}/{len(db)} = {pct:.1f}%")
    print(f"  missing from the corpus: {len(db) - len(matched)}")
    return {"matched": len(matched), "missing": len(db) - len(matched),
            "coverage_pct": round(pct, 1)}


def _normalize_doi(url_or_doi: str) -> str:
    """'https://doi.org/10.x/y' or '10.x/y' -> '10.x/y' (lowercased)."""
    s = (url_or_doi or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    return s


def mark_ingested(conn: sqlite3.Connection, dois, db_version: str,
                  write_passport: bool = True) -> dict:
    """Stamp `status='ingested'` + db_version on matching catalog rows, and
    (optionally) write the ingested record into each paper's paper.json.

    Returns {'matched': n, 'unmatched': [dois...]}. Called after a manual ingest with the
    replication_url DOIs from the collated CSV that was ingested."""
    from mo_pipeline.corpus.models import write_passport as _wp
    matched, unmatched = 0, []
    for raw in dois:
        doi = _normalize_doi(raw)
        row = conn.execute(
            "SELECT doi, folder FROM papers WHERE lower(doi)=?", (doi,)).fetchone()
        if row is None:
            unmatched.append(raw)
            continue
        conn.execute(
            "UPDATE papers SET status='ingested', ingested_db_version=?, "
            "updated_at=datetime('now') WHERE doi=?", (db_version, row["doi"]))
        if write_passport:
            try:
                _wp(Path(row["folder"]), ingested={"db_version": db_version})
            except Exception:
                pass
        matched += 1
    conn.commit()
    export_snapshot(conn)
    return {"matched": matched, "unmatched": unmatched}

