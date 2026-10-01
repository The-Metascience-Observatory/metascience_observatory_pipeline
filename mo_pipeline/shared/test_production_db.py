"""Tests for the published-database reader (shared/production_db.py)."""
import pytest

from mo_pipeline import config
from mo_pipeline.shared.production_db import published_dois


def test_published_dois_normalizes_replication_urls(tmp_path):
    db = tmp_path / "replications_database_x.csv"
    db.write_text("replication_url,original_url\n"
                  "https://doi.org/10.1000/A,x\n"
                  "http://dx.doi.org/10.1000/b.,y\n"
                  "not a doi,z\n")
    dois, path = published_dois(db)
    assert path == db
    assert dois == {"10.1000/a", "10.1000/b"}


def test_unresolved_database_is_reported_not_empty(monkeypatch):
    monkeypatch.setattr(config, "latest_replications_db", lambda: None)
    assert published_dois() == (set(), None)


def test_latest_replications_db_resolves_to_an_existing_csv():
    """A pinned path is what broke the already-published exclusion before.

    Skipped when the website checkout is absent, since the pipeline is usable
    without it -- but if version_history.txt IS there, the file it names must
    exist, or stage 5 would quietly stop excluding published papers.
    """
    if not config.VERSION_HISTORY_PATH.exists():
        pytest.skip("website data dir not present")
    db = config.latest_replications_db()
    assert db is not None, (
        f"{config.VERSION_HISTORY_PATH} exists but names no readable CSV")
    assert db.exists() and db.suffix == ".csv"
    assert db.name.startswith("replications_database_")
