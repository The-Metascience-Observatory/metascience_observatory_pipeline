"""Move flat inbox artifacts into one folder per record: inbox/{stem}/{stem}.*

Stage 5 writes each record into its own folder (fetchpdf's --make-subfolder),
named exactly as its future papers/{stem}/ folder. Records downloaded before that
sit flat at the inbox root, and a subfolder run cannot see them: fetchpdf fills
its goals from `{output_dir}/{stem}/{stem}.xml` and friends, so a flat
`{stem}.pdf` looks like a missing PDF and would be fetched again. This one-time
pass moves every flat record into place; leftovers are listed, never guessed at.

Dry-run unless `--execute`, matching `adopt-structured`.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from fetchpdf.retrieval.tiers import ARTIFACT_EXTENSIONS

from mo_pipeline import config
from mo_pipeline.corpus.models import doi_to_folder, folder_to_doi, is_doi_folder

#: Per-run files fetchpdf writes at the output root. They belong to no record.
RUN_LEVEL = {
    "failed_dois.csv", "missing_pdfs.html", "source_tracking.csv",
    "source_counts.json", ".fetchpdf_resolution.json",
}

#: Sidecars written next to a record, keyed by exact suffix, plus the numbered
#: families. Kept in step with corpus.adopt and fetchpdf.retrieval.*.
SIDECAR_SUFFIXES = (
    "_from_xml.md", "_from_html.md",
    ".provenance.json", ".linked_artifacts.json", "_linked_artifacts.json",
    "_abstract.md", "_supplementary_info.json", "_images.json",
)
SIDECAR_PREFIXES = ("_supplementary_info_", "_images")

#: Longest first, so "{stem}.fulltext.html" strips to "{stem}", not "{stem}.fulltext".
_SUFFIXES = tuple(sorted(tuple(ARTIFACT_EXTENSIONS) + SIDECAR_SUFFIXES,
                         key=len, reverse=True))


def _canonical(stem: str) -> bool:
    """A stem that is a DOI folder name AND round-trips through the codec.

    `is_doi_folder` only checks the prefix, so "10.1037--xge0000263 " (trailing
    space, a real leftover) would pass it and create a folder with a space in
    its name. Requiring the round trip refuses that and every legacy encoding.
    """
    if not is_doi_folder(stem) or stem != stem.strip():
        return False
    try:
        return doi_to_folder(folder_to_doi(stem)) == stem
    except Exception:
        return False


def record_stem(name: str) -> str | None:
    """The record a top-level inbox entry belongs to, or None if it is not one."""
    lower = name.lower()
    for suffix in _SUFFIXES:
        if lower.endswith(suffix) and len(name) > len(suffix):
            stem = name[: -len(suffix)]
            return stem if _canonical(stem) else None
    for marker in SIDECAR_PREFIXES:
        idx = lower.find(marker)
        if idx > 0:
            stem = name[:idx]
            return stem if _canonical(stem) else None
    return None


def migrate_inbox(execute: bool = False, inbox: Path | None = None) -> dict:
    """Move each flat inbox file into inbox/{stem}/. Dry-run unless `execute`."""
    inbox = inbox or config.INBOX_DIR
    summary = {"records": 0, "files": 0, "run_level": 0, "already_in_folder": 0,
               "leftovers": [], "examples": []}
    if not inbox.is_dir():
        return summary

    by_stem: dict[str, list[Path]] = {}
    for entry in sorted(inbox.iterdir()):
        if entry.name in RUN_LEVEL:
            summary["run_level"] += 1
            continue
        stem = record_stem(entry.name)
        if stem is None:
            # A directory named exactly like a record IS a record folder --
            # already in place. `is_doi_folder` alone cannot say so: a
            # "{stem}_images/" dump shares the prefix, and record_stem is what
            # tells the two apart.
            if entry.is_dir() and _canonical(entry.name):
                continue
            summary["leftovers"].append(entry.name)
            continue
        by_stem.setdefault(stem, []).append(entry)

    for stem, files in sorted(by_stem.items()):
        target = inbox / stem
        summary["records"] += 1
        summary["files"] += len(files)
        if len(summary["examples"]) < 5:
            summary["examples"].append(f"{stem}: {[f.name for f in files]}")
        if not execute:
            continue
        target.mkdir(exist_ok=True)
        for f in files:
            dest = target / f.name
            if dest.exists():
                # Never overwrite: a subfolder run may already have refetched
                # this half. Counted so the flat copy stays visible.
                summary["already_in_folder"] += 1
                summary["files"] -= 1
                continue
            shutil.move(str(f), str(dest))
    return summary
