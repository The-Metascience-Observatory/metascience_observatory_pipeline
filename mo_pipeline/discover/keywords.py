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


# ── Per-API query fan-out ────────────────────────────────────────────────────
# Which keyword lists each source runs. The search module reads this to build its
# per-source query lists, and keyword_stats to attribute yield, so it is the one
# definition. OSF and Semantic Scholar (social-sci / CS biased) skip the
# genetics/GWAS terms, which only generate noise there.
API_FANOUT: list[tuple[str, str, list[str]]] = [
    ("pubmed",           "PubMed",           ["pubmed_queries"]),
    ("openalex",         "OpenAlex",         ["replication_core", "genetics_queries"]),
    ("openalex_broad",   "OpenAlex (broad)", ["replication_core", "genetics_queries"]),
    ("europepmc",        "Europe PMC",       ["europepmc_queries"]),
    ("crossref",         "Crossref",         ["replication_core", "genetics_queries"]),
    ("osf",              "OSF",              ["replication_core"]),
    ("semantic_scholar", "Semantic Scholar", ["replication_core"]),
]


def ensure_defaults() -> bool:
    """Make sure the code defaults are registered, importing the search module
    if needed (heavy: requests + Bio, ~0.5 s once). Function-level import so
    there is no cycle and callers like the API registry stay light to import."""
    if not _DEFAULTS:
        try:
            from mo_pipeline.discover import search_for_replication_studies  # noqa: F401
        except Exception:
            return False
    return bool(_DEFAULTS)


def expected_queries() -> dict[str, list[str]]:
    """api name -> ordered effective query list (the real per-API fan-out).
    Reads the overlay fresh, so dashboard edits are reflected immediately."""
    ensure_defaults()
    eff = effective()
    return {api: [q for k in keys for q in eff.get(k, [])]
            for api, _label, keys in API_FANOUT}


def total_effective_queries() -> int:
    return sum(len(v) for v in expected_queries().values())
