"""Read-only view of the published replications database.

The website's newest `replications_database_*.csv` (resolved by
`config.latest_replications_db`, never pinned) is the record of what has
already been ingested. This module is the one place that turns its
`replication_url` column into a set of DOIs; stage 5 uses it to skip papers
that are already published, and the processed manifest uses it to auto-confirm
them in stage 4.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

from mo_pipeline import config

_DOI_RE = re.compile(r"10\.\d{4,9}/\S+", re.I)


def published_dois(path: Path | None = None) -> tuple[set[str], Path | None]:
    """(lowercased replication DOIs, the CSV they came from).

    Returns (set(), None) when no database resolves -- callers decide how loud
    to be about that, but none may treat it as "nothing is published".
    """
    path = path or config.latest_replications_db()
    if not path or not path.exists():
        return set(), None
    csv.field_size_limit(sys.maxsize)
    dois = set()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            m = _DOI_RE.search(row.get("replication_url") or "")
            if m:
                dois.add(m.group(0).rstrip(".,;/").lower())
    return dois, path
