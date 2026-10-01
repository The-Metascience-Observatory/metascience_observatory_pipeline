"""Corpus zip downloads: the whole papers/ tree or a paper-type subset.

Paper types are multi-label per paper, merged from two sources:
  1. the latest website production DB (replications_database_*.csv) — each
     replication entry's `replication_type` (the extraction taxonomy: direct,
     close experiment, close extension, conceptual, plus legacy labels),
     keyed by the entry's replication_url DOI;
  2. fallback: the stage-4 classifier's `replication_type` in
     confirmed_replications.csv for papers not in the production DB.
Corpus papers with no type anywhere (not yet extracted/ingested) land in the
'unclassified' bucket. The zip is streamed folder-by-folder (Zip64; PDFs
stored, text deflated) so nothing is staged on the nearly-full corpus drive.
Zip entries keep the papers/<doi folder>/... structure.
"""
from __future__ import annotations

import csv
import zipfile
from pathlib import Path

from mo_pipeline import config
from mo_pipeline.corpus.models import normalize_doi

UNCLASSIFIED = "unclassified"


# Already-compressed formats are stored; everything else (md/json/xml/…) deflates.
_STORE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".gz"}


def _norm_doi(doi: str) -> str:
    return normalize_doi(doi) or ""


def type_map() -> dict[str, set[str]]:
    """DOI (lowercased) -> set of replication types (multi-label)."""
    mapping: dict[str, set[str]] = {}

    db = config.latest_replications_db()
    if db is not None:
        with open(db, newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                doi = _norm_doi(row.get("replication_url") or "")
                rtype = (row.get("replication_type") or "").strip().lower()
                if doi and rtype:
                    mapping.setdefault(doi, set()).add(rtype)

    path = config.CONFIRMED_REPLICATIONS_CSV
    if path.exists():
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                doi = _norm_doi(row.get("doi") or "")
                rtype = (row.get("replication_type") or "").strip().lower()
                if doi and rtype and doi not in mapping:
                    mapping[doi] = {rtype}
    return mapping


def _catalog_rows() -> list[tuple[str, str]]:
    """(doi, folder path) for every paper in the catalog."""
    from mo_pipeline.corpus import catalog
    conn = catalog.connect()
    try:
        return [(r["doi"], r["folder"])
                for r in conn.execute("SELECT doi, folder FROM papers")]
    finally:
        conn.close()


def options() -> list[dict]:
    """Paper types with corpus counts, largest first (for the checkbox list).

    Counts are per-type paper counts; a multi-type paper is counted under each
    of its types, so the counts sum to more than the corpus total."""
    tmap = type_map()
    counts: dict[str, int] = {}
    for doi, _folder in _catalog_rows():
        for t in tmap.get(_norm_doi(doi), {UNCLASSIFIED}):
            counts[t] = counts.get(t, 0) + 1
    return [{"type": t, "count": n}
            for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def folders_for(types: set[str] | None) -> list[Path]:
    """Folder paths to include; None means the entire corpus. A paper matches
    if any of its types is selected."""
    rows = _catalog_rows()
    if types is None:
        return [Path(f) for _doi, f in rows]
    tmap = type_map()
    return [Path(f) for doi, f in rows
            if tmap.get(_norm_doi(doi), {UNCLASSIFIED}) & types]


class _StreamBuffer:
    """Unseekable write sink for ZipFile; drained between entries to stream."""

    def __init__(self):
        self._chunks: list[bytes] = []
        self._pos = 0

    def write(self, data) -> int:
        self._chunks.append(bytes(data))
        self._pos += len(data)
        return len(data)

    def tell(self) -> int:
        return self._pos

    def seekable(self) -> bool:
        return False

    def flush(self) -> None:
        pass

    def drain(self) -> bytes:
        out = b"".join(self._chunks)
        self._chunks.clear()
        return out


def zip_stream(folders: list[Path]):
    """Yield zip bytes for the given paper folders, one file at a time.

    Writing to an unseekable sink makes ZipFile use data descriptors, so the
    archive can stream without ever existing on disk. Peak memory is one
    member file (a few MB of PDF).
    """
    buf = _StreamBuffer()
    zf = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, allowZip64=True)
    try:
        for folder in folders:
            if not folder.is_dir():
                continue  # catalog momentarily ahead of the drive; skip, don't 500
            for path in sorted(p for p in folder.rglob("*") if p.is_file()):
                compress = (zipfile.ZIP_STORED
                            if path.suffix.lower() in _STORE_SUFFIXES
                            else zipfile.ZIP_DEFLATED)
                arcname = f"papers/{folder.name}/{path.relative_to(folder)}"
                zf.write(path, arcname=arcname, compress_type=compress)
                chunk = buf.drain()
                if chunk:
                    yield chunk
    finally:
        zf.close()  # central directory (also runs on client disconnect)
    yield buf.drain()
