"""
Step 2: Deduplicate candidate replication studies.

Deduplication strategy (all O(n) via hash-based grouping):
  1. Exact DOI match (lowercased, stripped)
  2. Exact PMID match
  3. Exact normalized-title match (lowercase, no punctuation, collapsed whitespace)

Keeps the record with the most complete metadata when merging.
Streams rows to avoid holding all data in memory at once.

Input:  data/candidates_raw.csv
Output: data/candidates_dedup.csv
"""

import csv
import re
import sys
from collections import defaultdict

from mo_pipeline.config import CANDIDATES_RAW_CSV, CANDIDATES_DEDUP_CSV, DATA_DIR

# Raise CSV field size limit for large abstracts
csv.field_size_limit(sys.maxsize)

FIELDNAMES = [
    "pmid", "doi", "title", "abstract", "authors",
    "journal", "year", "source_api", "source_query", "match_count",
]


def normalize_doi(doi):
    """Lowercase, strip whitespace and trailing punctuation."""
    if not doi:
        return ""
    doi = doi.strip().lower()
    doi = re.sub(r"^https?://doi\.org/", "", doi)
    doi = doi.rstrip(".")
    return doi


def normalize_title(title):
    """Lowercase, remove punctuation, collapse whitespace."""
    if not title:
        return ""
    t = title.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def completeness_score(row):
    """Count how many non-empty fields this row has."""
    return sum(1 for k, v in row.items() if v and v.strip())


def merge_rows(rows):
    """Merge a list of duplicate rows, keeping the most complete one.
    Concatenates source_api and source_query from all duplicates."""
    if len(rows) == 1:
        best = dict(rows[0])
        best["match_count"] = "1"
        return best

    # Sort by completeness, pick the best
    rows_sorted = sorted(rows, key=completeness_score, reverse=True)
    best = dict(rows_sorted[0])

    # Fill any blanks from other rows
    for other in rows_sorted[1:]:
        for k, v in other.items():
            if k in ("source_api", "source_query", "match_count"):
                continue
            if not best.get(k, "").strip() and v and v.strip():
                best[k] = v

    # Combine provenance
    apis = set()
    queries = set()
    for r in rows:
        if r.get("source_api"):
            apis.add(r["source_api"])
        if r.get("source_query"):
            queries.add(r["source_query"])
    best["source_api"] = "; ".join(sorted(apis))
    best["source_query"] = " | ".join(sorted(queries))
    best["match_count"] = str(len(rows))

    return best


def deduplicate(rows):
    """Deduplicate rows by DOI, PMID, then normalized title. All O(n)."""
    # We assign each row to a canonical group identified by a key.
    # Groups are keyed by: DOI (preferred) > PMID > normalized title.
    # We use a union-find-like approach: track group_key -> list of rows.

    groups = defaultdict(list)  # canonical_key -> [rows]
    # Maps to link secondary keys to the same canonical key
    doi_to_key = {}    # normalized_doi -> canonical_key
    pmid_to_key = {}   # pmid -> canonical_key
    title_to_key = {}  # normalized_title -> canonical_key
    next_key = [0]

    def get_new_key():
        k = next_key[0]
        next_key[0] += 1
        return k

    for row in rows:
        doi = normalize_doi(row.get("doi", ""))
        pmid = (row.get("pmid") or "").strip()
        title = normalize_title(row.get("title", ""))

        # Find existing group by any identifier
        canonical = None
        if doi and doi in doi_to_key:
            canonical = doi_to_key[doi]
        elif pmid and pmid in pmid_to_key:
            canonical = pmid_to_key[pmid]
        elif title and len(title) > 20 and title in title_to_key:
            canonical = title_to_key[title]

        if canonical is None:
            canonical = get_new_key()

        # Register all identifiers for this group
        if doi:
            doi_to_key[doi] = canonical
        if pmid:
            pmid_to_key[pmid] = canonical
        if title and len(title) > 20:
            title_to_key[title] = canonical

        groups[canonical].append(row)

    # Merge each group
    result = []
    for key in sorted(groups.keys()):
        result.append(merge_rows(groups[key]))

    return result


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Read raw candidates
    with open(CANDIDATES_RAW_CSV, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    print(f"Loaded {len(rows)} raw candidates")

    deduped = deduplicate(rows)
    print(f"After deduplication: {len(deduped)} unique candidates")

    # Write output
    with open(CANDIDATES_DEDUP_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in deduped:
            writer.writerow({k: row.get(k, "") for k in FIELDNAMES})

    print(f"Wrote {len(deduped)} rows to {CANDIDATES_DEDUP_CSV}")


if __name__ == "__main__":
    main()
