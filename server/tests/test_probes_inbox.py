"""Stage 5/7 probes count records in the per-DOI inbox layout, plus flat leftovers."""
from mo_pipeline import config
from server.app import registry

STEM = "10.1000--abc.1"


def _inbox(tmp_path):
    record = tmp_path / STEM
    record.mkdir()
    for name in [f"{STEM}.pdf", f"{STEM}.xml", f"{STEM}_from_xml.md"]:
        (record / name).write_text("x")
    (tmp_path / "10.2000--flat.pdf").write_text("x")          # pre-layout leftover
    (tmp_path / "failed_dois.csv").write_text("timestamp,doi,category,detail\n"
                                              "2026-09-02,10.1/x,all_sources_failed,\n")
    return tmp_path


def test_download_probe_counts_record_folders_and_flat_leftovers(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "INBOX_DIR", _inbox(tmp_path))

    probe = registry.probe_download(None)

    assert probe["state"] == "partial"
    assert "2 PDFs in inbox" in probe["detail"]
    assert "1 XML/HTML alongside (1 rendered to markdown)" in probe["detail"]
    assert "1 known failures" in probe["detail"]


def test_convert_probe_sees_pdfs_inside_record_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "INBOX_DIR", _inbox(tmp_path))

    probe = registry.probe_convert(None)

    assert probe["state"] == "partial"
    assert probe["detail"].startswith("2 PDFs in inbox")


def test_probes_are_idle_on_a_missing_inbox(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "INBOX_DIR", tmp_path / "nope")

    assert registry.probe_download(None)["state"] == "idle"
    assert registry.probe_convert(None)["state"] == "idle"
