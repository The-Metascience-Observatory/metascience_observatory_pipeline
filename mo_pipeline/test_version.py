"""Versioning regression tests. Run:
    cd mo_pipeline && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest mo_pipeline/test_version.py -q
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import mo_pipeline
from mo_pipeline import config, version as v


def test_package_version_is_the_single_source_of_truth():
    assert re.fullmatch(r"\d+\.\d+\.\d+", mo_pipeline.__version__)
    proj = tomllib.load(open(config.REPO_ROOT / "pyproject.toml", "rb"))["project"]
    # pyproject must read the version from the package, never restate it
    assert "version" in proj.get("dynamic", []) and "version" not in proj


def test_manifest_identifies_code_and_prompts():
    m = v.manifest()
    assert m["pipeline_version"] == mo_pipeline.__version__
    assert re.fullmatch(r"[0-9a-f]{16}", m["code_fingerprint"])
    assert set(m) >= {"pipeline_version", "prompt_version", "code_fingerprint",
                      "git_commit", "git_dirty_pipeline", "git_dirty_prompts"}
    assert v.manifest() == m                      # cached: stable within a run


def test_code_fingerprint_tracks_source_content(tmp_path, monkeypatch):
    src = tmp_path / "a.py"
    src.write_text("x = 1\n")
    monkeypatch.setattr(v, "EXTRACT_SOURCES", (str(src.relative_to(tmp_path)),))
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    v._CACHE.clear()
    first = v.code_fingerprint()
    v._CACHE.clear()
    assert v.code_fingerprint() == first          # same content, same fingerprint
    src.write_text("x = 2\n")
    v._CACHE.clear()
    assert v.code_fingerprint() != first          # changed code, changed fingerprint
    v._CACHE.clear()


def test_every_extractor_source_exists():
    missing = [s for s in v.EXTRACT_SOURCES if not (config.REPO_ROOT / s).is_file()]
    assert not missing, f"EXTRACT_SOURCES lists files that do not exist: {missing}"


def test_prompt_files_match_the_changelog_entry_for_the_current_version():
    """The live guard: editing a prompt without bumping version.txt fails here."""
    ver = config.EXTRACTOR_VERSION_FILE.read_text().strip()
    recorded = v.changelog_shas(ver)
    assert recorded, f"prompts/CHANGELOG.md has no hash table for prompt {ver}"
    assert set(recorded) == set(v.prompt_file_sha256()), "changelog lists a different set of prompt files"
    assert v.prompt_drift() == [], (
        "prompt files changed without a version bump — update prompts/version.txt "
        "and add a CHANGELOG section")


def test_prompt_drift_reports_an_unversioned_edit(tmp_path, monkeypatch):
    fake = tmp_path / "CHANGELOG.md"
    ver = config.EXTRACTOR_VERSION_FILE.read_text().strip()
    fake.write_text(f"## Prompt {ver} (test)\n\n| prompt file | sha256 |\n|---|---|\n"
                    f"| prompt_shared_core.md | {'0' * 64} |\n")
    monkeypatch.setattr(v, "PROMPT_CHANGELOG", fake)
    assert v.prompt_drift() == ["prompt_shared_core.md"]


def test_self_check_passes():
    assert v._self_check() == 0
