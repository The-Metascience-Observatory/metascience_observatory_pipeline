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
    """Deduplicate rows sharing a DOI, PMID or normalized title (> 20 chars).

    Union-find over row indices, so a row that carries two identifiers merges the
    groups each one already belongs to: A(doi=X), B(pmid=P), C(doi=X, pmid=P) is
    one paper. The previous first-match lookup put C with A and left B on its own,
    so the same paper went to the screening LLM twice. Near-linear.
    """
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)   # keep the earliest row as root

    first_seen = {}  # (kind, value) -> first row index carrying it
    for i, row in enumerate(rows):
        doi = normalize_doi(row.get("doi", ""))
        pmid = (row.get("pmid") or "").strip()
        title = normalize_title(row.get("title", ""))
        keys = [("doi", doi), ("pmid", pmid)] + ([("title", title)] if title and len(title) > 20 else [])
        for key in keys:
            if not key[1]:
                continue
            if key in first_seen:
                union(i, first_seen[key])
            else:
                first_seen[key] = i

    groups = defaultdict(list)  # root row index -> [rows], in input order
    for i, row in enumerate(rows):
        groups[find(i)].append(row)
    return [merge_rows(groups[k]) for k in sorted(groups)]


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
