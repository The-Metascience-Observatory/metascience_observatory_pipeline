"""Stage-5 classification guards.

Two regressions these pin, both of which shipped silently:

* the CLOSE-replication patterns were matched with `re.search` against text that
  had been `.lower()`d, while two of them carry uppercase literals -- so the
  ancestry rule and the `SNP` alternative could never fire and population-change
  studies were passing through as "direct";
* the already-published exclusion was guarded by an `exists()` check on a pinned
  path that had been deleted, so it did nothing and printed nothing.

Run:
    cd mo_pipeline && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
        python -m pytest mo_pipeline/discover/test_filter_direct_replications.py -q
"""
from __future__ import annotations

import pytest

from mo_pipeline import config
from mo_pipeline.discover.filter_direct_replications import (
    DIRECT_RE, EXTENSION_RE, is_strong_direct)


def _row(reasoning: str, *, rtype: str = "direct", conf: str = "high") -> dict:
    return {"replication_type": rtype, "confidence": conf,
            "reasoning": reasoning, "title": ""}


# ── the patterns are case-insensitive ────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "replicated in an african american cohort",          # lowercased, as before
    "Replicated in an African American cohort",          # as the model writes it
    "REPLICATED IN AN AFRICAN AMERICAN COHORT",
])
def test_ancestry_pattern_fires_in_any_case(text):
    assert any(p.search(text) for p in EXTENSION_RE), text


@pytest.mark.parametrize("text", ["snp genotyping", "SNP genotyping", "a novel polymorphism"])
def test_genetic_pattern_fires_in_any_case(text):
    assert any(p.search(text) for p in EXTENSION_RE), text


@pytest.mark.parametrize("text", ["Pre-registered replication", "pre-registered replication",
                                  "Same Protocol", "many labs"])
def test_direct_patterns_fire_in_any_case(text):
    assert any(p.search(text) for p in DIRECT_RE), text


# ── which papers survive the guard ───────────────────────────────────────────

def test_population_change_is_not_a_strong_direct():
    """The bug: this returned True because the ancestry rule never matched."""
    assert is_strong_direct(_row(
        "Pre-registered replication of the association in an African American cohort")) is False


def test_genetic_variant_retest_is_not_a_strong_direct():
    assert is_strong_direct(_row(
        "Pre-registered replication testing the same SNP in an independent sample")) is False


def test_genuine_direct_still_passes():
    assert is_strong_direct(_row(
        "Pre-registered replication following the same protocol")) is True


def test_type_and_confidence_still_gate():
    assert is_strong_direct(_row("same protocol", rtype="conceptual")) is False
    assert is_strong_direct(_row("same protocol", conf="low")) is False


# ── the already-published exclusion resolves a real file ─────────────────────

def test_latest_replications_db_resolves_to_an_existing_csv():
    """A pinned path is what broke this before; the resolver must find the file.

    Skipped rather than failed when the website checkout is absent, since the
    pipeline is usable without it -- but if version_history.txt IS there, the
    file it names must exist, because every caller guards on exists() and would
    otherwise skip the exclusion in silence.
    """
    if not config.VERSION_HISTORY_PATH.exists():
        pytest.skip("website data dir not present")
    db = config.latest_replications_db()
    assert db is not None, (
        f"{config.VERSION_HISTORY_PATH} exists but names no readable CSV; "
        "stage 5 would silently stop excluding already-published papers")
    assert db.exists() and db.suffix == ".csv"
    assert db.name.startswith("replications_database_")
