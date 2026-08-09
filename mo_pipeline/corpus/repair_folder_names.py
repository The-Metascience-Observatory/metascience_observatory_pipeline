"""One-time repair of corpus folder names that predate the reversible DOI codec.

Three legacy naming schemes coexisted in papers/ before models.doi_to_folder
was the single source of truth:
  1. ':' encoded as '--' (colliding with '/'), e.g. 10.1023--a--1018769825030
     for 10.1023/a:1018769825030 — folder_to_doi silently mis-decodes to '/'.
  2. '_XX_' hex tokens (':' -> '_3a_', '<' -> '_3c_', ';' -> '_3b_') that the
     shared decoder never handled.
  3. Trailing unicode whitespace in a couple of folder names.

This module renames those folders to the current encoding. The true DOI comes
from the folder's doi.txt when present; otherwise the naive decode is checked
against a normalized index of every DOI the pipeline knows about (data/*.csv) —
'10.1023--a--123' and '10.1023/a:123' share a normalized key, so the known
colon DOI wins over the naive slash decode.

    python -m mo_pipeline.corpus repair-names           # dry-run: list renames
    python -m mo_pipeline.corpus repair-names --apply   # perform them
    python -m mo_pipeline.corpus repair-names --check   # invariant: doi.txt
                                                        # round-trips to folder

Run `python -m mo_pipeline.corpus scan` afterwards to rebuild the catalog.
"""
from __future__ import annotations

import bisect
import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path

from mo_pipeline import config
from mo_pipeline.corpus.models import doi_to_folder, folder_to_doi

REPAIR_LOG = config.MEDIA_ROOT / "repair_log.csv"

# Every pipeline CSV that carries authoritative DOIs (any column with 'doi'
# in its name, plus replication/original URL columns holding doi.org links).
_KNOWN_DOI_CSVS = ("confirmed_replications.csv", "classified.csv",
                   "candidates_dedup.csv", "download_status.csv",
                   "direct_replications.csv")

_DOI_URL_RE = re.compile(r"^https?://(?:dx\.)?doi\.org/", re.I)


def _norm(doi: str) -> str:
    """Collapse a DOI to a punctuation-blind key so all historical encodings of
    the same DOI ('/', ':', '--', '_3a_'…) land on the same index entry."""
    return re.sub(r"[^0-9a-z]", "", doi.lower())


def known_doi_index() -> dict[str, str]:
    """normalized key -> DOI, from every data CSV. Keys claimed by two
    *different* DOIs are dropped as ambiguous (never used for renames)."""
    index: dict[str, str] = {}
    ambiguous: set[str] = set()
    for name in _KNOWN_DOI_CSVS:
        path = config.DATA_DIR / name
        if not path.exists():
            continue
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            cols = [c for c in (reader.fieldnames or [])
                    if c and ("doi" in c.lower()
                              or c in ("replication_url", "original_url", "url"))]
            for row in reader:
                for col in cols:
                    doi = _DOI_URL_RE.sub("", (row.get(col) or "").strip())
                    if not doi.startswith("10."):
                        continue
                    doi = doi.lower()  # corpus folder names are lowercase
                    key = _norm(doi)
                    prior = index.get(key)
                    if prior is not None and prior.lower() != doi.lower():
                        ambiguous.add(key)
                    else:
                        index.setdefault(key, doi)
    for key in ambiguous:
        index.pop(key, None)
    return index


def _looks_suspicious(clean: str, key: str, sorted_keys: list[str]) -> bool:
    """A folder with no doi.txt and no index match that still smells like a
    legacy or truncated encoding: stray '.pdf' suffix, SICI leftovers, the
    Kluwer ':'-as-'--' shape, or a name that is a strict prefix of a known DOI
    (truncated at a forbidden char by some old downloader)."""
    if clean.lower().endswith(".pdf") or "(sici)" in clean.lower():
        return True
    if re.match(r"^10\.1023--[ab]--", clean):
        return True
    if not clean.startswith("10."):
        return True  # papers/ should only hold DOI-named folders
    if len(key) >= 15:
        i = bisect.bisect_left(sorted_keys, key)
        if i < len(sorted_keys) and sorted_keys[i] != key and sorted_keys[i].startswith(key):
            return True
    return False


@dataclass
class Repair:
    old: str
    new: str
    doi: str
    source: str          # doi.txt | csv-index | whitespace


