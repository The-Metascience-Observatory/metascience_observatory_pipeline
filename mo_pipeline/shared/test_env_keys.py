"""The API keys in .env.local must actually load.

They were once read by a path resolved from the package directory instead of
the repo root, so every key silently came back None. That was invisible while
OpenAlex served anonymous requests; once it moved to a daily budget, anonymous
meant "$0 remaining" and every enrichment call returned HTTP 429.
"""
from __future__ import annotations

from mo_pipeline import config
from mo_pipeline.shared.env import env_key, read_env_file


def test_env_file_is_the_repo_root_one():
    assert config.ENV_FILE == config.REPO_ROOT / ".env.local"


def test_env_key_prefers_the_environment_then_files(tmp_path, monkeypatch):
    extra = tmp_path / "extra.env"
    extra.write_text("# comment\nFOO_TEST_KEY='from-file'\n")
    monkeypatch.delenv("FOO_TEST_KEY", raising=False)
    assert read_env_file(extra) == {"FOO_TEST_KEY": "from-file"}
    assert env_key("FOO_TEST_KEY", extra) == "from-file"
    monkeypatch.setenv("FOO_TEST_KEY", "from-env")
    assert env_key("FOO_TEST_KEY", extra) == "from-env"


def test_the_openalex_key_is_found_when_the_file_has_one():
    if not read_env_file(config.ENV_FILE).get("OPENALEXAPIKEY"):
        return                                   # nothing to assert on this machine
    from mo_pipeline.shared.fetch_metadata_from_doi import OPENALEX_API_KEY
    assert OPENALEX_API_KEY, "the key is in .env.local but the loader returned nothing"
