"""
Runtime-editable overlay for the stage-1 search keyword lists.

The curated lists live in `search_for_replication_studies.py` as code defaults.
This module lets the dashboard VIEW and EDIT them without rewriting Python: edits
are stored as a JSON overlay at `data/keywords.json` (gitignored runtime state),
merged over the code defaults per-list. Deleting the overlay (or a single list)
reverts to the code defaults.

Flow:
  - search module defines its default lists, then calls `apply_overrides(defaults)`
    at import → registers the defaults here and returns the effective lists.
  - the API calls `effective()` (fresh read) for GET, `save_list()` for PUT,
    `reset()` to revert. None of these import the search module (no cycle).
"""
from __future__ import annotations

import copy
import json

from mo_pipeline import config

KEYWORDS_PATH = config.DATA_DIR / "keywords.json"

# Metadata for the UI: which lists exist, a label, which sources each feeds, and
# the query syntax so the editor can hint the user.
KEY_META = [
    {"key": "replication_core", "label": "Core replication terms",
     "feeds": ["OpenAlex", "Crossref", "OSF", "Semantic Scholar"],
     "syntax": "bare phrase (e.g. \"conceptual replication\")"},
    {"key": "genetics_queries", "label": "Genetics / GWAS terms",
     "feeds": ["OpenAlex", "Crossref"],
     "syntax": "bare phrase (biomedical only — excluded from OSF/S2)"},
    {"key": "pubmed_queries", "label": "PubMed queries",
     "feeds": ["PubMed"],
     "syntax": "fielded, e.g. '\"replication study\"[Title/Abstract]'"},
    {"key": "europepmc_queries", "label": "Europe PMC queries",
     "feeds": ["Europe PMC"],
     "syntax": "fielded, e.g. '(BODY:\"we replicated\") AND SRC:MED'"},
]
_KEYS = [m["key"] for m in KEY_META]

_DEFAULTS: dict[str, list[str]] = {}


def register_defaults(defaults: dict[str, list[str]]) -> None:
    """Called by the search module at import so effective()/the API know the
    code defaults without importing the search module (avoids a cycle)."""
    _DEFAULTS.update(copy.deepcopy(defaults))


def _read_overlay() -> dict:
    try:
        return json.loads(KEYWORDS_PATH.read_text())
    except Exception:
        return {}


def apply_overrides(defaults: dict[str, list[str]]) -> dict[str, list[str]]:
    """Register `defaults`, then return them with any overlay lists substituted."""
    register_defaults(defaults)
    overlay = _read_overlay()
    return {k: list(overlay.get(k, defaults[k])) for k in defaults}


def effective() -> dict[str, list[str]]:
    """Current effective lists = code defaults with overlay substituted."""
    overlay = _read_overlay()
    return {k: list(overlay.get(k, _DEFAULTS.get(k, []))) for k in _KEYS if k in _DEFAULTS}


def is_overridden() -> dict[str, bool]:
    overlay = _read_overlay()
    return {k: (k in overlay) for k in _KEYS}


def save_list(key: str, items: list[str]) -> dict[str, list[str]]:
    """Persist an edited list to the overlay. Validates key + basic shape."""
    if key not in _KEYS:
        raise KeyError(f"unknown keyword list: {key}")
    cleaned = [s.strip() for s in items if isinstance(s, str) and s.strip()]
    overlay = _read_overlay()
    overlay[key] = cleaned
    KEYWORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEYWORDS_PATH.write_text(json.dumps(overlay, indent=2))
    return effective()


def reset(key: str | None = None) -> dict[str, list[str]]:
    """Revert one list (or all) to the code defaults by dropping the overlay entry."""
    overlay = _read_overlay()
    if key is None:
        overlay = {}
    else:
        overlay.pop(key, None)
    KEYWORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEYWORDS_PATH.write_text(json.dumps(overlay, indent=2))
    return effective()