def plan_repairs(papers_dir: Path | None = None):
    """Returns (renames, unresolved, conflicts). Read-only."""
    papers_dir = papers_dir or config.PAPERS_DIR
    index = known_doi_index()
    sorted_keys = sorted(index)
    renames: list[Repair] = []
    unresolved: list[str] = []
    conflicts: list[Repair] = []
    names = {e.name for e in os.scandir(papers_dir) if e.is_dir()}

    for name in sorted(names):
        clean = re.sub(r"\s+$", "", name)
        true_doi, source = None, None

        doi_txt = papers_dir / name / "doi.txt"
        if doi_txt.exists():
            text = doi_txt.read_text(encoding="utf-8", errors="replace").strip()
            if text.startswith("10."):
                true_doi, source = text, "doi.txt"

        if true_doi is None:
            stripped = re.sub(r"\.pdf$", "", clean, flags=re.I)
            # Folders named from a full DOI URL, e.g. https_3a_----doi.org--10.1037--x
            stripped = re.sub(r"^https?(?:_3a_|~)----(?:dx\.)?doi\.org--", "", stripped)
            decoded = folder_to_doi(stripped)
            key = _norm(decoded)
            known = index.get(key)
            if known is not None and known.lower() != decoded.lower():
                true_doi, source = known, "csv-index"   # e.g. ':' stored as '--'
            elif known is not None and stripped != clean:
                true_doi, source = decoded, "stripped"  # known DOI + stray '.pdf'/URL prefix
            elif clean != name:
                true_doi, source = decoded, "whitespace"
            elif known is None and _looks_suspicious(clean, key, sorted_keys):
                # Truncated / legacy-encoded names we can't confidently map —
                # report for manual review, never guess.
                unresolved.append(name)
                continue

        if true_doi is None:
            continue  # nothing suggests the name is wrong

        target = doi_to_folder(true_doi)
        if target.lower() == clean.lower():
            target = clean  # avoid pure case churn; keep on-disk casing
        if target == name:
            continue
        repair = Repair(old=name, new=target, doi=true_doi, source=source)
        if target in names:
            # Target already exists on disk, or two legacy variants of the same
            # DOI map to the same target — duplicate folders to merge by hand.
            conflicts.append(repair)
        else:
            names.add(target)  # claim it so a second variant becomes a conflict
            renames.append(repair)
    return renames, unresolved, conflicts


def repair(apply: bool = False, papers_dir: Path | None = None) -> dict:
    papers_dir = papers_dir or config.PAPERS_DIR
    renames, unresolved, conflicts = plan_repairs(papers_dir)

    for r in renames:
        print(f"{'RENAME' if apply else 'would rename'}  {r.old!r}\n"
              f"        -> {r.new!r}   [{r.source}: {r.doi}]")
    for r in conflicts:
        print(f"CONFLICT (target exists, skipped)  {r.old!r} -> {r.new!r}")
    for name in unresolved:
        print(f"UNRESOLVED (manual review)  {name!r}")

    if apply and renames:
        write_header = not REPAIR_LOG.exists()
        with open(REPAIR_LOG, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(["old", "new", "doi", "source"])
            for r in renames:
                if (papers_dir / r.new).exists():
                    print(f"SKIP (target appeared on disk)  {r.old!r} -> {r.new!r}")
                    continue
                os.rename(papers_dir / r.old, papers_dir / r.new)
                doi_txt = papers_dir / r.new / "doi.txt"
                if not doi_txt.exists():
                    doi_txt.write_text(r.doi + "\n", encoding="utf-8")
                writer.writerow([r.old, r.new, r.doi, r.source])
        print(f"\nRenamed {len(renames)} folders; log appended to {REPAIR_LOG}")
        print("Now rebuild the catalog: python -m mo_pipeline.corpus scan")
    return {"renames": len(renames), "conflicts": len(conflicts),
            "unresolved": len(unresolved), "applied": bool(apply and renames)}


def check(papers_dir: Path | None = None) -> int:
    """Invariant: every folder with a doi.txt must equal doi_to_folder(doi)."""
    papers_dir = papers_dir or config.PAPERS_DIR
    bad = 0
    for entry in sorted(os.scandir(papers_dir), key=lambda e: e.name):
        if not entry.is_dir():
            continue
        doi_txt = Path(entry.path) / "doi.txt"
        if not doi_txt.exists():
            continue
        doi = doi_txt.read_text(encoding="utf-8", errors="replace").strip()
        if not doi.startswith("10."):
            continue
        target = doi_to_folder(doi)
        if target != entry.name and target.lower() != entry.name.lower():
            print(f"MISMATCH  {entry.name!r}  doi.txt={doi!r}  expected={target!r}")
            bad += 1
    print(f"{'OK — all doi.txt folders round-trip' if not bad else f'{bad} mismatches'}")
    return bad
