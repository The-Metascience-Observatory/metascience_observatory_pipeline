"""
One-time reorganization of the PDF drive into the flat `papers/` corpus.

Two phases, each a separate command so the plan can be reviewed before anything
moves:

  inventory  — READ-ONLY. Walk every legacy location, classify each entry,
               resolve duplicate DOIs to a winner, and write migration_plan.csv.
               Nothing on disk changes.
  migrate    — Execute migration_plan.csv: MOVE each folder to its destination
               (never copy — the drive is 99% full), append every move to
               migration_log.csv, and stamp a paper.json passport. Idempotent:
               re-running skips moves whose source is already gone.

Legacy layout consumed:
  ingested/<batch>/10.*--*/      -> papers/         (source_batch = <batch>)
  ingested/NO_REPLICATIONS/10.*  -> papers/         (verdict already in folder)
  <MEDIA>/8th_batch/10.*--*/     -> papers/         (converted WIP folders)
  <MEDIA>/8th_batch/*.pdf        -> inbox/          (loose, not yet converted)
  <MEDIA>/questionable/, acm_AIS_replication_research/ -> special/  (untouched)
  ingested/*.csv (collated_results*) -> legacy/     (stray outputs)

Duplicate DOIs (~1,372, mostly first_round/second_round overlap): the winner is
the folder with the newest extraction (highest ai_version, then most result
entries, then converted over raw). Loser folders go to legacy/ with their tag
subfolders preserved; any extraction tags the loser has that the winner lacks
are merged into the winner first (tags are namespaced, so no collision).
"""
from __future__ import annotations

import csv
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path

from mo_pipeline import config
from mo_pipeline.corpus.models import (
    scan_folder, folder_to_doi, is_doi_folder, write_passport, STATUS_ORDER,
)

csv.field_size_limit(sys.maxsize)

MEDIA = config.MEDIA_ROOT
PLAN_CSV = MEDIA / "migration_plan.csv"
LOG_CSV = MEDIA / "migration_log.csv"

_SPECIAL_DIRS = ["questionable", "acm_AIS_replication_research"]
_PLAN_FIELDS = ["doi", "src", "dest_kind", "dest", "role", "status", "source_batch",
                "ai_version", "n_replications", "note"]


def _iter_doi_folders(parent: Path):
    """Yield immediate child folders of `parent` that look like DOI folders."""
    if not parent.exists():
        return
    with os.scandir(parent) as it:
        for e in it:
            if e.is_dir() and is_doi_folder(e.name):
                yield Path(e.path)


def inventory() -> list[dict]:
    """READ-ONLY walk of all legacy locations -> a migration plan (list of dicts)."""
    entries: list[dict] = []

    # 1. ingested/<batch>/<doi folders>  (and NO_REPLICATIONS)
    ingested = config.INGESTED_ROOT
    if ingested.exists():
        for batch in sorted(p for p in ingested.iterdir() if p.is_dir()):
            for folder in _iter_doi_folders(batch):
                p = scan_folder(folder)
                entries.append(_entry(p, folder, source_batch=batch.name))
        # stray collated CSVs at the ingested root -> legacy
        for stray in sorted(ingested.glob("*.csv")):
            entries.append({"doi": "", "src": str(stray), "dest_kind": "legacy",
                            "dest": str(config.LEGACY_DIR / stray.name), "role": "stray-csv",
                            "status": "", "source_batch": "ingested", "ai_version": "",
                            "n_replications": "", "note": "stray collated_results at ingested root"})

    # 2. WIP batch dir at MEDIA top level (converted folders + loose PDFs)
    wip = config.CURRENT_BATCH_DIR
    if wip.exists():
        for folder in _iter_doi_folders(wip):
            p = scan_folder(folder)
            entries.append(_entry(p, folder, source_batch=wip.name))
        with os.scandir(wip) as it:
            for e in it:
                if e.is_file() and e.name.lower().endswith(".pdf"):
                    entries.append({"doi": folder_to_doi(Path(e.name).stem), "src": e.path,
                                    "dest_kind": "inbox", "dest": str(config.INBOX_DIR / e.name),
                                    "role": "loose-pdf", "status": "downloaded",
                                    "source_batch": wip.name, "ai_version": "",
                                    "n_replications": "", "note": "loose PDF -> inbox"})

    # 3. special corpora -> special/ (moved whole, untouched)
    for name in _SPECIAL_DIRS:
        d = MEDIA / name
        if d.exists():
            entries.append({"doi": "", "src": str(d), "dest_kind": "special",
                            "dest": str(config.SPECIAL_DIR / name), "role": "special-corpus",
                            "status": "", "source_batch": name, "ai_version": "",
                            "n_replications": "", "note": "pre-pipeline corpus, moved untouched"})

    # 4. resolve duplicate DOIs among the paper folders
    _resolve_duplicates(entries)
    return entries


