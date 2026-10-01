from mo_pipeline.discover import build_processed_manifest as b


def test_production_db_overrides_and_extends_the_catalog(tmp_path, monkeypatch):
    db = tmp_path / "replications_database_x.csv"
    db.write_text("replication_url,original_url\nhttps://doi.org/10.1000/A,x\nhttps://doi.org/10.1000/new.,y\n")
    monkeypatch.setattr(b, "_rows_from_catalog", lambda: [("10.1000/a", "/p/a", "0"), ("10.1000/b", "/p/b", "")])
    monkeypatch.setattr("mo_pipeline.config.latest_replications_db", lambda: db)
    out = tmp_path / "m.csv"
    monkeypatch.setattr(b, "PROCESSED_MANIFEST_CSV", out)
    monkeypatch.setattr(b, "DATA_DIR", tmp_path)
    b.main()
    rows = {r.split(",")[0]: r.split(",")[2] for r in out.read_text().splitlines()[1:]}
    assert rows == {"10.1000/a": "1", "10.1000/b": "", "10.1000/new": "1"}
