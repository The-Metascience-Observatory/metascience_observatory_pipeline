"""The API keys in .env.local must actually load.

They are read by path, and the path was wrong for a long time: `.parent.parent`
from `mo_pipeline/shared/` is the package directory, not the repo root, so every
key silently resolved to None. That was invisible while OpenAlex served anonymous
requests; once it moved to a daily budget, anonymous meant "$0 remaining" and every
enrichment call returned HTTP 429.
"""
from __future__ import annotations

from pathlib import Path

from mo_pipeline import config


def test_env_local_path_points_at_the_repo_root():
    from mo_pipeline.shared import fetch_metadata_from_doi as d
    from mo_pipeline.shared import fetch_metadata_from_title as t
    for mod in (d, t):
        resolved = Path(mod.__file__).resolve().parents[2]
        assert resolved == config.REPO_ROOT, f"{mod.__name__} would look in {resolved}"


def test_the_openalex_key_is_found_when_the_file_has_one():
    from mo_pipeline.shared.fetch_metadata_from_doi import _get_openalex_api_key
    env = config.REPO_ROOT / ".env.local"
    if not env.exists() or "OPENALEXAPIKEY=" not in env.read_text():
        return                                   # nothing to assert on this machine
    assert _get_openalex_api_key(), "the key is in .env.local but the loader returned nothing"
