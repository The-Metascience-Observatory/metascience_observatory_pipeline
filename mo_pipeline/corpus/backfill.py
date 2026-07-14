"""
One-time ad-hoc backfill of fulltext markdown for replications-database papers
that have no folder in the corpus.

Not part of the recurring pipeline — this reconciles the historical database
(built over many batches) against the reorganized `papers/` corpus and pulls +
converts the papers whose markdown was never retained.

Steps (each resumable, run in order):

    python -m mo_pipeline.corpus.backfill compute    # -> missing DOI lists
    python -m mo_pipeline.corpus.backfill fetch       # fetch_pdf_from_doi -> backfill_pull/
    python -m mo_pipeline.corpus.backfill convert     # pdf4llm -> papers/
    python -m mo_pipeline.corpus.backfill finalize    # rescan + coverage delta

`coverage` (also exposed as `python -m mo_pipeline.corpus coverage`) prints the
current database→corpus markdown coverage without changing anything.
"""
from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
from pathlib import Path

from mo_pipeline import config
from mo_pipeline.corpus import catalog
from mo_pipeline.corpus.models import doi_to_folder

csv.field_size_limit(2**31 - 1)

PULL_DIR = config.MEDIA_ROOT / "backfill_pull"
MISSING_DOIS_FILE = config.DATA_DIR / "backfill_missing_dois.txt"
NON_DOI_FILE = config.DATA_DIR / "backfill_non_doi.csv"
MIN_FREE_GB = 3.0     # abort fetch below this free space on the drive


# ── coverage computation (reused by the `coverage` command) ──────────────────
def latest_db_path() -> Path:
    vh = config.VERSION_HISTORY_PATH
    latest = None
    if vh.exists():
        for line in vh.read_text().splitlines():
            line = line.split("#")[0].strip()
            if line:
                latest = line
    if not latest:
        raise FileNotFoundError(f"no DB entry in {vh}")
    return config.WEBSITE_DATA_DIR / latest


def db_replication_dois(db_path: Path) -> set[str]:
    with open(db_path, newline="") as f:
        return {catalog._normalize_doi(r.get("replication_url", ""))
                for r in csv.DictReader(f)} - {""}


def compute_missing(conn=None):
    """Return (matched, pullable, non_doi) DOI sets vs the current catalog."""
    own = conn is None
    conn = conn or catalog.connect()
    try:
        cat = {r[0].lower() for r in conn.execute("SELECT doi FROM papers")}
    finally:
        if own:
            conn.close()
    db = {d.lower() for d in db_replication_dois(latest_db_path())}
    matched = db & cat
    missing = db - cat
    pullable = sorted(d for d in missing if d.startswith("10."))
    non_doi = sorted(d for d in missing if not d.startswith("10."))
    return matched, pullable, non_doi


def coverage_report() -> dict:
    matched, pullable, non_doi = compute_missing()
    total = len(matched) + len(pullable) + len(non_doi)
    pct = 100 * len(matched) / total if total else 0
    print(f"Database→corpus markdown coverage: {len(matched)}/{total} = {pct:.1f}%")
    print(f"  have markdown : {len(matched)}")
    print(f"  missing (DOI, pullable)    : {len(pullable)}")
    print(f"  missing (non-DOI url)      : {len(non_doi)}")
    return {"matched": len(matched), "pullable": len(pullable),
            "non_doi": len(non_doi), "coverage_pct": round(pct, 1)}


# ── steps ────────────────────────────────────────────────────────────────────
def cmd_compute(_args):
    matched, pullable, non_doi = compute_missing()
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    MISSING_DOIS_FILE.write_text("\n".join(pullable) + ("\n" if pullable else ""))
    with open(NON_DOI_FILE, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["value"]); w.writerows([[d] for d in non_doi])
    print(f"have markdown: {len(matched)} | pullable missing: {len(pullable)} "
          f"| non-DOI missing: {len(non_doi)}")
    print(f"wrote {MISSING_DOIS_FILE}")
    print(f"wrote {NON_DOI_FILE} (manual review — not pulled)")


def _already_pulled_stems() -> set[str]:
    if not PULL_DIR.exists():
        return set()
    return {p.stem.lower() for p in PULL_DIR.glob("*.pdf")}


