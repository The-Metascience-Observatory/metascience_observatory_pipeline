"""
Author Name Remediation Script
================================
Scans the master replications database for author entries that are pure
abbreviations (e.g. "Smith, J." or "E. J. Masicampo") and tries to recover
full given names by re-querying Crossref, OpenAlex, and Semantic Scholar.

Category A (targeted here): first token of given name is an initial AND
  there is no subsequent full-name token — i.e. no usable given name at all.
Category B (skipped): "J. Lukas Thürmer" style — initial + full middle name
  used as primary name. This IS the author's real style; leave it alone.

Usage:
    python remediate_author_names.py
    python remediate_author_names.py --dry-run          # show what would change, don't save
    python remediate_author_names.py --limit 50         # process at most 50 affected rows
    python remediate_author_names.py --col original     # only fix original_authors
    python remediate_author_names.py --col replication  # only fix replication_authors
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# ── reuse helpers from the main fetch module ──────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
from fetch_metadata_from_doi import (
    _is_initial_token,
    _count_full_first_names,
    _authors_have_abbreviations,
    _new_authors_are_better,
    _format_initial,
    _request_with_retry,
    CROSSREF_API_KEY,
    CROSSREF_EMAIL,
    SEMANTIC_SCHOLAR_API_KEY,
    OPENALEX_API_KEY,
    CONTACT_EMAIL,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR    = os.path.join(SCRIPT_DIR, '..', 'data')
BACKUP_DIR  = os.path.join(DATA_DIR, 'backup')
VERSION_HISTORY_PATH = os.path.join(DATA_DIR, 'version_history.txt')
CACHE_PATH  = os.path.join(SCRIPT_DIR, 'author_remediation_cache.json')

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/117.0.0.0 Safari/537.36"
    )
}


# ── Category-A detection ──────────────────────────────────────────────────────

def _is_category_a(name: str) -> bool:
    """Return True if this author entry is a pure abbreviation with no full
    given-name component (Category A).

    Category A examples  : "Smith, J."  "J.K. Smith"  "E. J. Masicampo"
    Category B (skip)    : "J. Lukas Thürmer"  — initial + full middle name
    Full name (skip)     : "John Smith"  "Smith, John"
    """
    name = name.strip()
    if not name:
        return False
    if ',' in name:
        given = name.split(',', 1)[1].strip()
    else:
        parts = name.split()
        given = ' '.join(parts[:-1]) if len(parts) > 1 else (parts[0] if parts else '')
    tokens = given.split()
    if not tokens:
        return False
    # First token must be an initial
    if not _is_initial_token(tokens[0]):
        return False
    # For it to be Category A there must be NO full-name token anywhere in given
    for tok in tokens[1:]:
        if not _is_initial_token(tok):
            return False  # Category B — has a real word after the initial
    return True


def has_category_a(authors_str: str) -> bool:
    """Return True if any author in the semicolon-separated string is Category A."""
    if not authors_str or not isinstance(authors_str, str):
        return False
    return any(_is_category_a(a) for a in authors_str.split(';') if a.strip())


# ── Per-API author fetchers ───────────────────────────────────────────────────

def _fetch_crossref(doi: str) -> str | None:
    """Return semicolon-separated author string from Crossref, or None."""
    try:
        url = f"https://api.crossref.org/works/{doi}"
        if CROSSREF_EMAIL:
            url += f"?mailto={CROSSREF_EMAIL}"
        hdrs = HEADERS.copy()
        if CROSSREF_API_KEY:
            hdrs['Crossref-Plus-API-Token'] = f'Bearer {CROSSREF_API_KEY}'
        r = _request_with_retry(url, headers=hdrs)
        if r and r.status_code == 200:
            m = r.json()["message"]
            authors = []
            for a in m.get("author", []):
                parts = []
                if "given" in a:
                    parts.append(a["given"])
                if "family" in a:
                    parts.append(a["family"])
                n = _format_initial(" ".join(parts).strip())
                if n:
                    authors.append(n)
            return "; ".join(authors) or None
    except Exception as e:
        logger.debug(f"Crossref error for {doi}: {e}")
    return None


def _fetch_openalex(doi: str) -> str | None:
    """Return semicolon-separated author string from OpenAlex, or None."""
    try:
        url = f"https://api.openalex.org/works/https://doi.org/{doi}"
        if OPENALEX_API_KEY:
            url += f"?api_key={OPENALEX_API_KEY}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            data = r.json()
            names = [
                a["author"]["display_name"]
                for a in data.get("authorships", [])
                if a.get("author", {}).get("display_name")
            ]
            return "; ".join(names) or None
    except Exception as e:
        logger.debug(f"OpenAlex error for {doi}: {e}")
    return None


def _fetch_semantic_scholar(doi: str) -> str | None:
    """Return semicolon-separated author string from Semantic Scholar, or None."""
    try:
        hdrs = HEADERS.copy()
        if SEMANTIC_SCHOLAR_API_KEY:
            hdrs['x-api-key'] = SEMANTIC_SCHOLAR_API_KEY
        r = _request_with_retry(
            f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?fields=authors",
            headers=hdrs,
            max_retries=2,
        )
        if r and r.status_code == 200:
            names = [a.get("name", "") for a in r.json().get("authors", []) if a.get("name")]
            return "; ".join(names) or None
    except Exception as e:
        logger.debug(f"Semantic Scholar error for {doi}: {e}")
    return None


# ── Best-of-three logic ───────────────────────────────────────────────────────

def best_authors_from_apis(doi: str, current: str, delay: float = 0.3) -> tuple[str | None, str]:
    """
    Try Crossref → OpenAlex → Semantic Scholar in order.
    Accept the first result that is strictly better than `current`
    (more full first names, passes the 70% author-count guard).
    Returns (best_authors_string_or_None, source_label).
    """
    best = current
    source = "original"

    for label, fetch_fn in [
        ("Crossref",          lambda: _fetch_crossref(doi)),
        ("OpenAlex",          lambda: _fetch_openalex(doi)),
        ("SemanticScholar",   lambda: _fetch_semantic_scholar(doi)),
    ]:
        time.sleep(delay)
        result = fetch_fn()
        if result and _new_authors_are_better(best, result):
            best = result
            source = label
            # Stop as soon as we have no remaining Category A entries
            if not has_category_a(best):
                break

    if best == current:
        return None, "none"
    return best, source


# ── DOI extraction ────────────────────────────────────────────────────────────

def doi_from_url(url: str) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    url = url.strip()
    for prefix in ("https://doi.org/", "http://doi.org/",
                   "https://dx.doi.org/", "http://dx.doi.org/"):
        if url.startswith(prefix):
            doi = url[len(prefix):]
            return doi if doi.startswith("10.") else None
    return None


# ── Cache helpers ─────────────────────────────────────────────────────────────

def load_cache() -> dict:
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_cache(cache: dict):
    with open(CACHE_PATH, 'w') as f:
        json.dump(cache, f, indent=2)


# ── Latest master database ────────────────────────────────────────────────────

def get_latest_csv() -> str | None:
    if os.path.exists(VERSION_HISTORY_PATH):
        with open(VERSION_HISTORY_PATH) as f:
            lines = f.readlines()
        for line in reversed(lines):
            line = line.strip()
            if line and not line.startswith('#'):
                filename = line.split('#')[0].strip()
                if filename.startswith('../data/'):
                    filename = filename.replace('../data/', '')
                full = os.path.join(DATA_DIR, filename)
                if os.path.exists(full):
                    return full
    import glob as _glob
    candidates = sorted(_glob.glob(os.path.join(DATA_DIR, 'replications_database_*.csv')))
    return candidates[-1] if candidates else None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Remediate abbreviated author names in the database.")
    parser.add_argument('--dry-run', action='store_true',
                        help="Show what would be changed without writing anything")
    parser.add_argument('--limit', type=int, default=None,
                        help="Stop after processing this many affected rows")
    parser.add_argument('--col', choices=['original', 'replication', 'both'], default='both',
                        help="Which author column(s) to fix (default: both)")
    args = parser.parse_args()

    csv_path = get_latest_csv()
    if not csv_path:
        print("ERROR: Could not find master database CSV.", file=sys.stderr)
        sys.exit(1)
    print(f"Database : {os.path.basename(csv_path)}")
    print(f"Dry run  : {args.dry_run}")
    print(f"Columns  : {args.col}")

    # Load
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)
    print(f"Loaded   : {len(rows)} rows\n")

    cache = load_cache()
    print(f"Cache    : {len(cache)} DOI entries already resolved\n")

    target_cols = []
    if args.col in ('original', 'both'):
        target_cols.append('original_authors')
    if args.col in ('replication', 'both'):
        target_cols.append('replication_authors')

    # Identify affected rows
    affected = []  # list of (row_idx, col, doi)
    for i, row in enumerate(rows):
        for col in target_cols:
            val = row.get(col, '')
            if not has_category_a(val):
                continue
            url_col = 'original_url' if col == 'original_authors' else 'replication_url'
            doi = doi_from_url(row.get(url_col, ''))
            if doi:
                affected.append((i, col, doi))

    print(f"Affected : {len(affected)} (row, column) pairs with Category A abbreviations")
    if args.limit:
        affected = affected[:args.limit]
        print(f"Limited  : processing first {args.limit}")
    print()

    # Process
    changes = []
    for n, (i, col, doi) in enumerate(affected, 1):
        current = rows[i].get(col, '')
        cur_full, cur_total = _count_full_first_names(current)

        # Check cache first
        cache_key = f"{doi}::{col}"
        if cache_key in cache:
            cached = cache[cache_key]
            if cached is None:
                logger.debug(f"[{n}/{len(affected)}] SKIP (cached miss) {doi} [{col}]")
                continue
            new_authors, source = cached['authors'], cached['source']
        else:
            print(f"[{n}/{len(affected)}] {doi} [{col}]")
            print(f"  Current ({cur_full}/{cur_total} full): {current[:100]}")
            new_authors, source = best_authors_from_apis(doi, current)
            # Cache result (None means "tried, no improvement")
            cache[cache_key] = {'authors': new_authors, 'source': source} if new_authors else None
            if n % 20 == 0:
                save_cache(cache)

        if new_authors:
            new_full, new_total = _count_full_first_names(new_authors)
            print(f"  → {source} improved {cur_full}/{cur_total} → {new_full}/{new_total} full names")
            print(f"    {new_authors[:100]}")
            changes.append((i, col, new_authors))
            if not args.dry_run:
                rows[i][col] = new_authors
        else:
            logger.debug(f"  No improvement found for {doi} [{col}]")

    save_cache(cache)

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Affected pairs scanned : {len(affected)}")
    print(f"Rows improved          : {len(changes)}")
    if args.dry_run:
        print("DRY RUN — no changes written.")
        return

    if not changes:
        print("Nothing to write.")
        return

    # Back up original
    os.makedirs(BACKUP_DIR, exist_ok=True)
    import shutil
    backup_dest = os.path.join(BACKUP_DIR, os.path.basename(csv_path))
    if not os.path.exists(backup_dest):
        shutil.copy2(csv_path, backup_dest)
        print(f"Backed up → data/backup/{os.path.basename(csv_path)}")

    # Write new timestamped file
    ts = datetime.now().strftime('%Y_%m_%d_%H%M%S')
    new_filename = f"replications_database_{ts}.csv"
    new_path = os.path.join(DATA_DIR, new_filename)
    with open(new_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved    → data/{new_filename}")

    # Append to version_history.txt
    with open(VERSION_HISTORY_PATH, 'a') as f:
        f.write(f"\n{new_filename}  # author name remediation ({len(changes)} rows updated)\n")
    print(f"Updated  → version_history.txt")


if __name__ == '__main__':
    main()
