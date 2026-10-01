"""Stage-4 verdicts: enum validation, Level-2 verdicts, and stale-row reuse."""
import csv

from mo_pipeline.discover import classify_candidates as cc


def test_valid_verdict_is_normalized():
    v = cc._validated({"is_replication": "true", "confidence": "High", "replication_type": "Direct"})
    assert v["is_replication"] is True and v["confidence"] == "high" and v["replication_type"] == "direct"
    assert cc._validated({"is_replication": False, "confidence": "low", "replication_type": None})


def test_type_in_the_confidence_column_is_rejected():
    assert cc._validated({"is_replication": True, "confidence": "close", "replication_type": "close"}) is None
    assert cc._validated({"is_replication": True, "confidence": "high", "replication_type": "partial"}) is None
    assert cc._validated({"is_replication": "maybe", "confidence": "high", "replication_type": None}) is None


def test_legacy_auto_rows_and_invalid_rows_are_not_reused():
    good = {"is_replication": "True", "confidence": "high", "replication_type": "direct", "reasoning": "x"}
    assert cc._prior_is_valid(good)
    assert not cc._prior_is_valid(dict(good, reasoning="Already ingested into corpus"))
    assert not cc._prior_is_valid(dict(good, confidence="close"))


def test_manifest_without_verdict_column_is_ignored(tmp_path, monkeypatch):
    p = tmp_path / "m.csv"
    p.write_text("doi,ingested_path\n10.1/a,/x\n")
    monkeypatch.setattr(cc, "PROCESSED_MANIFEST_CSV", p)
    assert cc._load_ingested_dois() == {}
    p.write_text("doi,ingested_path,contains_replications\n10.1/a,/x,1\n10.1/b,/y,0\n10.1/c,/z,\n")
    assert cc._load_ingested_dois() == {"10.1/a": "1", "10.1/b": "0"}