def cmd_fetch(args):
    from fetch_pdf_from_doi import batch_fetch_pdfs
    if not MISSING_DOIS_FILE.exists():
        sys.exit("run `compute` first")
    dois = [d.strip() for d in MISSING_DOIS_FILE.read_text().splitlines() if d.strip()]
    # Resumable: skip DOIs whose PDF already exists in the pull dir.
    have = _already_pulled_stems()
    dois = [d for d in dois if doi_to_folder(d).lower() not in have]
    if args.limit:
        dois = dois[: args.limit]
    if not dois:
        print("nothing to fetch (all pullable DOIs already downloaded)")
        return
    free_gb = shutil.disk_usage(config.MEDIA_ROOT).free / 1e9
    print(f"drive free: {free_gb:.1f} GB | fetching {len(dois)} DOIs -> {PULL_DIR}")
    if free_gb < MIN_FREE_GB:
        sys.exit(f"aborting: only {free_gb:.1f} GB free (< {MIN_FREE_GB}) — free space first")
    PULL_DIR.mkdir(parents=True, exist_ok=True)
    results = batch_fetch_pdfs(
        dois, str(PULL_DIR), email=config.ENTREZ_EMAIL, workers=args.workers,
        delay=0.2, legalonly=args.legalonly, use_playwright=not args.legalonly,
        create_missing_report=True, verbose=False)
    ok = sum(1 for r in results if r[1])
    print(f"fetched {ok}/{len(dois)} PDFs "
          f"({shutil.disk_usage(config.MEDIA_ROOT).free/1e9:.1f} GB free now)")
    print(f"failures logged in {PULL_DIR}/failed_dois.csv")


def cmd_convert(args):
    if not PULL_DIR.exists() or not any(PULL_DIR.glob("*.pdf")):
        sys.exit("no PDFs in backfill_pull/ — run `fetch` first")
    workers = min(args.workers, 4)
    cmd = ["pdf4llm", "batch", str(PULL_DIR), "-o", str(config.PAPERS_DIR),
           "--mode", "full-grobid", "--workers", str(workers), "--movepdf", "--resume"]
    print("running:", " ".join(cmd))
    rc = subprocess.call(cmd)
    if rc != 0:
        sys.exit(f"pdf4llm exited {rc}")
    print("conversion done — PDFs moved into papers/{doi}/ with markdown")


def cmd_convert_xml(_args):
    """Convert leftover Elsevier fulltext XMLs in backfill_pull/ into papers/."""
    from mo_pipeline.corpus import xml_to_markdown
    xmls = list(PULL_DIR.glob("*.xml")) if PULL_DIR.exists() else []
    if not xmls:
        print("no *.xml in backfill_pull/ — nothing to convert")
        return
    print(f"converting {len(xmls)} Elsevier XML fulltexts -> {config.PAPERS_DIR}")
    result = xml_to_markdown.convert_dir(PULL_DIR, config.PAPERS_DIR)
    print(f"\nconverted {result['converted']}/{result['total']} "
          f"({result['thin']} thin-body); {len(result['failed'])} failed")
    if result["failed"]:
        print("  failed:", result["failed"][:10])


def cmd_finalize(_args):
    before = coverage_report()
    print("rescanning catalog...")
    catalog.scan(verbose=True)
    print("\n--- coverage after backfill ---")
    after = coverage_report()
    gained = after["matched"] - before["matched"]
    print(f"\nnet new papers with markdown: {gained}")
    print(f"still missing: {after['pullable']} pullable (fetch failures) "
          f"+ {after['non_doi']} non-DOI")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mo_pipeline.corpus.backfill")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("compute", help="write missing-DOI lists")
    f = sub.add_parser("fetch", help="download PDFs for pullable missing DOIs")
    f.add_argument("--limit", type=int, help="only first N (for a test slice)")
    f.add_argument("--workers", type=int, default=4)
    f.add_argument("--legalonly", action="store_true", help="skip Sci-Hub/AA")
    c = sub.add_parser("convert", help="pdf4llm convert backfill_pull -> papers/")
    c.add_argument("--workers", type=int, default=4)
    sub.add_parser("convert-xml", help="convert leftover Elsevier XML fulltexts -> papers/")
    sub.add_parser("finalize", help="rescan + coverage delta")
    sub.add_parser("coverage", help="print current coverage only")
    args = ap.parse_args(argv)
    {"compute": cmd_compute, "fetch": cmd_fetch, "convert": cmd_convert,
     "convert-xml": cmd_convert_xml, "finalize": cmd_finalize,
     "coverage": lambda a: coverage_report()}[args.cmd](args)


if __name__ == "__main__":
    main()