def _legacy_dest(folder: Path, source_batch: str) -> str:
    """Batch-prefixed legacy path so same-DOI losers never collide."""
    return str(config.LEGACY_DIR / f"{source_batch}__{folder.name}")


def _entry(p, folder: Path, source_batch: str) -> dict:
    # Content-less husks (an aborted extraction leaves an empty tag subfolder,
    # no PDF/markdown/results) go to legacy rather than polluting papers/.
    if p.status == "empty":
        return {
            "doi": p.doi, "src": str(folder), "dest_kind": "legacy",
            "dest": _legacy_dest(folder, source_batch), "role": "empty-husk",
            "status": p.status, "source_batch": source_batch, "ai_version": "",
            "n_replications": 0, "note": "empty folder (no pdf/markdown/results)",
        }
    return {
        "doi": p.doi, "src": str(folder), "dest_kind": "papers",
        "dest": str(config.PAPERS_DIR / folder.name.split(" (")[0]),
        "role": "paper", "status": p.status, "source_batch": source_batch,
        "ai_version": p.ai_version or "", "n_replications": p.n_replications, "note": "",
    }


def _rank(e: dict) -> tuple:
    """Winner ranking for duplicate DOIs: newest version, most results, most processed."""
    ver = tuple(int(x) for x in (e.get("ai_version") or "").replace(".", " ").split() if x.isdigit())
    n = int(e.get("n_replications") or 0)
    status_rank = STATUS_ORDER.index(e["status"]) if e.get("status") in STATUS_ORDER else 0
    return (ver, n, status_rank)


def _resolve_duplicates(entries: list[dict]) -> None:
    papers = [e for e in entries if e["role"] == "paper"]
    by_doi: dict[str, list[dict]] = defaultdict(list)
    for e in papers:
        by_doi[e["doi"]].append(e)
    for doi, group in by_doi.items():
        if len(group) == 1:
            continue
        winner = max(group, key=_rank)
        for e in group:
            if e is winner:
                e["note"] = f"winner of {len(group)} dup folders"
            else:
                e["role"] = "dup-loser"
                e["dest_kind"] = "legacy"
                # Batch-prefixed so two losers of the same DOI never collide.
                e["dest"] = _legacy_dest(Path(e["src"]), e["source_batch"])
                e["note"] = f"dup loser (winner: {winner['src']})"


def write_plan(entries: list[dict], path: Path = PLAN_CSV) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_PLAN_FIELDS)
        w.writeheader()
        for e in entries:
            w.writerow({k: e.get(k, "") for k in _PLAN_FIELDS})
    return path


def plan_summary(entries: list[dict]) -> dict:
    roles = defaultdict(int)
    dests = defaultdict(int)
    for e in entries:
        roles[e["role"]] += 1
        dests[e["dest_kind"]] += 1
    return {"total_entries": len(entries), "by_role": dict(roles), "by_dest": dict(dests)}


