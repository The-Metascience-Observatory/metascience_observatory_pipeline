"""Shared helpers for the claim-centrality labeling pilot.

The pilot tests whether claude CLI labeling of claim centrality (is a
database row's claim a central claim of its original paper, or a secondary
finding?) agrees with human judgment. See prompts/prompt_centrality.md for
the rubric; outputs live under DATA_DIR/label_centrality/.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from pathlib import Path

from mo_pipeline import config

OUT_DIR = config.DATA_DIR / "label_centrality"
MANIFEST_PATH = OUT_DIR / "pilot_manifest.json"
ROWS_CSV_PATH = OUT_DIR / "pilot_rows_UNBLINDED_do_not_show_labelers.csv"
ABSTRACTS_PATH = OUT_DIR / "abstracts_cache.json"
LABELS_CSV_PATH = OUT_DIR / "centrality_labels_pilot.csv"
VALIDATION_SHEET_PATH = OUT_DIR / "validation_sheet.csv"

# The only keys allowed anywhere in the blinded manifest. Everything a labeler
# sees is built from the manifest, so this whitelist IS the blinding guarantee.
MANIFEST_ALLOWED_KEYS = {
    "csv_version", "sampled_at_note", "units",
    "replication_doi", "folder",
    "original_doi", "original_title", "original_journal", "original_year",
    "rows", "row_id", "claim_description",
}


def latest_csv_path() -> Path:
    lines = config.VERSION_HISTORY_PATH.read_text().strip().split("\n")
    lines = [ln for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    return config.WEBSITE_DATA_DIR / lines[-1].split("#")[0].strip()


def load_db_rows() -> tuple[str, list[dict]]:
    """Return (csv filename, rows with a stable row_id added)."""
    path = latest_csv_path()
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    for i, r in enumerate(rows):
        key = "\x1f".join([r["original_url"], r["replication_url"], r["description"]])
        digest = hashlib.sha1(key.encode()).hexdigest()[:10]
        r["row_id"] = f"r{i:05d}-{digest}"
    return path.name, rows


def normalize_doi(url_or_doi: str) -> str | None:
    """'https://doi.org/10.X/Y' or '10.X/Y' -> lowercased bare DOI."""
    s = (url_or_doi or "").strip()
    m = re.search(r"(?:doi\.org/)?(10\.\d{4,9}/\S+)", s, re.I)
    return m.group(1).lower().rstrip("/") if m else None


def load_catalog_folders() -> dict[str, Path]:
    """Lowercased DOI -> corpus folder path, from corpus.sqlite (never walk papers/)."""
    con = sqlite3.connect(config.CATALOG_PATH)
    try:
        out = {}
        for doi, folder in con.execute("SELECT doi, folder FROM papers"):
            out[doi.lower()] = Path(folder)
        return out
    finally:
        con.close()


def assert_manifest_blinded(manifest: dict) -> None:
    """Fail loudly if the manifest contains any non-whitelisted key."""
    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k not in MANIFEST_ALLOWED_KEYS:
                    raise AssertionError(f"blinding violation: unexpected key {k!r} in manifest")
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(manifest)


def load_manifest() -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text())
    assert_manifest_blinded(manifest)
    return manifest
