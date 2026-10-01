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
import sqlite3
from pathlib import Path

from mo_pipeline import config

OUT_DIR = config.DATA_DIR / "label_centrality"


class Paths:
    """File paths for one labeling run. run='pilot' is the original 150-row
    stratified sample; run='enriched' is the all-rows-from-multi-finding-papers
    sample. The abstracts cache is shared across runs (same OpenAlex data)."""

    def __init__(self, run: str = "pilot"):
        self.run = run
        self.manifest = OUT_DIR / f"{run}_manifest.json"
        self.rows_csv = OUT_DIR / f"{run}_rows_UNBLINDED_do_not_show_labelers.csv"
        if run == "pilot":
            # preserve the filenames already delivered to Dan
            self.labels_csv = OUT_DIR / "centrality_labels_pilot.csv"
            self.validation_sheet = OUT_DIR / "validation_sheet.csv"
        else:
            self.labels_csv = OUT_DIR / f"{run}_centrality_labels.csv"
            self.validation_sheet = OUT_DIR / f"{run}_validation_sheet.csv"

    def load_manifest(self) -> dict:
        manifest = json.loads(self.manifest.read_text())
        assert_manifest_blinded(manifest)
        return manifest


ABSTRACTS_PATH = OUT_DIR / "abstracts_cache.json"

# Back-compat constants for the original pilot run.
MANIFEST_PATH = Paths("pilot").manifest
ROWS_CSV_PATH = Paths("pilot").rows_csv
LABELS_CSV_PATH = OUT_DIR / "centrality_labels_pilot.csv"  # original filename
VALIDATION_SHEET_PATH = OUT_DIR / "validation_sheet.csv"   # original filename

# The only keys allowed anywhere in the blinded manifest. Everything a labeler
# sees is built from the manifest, so this whitelist IS the blinding guarantee.
MANIFEST_ALLOWED_KEYS = {
    "csv_version", "sampled_at_note", "units",
    "replication_doi", "folder",
    "original_doi", "original_title", "original_journal", "original_year",
    "rows", "row_id", "claim_description",
}


def latest_csv_path() -> Path:
    path = config.latest_replications_db()
    if path is None:
        raise FileNotFoundError(f"no replications database named in {config.VERSION_HISTORY_PATH}")
    return path


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
