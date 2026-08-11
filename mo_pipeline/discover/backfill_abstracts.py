"""
Backfill missing abstracts for screening candidates.

17% of deduped candidates (28,928 of 165,869) reach stage 4 with no abstract and
are screened on title alone. This is not a pipeline bug — the OpenAlex search
endpoint returns `abstract_inverted_index` by default and it is simply null for
many works, and Crossref often has no deposited abstract. Measured recovery
against `fetchpdf`'s 10-source ladder is ~30%, so roughly 8,700 are retrievable
and the remaining ~20,000 genuinely have no abstract in any source we query.

Reads and writes the cached JSONL rather than `candidates_dedup.csv`: leaving the
funnel CSVs untouched keeps stage counts reproducible and the recall harness
comparable across runs.

Parallelism lives here rather than in fetchpdf because `fetch_abstract_from_doi`
is strictly serial with a sleep after each of up to 10 sources — a 30-DOI probe
exceeded two minutes. Keeping the ThreadPoolExecutor in this repo also avoids the
diverged fetchpdf/fetchpdf_public copies.

Usage:
    python -m mo_pipeline.discover.backfill_abstracts --limit 500     # measure first
    python -m mo_pipeline.discover.backfill_abstracts                 # full run
    python -m mo_pipeline.discover.backfill_abstracts --stats         # coverage only
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    from fetchpdf.fetch_abstract_from_doi import fetch_abstract_from_doi
except ImportError:  # fail here, not inside a worker thread where it cannot exit cleanly
    sys.exit("fetchpdf not importable.\n"
             "  pip install --break-system-packages --user -e "
             "/home/dan/Dropbox/AAA_METASCIENCE_OBSERVATORY/fetchpdf")

CACHE_DIR = Path.home() / ".local/share/mo_pipeline/cache"
CACHE_PATH = CACHE_DIR / "abstracts_dedup.jsonl.gz"
# Answered DOIs, so a killed run resumes instead of re-fetching. Holds negative
# results too — a DOI with no abstract anywhere must not be retried every run.
CHECKPOINT_PATH = CACHE_DIR / "backfill_abstracts_checkpoint.jsonl"

MIN_ABSTRACT_CHARS = 80  # shorter than this is a stub, not an abstract

_print_lock = threading.Lock()


def _load_cache(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"cache not found: {path}\nRun the search stages first, or restore the mirror "
                 f"from /media/dan/500Gb/metascience_observatory_pdfs/cache/")
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def _load_checkpoint() -> dict[str, str | None]:
    """{doi: abstract_or_None} for every DOI already attempted."""
    done: dict[str, str | None] = {}
    if not CHECKPOINT_PATH.exists():
        return done
    with open(CHECKPOINT_PATH, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # tolerate a torn final line from a killed run
            done[rec["doi"]] = rec.get("abstract")
    return done


def _append_checkpoint(fh, doi: str, abstract: str | None) -> None:
    fh.write(json.dumps({"doi": doi, "abstract": abstract}, ensure_ascii=False) + "\n")
    fh.flush()


def _fetch_one(doi: str) -> tuple[str, str | None]:
    """Return (doi, abstract_or_None) via fetchpdf's 10-source ladder."""
    try:
        result = fetch_abstract_from_doi(doi, verbose=False) or {}
        abstract = (result.get("abstract") or "").strip()
        return doi, abstract if len(abstract) >= MIN_ABSTRACT_CHARS else None
    except Exception:
        # A dead source or malformed DOI should not take the batch down; the
        # checkpoint records the miss so it is not retried.
        return doi, None


def coverage(rows: list[dict]) -> tuple[int, int, int]:
    """(total, with_abstract, missing_with_doi)"""
    total = len(rows)
    have = sum(1 for r in rows if (r.get("abstract") or "").strip())
    missing = sum(1 for r in rows
                  if not (r.get("abstract") or "").strip() and (r.get("doi") or "").strip())
    return total, have, missing


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=None,
                    help="only attempt the first N missing DOIs (measure runtime first)")
    ap.add_argument("--workers", type=int, default=12,
                    help="concurrent fetches (default 12; the ladder is network-bound)")
    ap.add_argument("--stats", action="store_true", help="report coverage and exit")
    args = ap.parse_args()

    rows = _load_cache(CACHE_PATH)
    total, have, missing = coverage(rows)
    print(f"cache: {CACHE_PATH}")
    print(f"  rows            : {total:,}")
    print(f"  with abstract   : {have:,} ({100*have/total:.0f}%)")
    print(f"  missing + DOI   : {missing:,}")
    if args.stats:
        return

    done = _load_checkpoint()
    todo = [r["doi"] for r in rows
            if not (r.get("abstract") or "").strip()
            and (r.get("doi") or "").strip()
            and r["doi"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"  already attempted: {len(done):,}")
    print(f"  to fetch now     : {len(todo):,}\n")
    if not todo:
        print("nothing to do")
        return

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    found = 0
    with open(CHECKPOINT_PATH, "a", encoding="utf-8") as ck:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(_fetch_one, d): d for d in todo}
            for i, fut in enumerate(as_completed(futures), 1):
                doi, abstract = fut.result()
                if abstract:
                    found += 1
                _append_checkpoint(ck, doi, abstract)
                if i % 50 == 0 or i == len(todo):
                    el = time.monotonic() - start
                    rate = i / el if el else 0
                    eta = (len(todo) - i) / rate / 60 if rate else 0
                    with _print_lock:
                        print(f"  {i:,}/{len(todo):,} attempted | {found:,} recovered "
                              f"({100*found/i:.0f}%) | {rate:.1f}/s | ETA {eta:.0f}min",
                              flush=True)

    elapsed = time.monotonic() - start
    print(f"\nfetched {len(todo):,} in {elapsed/60:.1f} min — recovered {found:,} "
          f"({100*found/max(len(todo),1):.0f}%)")

    # Merge into the cache. Rewrite via a temp file + os.replace so a crash
    # mid-write cannot leave a truncated cache (same pattern as keyword_stats.py).
    merged = _load_checkpoint()
    applied = 0
    tmp = CACHE_PATH.with_suffix(".tmp.gz")
    with gzip.open(tmp, "wt", encoding="utf-8") as out:
        for r in rows:
            if not (r.get("abstract") or "").strip():
                got = merged.get((r.get("doi") or "").strip())
                if got:
                    r["abstract"] = got
                    r["abstract_backfilled"] = True
                    applied += 1
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, CACHE_PATH)

    total2, have2, missing2 = coverage(rows)
    print(f"applied {applied:,} abstracts to the cache")
    print(f"  coverage {have:,} -> {have2:,} ({100*have/total:.0f}% -> {100*have2/total2:.0f}%)")
    print(f"\nNext: re-screen only the rows that gained an abstract — they were previously")
    print(f"      classified on title alone. Delete those DOIs from classified.csv first,")
    print(f"      or Level-1 DOI reuse in classify_candidates.py will skip them.")


if __name__ == "__main__":
    main()