def sweep_leftovers(execute: bool = False, log_path: Path = LOG_CSV) -> dict:
    """Move everything still under ingested/ and the WIP batch into legacy/.

    Run AFTER migrate(): the paper folders with clean DOI names are already in
    papers/. What remains is cruft and edge cases — dangling symlinks (targets
    already moved), stray collated_results CSVs, conversion scratch dirs
    (markdown_output*), and a tail of folders with MALFORMED DOI names (missing
    '--', underscores, typo'd '110.' prefixes). These are preserved in legacy/
    (batch-prefixed, never overwritten) for manual review, not deleted. Most are
    duplicates of papers whose clean-named winner already migrated.
    """
    config.LEGACY_DIR.mkdir(parents=True, exist_ok=True) if execute else None
    sources = []
    if config.INGESTED_ROOT.exists():
        for batch in sorted(p for p in config.INGESTED_ROOT.iterdir() if p.is_dir()):
            for entry in batch.iterdir():
                sources.append((batch.name, entry))
        for stray in config.INGESTED_ROOT.glob("*"):
            if stray.is_file():
                sources.append(("ingested_root", stray))
    if config.CURRENT_BATCH_DIR.exists():
        for entry in config.CURRENT_BATCH_DIR.iterdir():
            sources.append((config.CURRENT_BATCH_DIR.name, entry))

    moved = skipped = failed = 0
    log_rows = []
    for batch, src in sources:
        leaf = f"{batch}__{src.name}"
        # ext4 caps a filename at 255 bytes; some report .html leaves blow past
        # it once batch-prefixed. Truncate, appending a short hash for uniqueness.
        if len(leaf.encode()) > 240:
            import hashlib
            h = hashlib.sha1(leaf.encode()).hexdigest()[:8]
            leaf = leaf.encode()[:220].decode("utf-8", "ignore") + f"__{h}"
        dest = config.LEGACY_DIR / leaf
        try:
            if dest.exists():
                skipped += 1
                continue
            if not execute:
                moved += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            # os.rename over shutil.move: keep dangling symlinks as links.
            try:
                os.rename(str(src), str(dest))
            except OSError:
                shutil.move(str(src), str(dest))
            log_rows.append({"doi": "", "src": str(src), "dest": str(dest), "role": "leftover-sweep"})
            moved += 1
        except OSError as e:
            print(f"  sweep skip (error): {src} -> {e}", file=sys.stderr)
            failed += 1

    if execute and log_rows:
        new = not log_path.exists()
        with open(log_path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["doi", "src", "dest", "role"])
            if new:
                w.writeheader()
            w.writerows(log_rows)
    return {"leftover_sources": len(sources), "moved" if execute else "would_move": moved,
            "skipped": skipped, "failed": failed, "executed": execute}


def migrate(plan_path: Path = PLAN_CSV, execute: bool = False, log_path: Path = LOG_CSV) -> dict:
    """Execute the plan. execute=False is a dry-run (prints, moves nothing)."""
    if not plan_path.exists():
        raise FileNotFoundError(f"no plan at {plan_path}; run inventory first")
    with open(plan_path, newline="") as f:
        rows = list(csv.DictReader(f))

    for d in (config.PAPERS_DIR, config.INBOX_DIR, config.SPECIAL_DIR, config.LEGACY_DIR):
        if execute:
            d.mkdir(parents=True, exist_ok=True)

    moved = skipped = missing = 0
    log_rows = []
    for r in rows:
        src = Path(r["src"])
        dest = Path(r["dest"])
        if not src.exists():
            # Idempotent: already moved (or dest exists) -> skip.
            if dest.exists():
                skipped += 1
            else:
                missing += 1
            continue
        if dest.exists():
            skipped += 1
            continue
        if not execute:
            moved += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        if r["role"] in ("paper",) and r["source_batch"]:
            try:
                write_passport(dest, source_batch=r["source_batch"])
            except Exception:
                pass
        log_rows.append({"doi": r.get("doi", ""), "src": str(src), "dest": str(dest),
                         "role": r["role"]})
        moved += 1

    if execute and log_rows:
        new = not log_path.exists()
        with open(log_path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["doi", "src", "dest", "role"])
            if new:
                w.writeheader()
            w.writerows(log_rows)

    return {"planned": len(rows), "moved" if execute else "would_move": moved,
            "skipped": skipped, "missing_src": missing, "executed": execute}
