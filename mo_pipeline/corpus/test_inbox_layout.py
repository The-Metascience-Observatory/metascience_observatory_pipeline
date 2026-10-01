"""inbox-subfolders: flat inbox files move into one folder per record."""
from pathlib import Path

from mo_pipeline.corpus import inbox_layout

STEM = "10.1000--abc.1"
RECORD_FILES = [
    f"{STEM}.pdf", f"{STEM}.xml", f"{STEM}.fulltext.html", f"{STEM}.landing.html",
    f"{STEM}_from_xml.md", f"{STEM}.provenance.json", f"{STEM}_abstract.md",
    f"{STEM}_supplementary_info.json", f"{STEM}_supplementary_info_2.xlsx",
]


def _touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_every_file_of_a_flat_record_moves_into_its_own_folder(tmp_path):
    for name in RECORD_FILES:
        _touch(tmp_path / name)
    _touch(tmp_path / f"{STEM}_images" / "fig1.png")

    dry = inbox_layout.migrate_inbox(execute=False, inbox=tmp_path)
    assert dry["records"] == 1
    assert dry["files"] == len(RECORD_FILES) + 1
    assert (tmp_path / f"{STEM}.pdf").exists(), "dry run must move nothing"

    done = inbox_layout.migrate_inbox(execute=True, inbox=tmp_path)
    folder = tmp_path / STEM
    assert done["records"] == 1
    assert sorted(p.name for p in folder.iterdir()) == sorted(RECORD_FILES + [f"{STEM}_images"])
    assert (folder / f"{STEM}_images" / "fig1.png").exists()
    assert not [p for p in tmp_path.iterdir() if p.is_file()]


def test_run_level_files_and_unparseable_names_stay_at_the_root(tmp_path):
    for name in ["failed_dois.csv", "missing_pdfs.html", ".fetchpdf_resolution.json",
                 "10.1037--xge0000263 .pdf", "notes.txt"]:
        _touch(tmp_path / name)

    summary = inbox_layout.migrate_inbox(execute=True, inbox=tmp_path)

    assert summary["records"] == 0
    assert summary["run_level"] == 3
    assert summary["leftovers"] == ["10.1037--xge0000263 .pdf", "notes.txt"]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([
        "failed_dois.csv", "missing_pdfs.html", ".fetchpdf_resolution.json",
        "10.1037--xge0000263 .pdf", "notes.txt"])


def test_a_file_already_in_the_folder_is_never_overwritten(tmp_path):
    _touch(tmp_path / STEM / f"{STEM}.pdf", "refetched")
    flat = _touch(tmp_path / f"{STEM}.pdf", "old")

    summary = inbox_layout.migrate_inbox(execute=True, inbox=tmp_path)

    assert summary["already_in_folder"] == 1
    assert flat.read_text() == "old"
    assert (tmp_path / STEM / f"{STEM}.pdf").read_text() == "refetched"


def test_existing_record_folders_are_left_alone_and_a_second_run_is_a_no_op(tmp_path):
    for name in RECORD_FILES:
        _touch(tmp_path / name)
    inbox_layout.migrate_inbox(execute=True, inbox=tmp_path)

    again = inbox_layout.migrate_inbox(execute=True, inbox=tmp_path)

    assert again["records"] == 0 and again["files"] == 0 and again["leftovers"] == []


def test_record_stem_only_accepts_canonical_folder_names():
    assert inbox_layout.record_stem(f"{STEM}.fulltext.html") == STEM
    assert inbox_layout.record_stem(f"{STEM}_supplementary_info_7.csv") == STEM
    assert inbox_layout.record_stem(f"{STEM}_images") == STEM
    assert inbox_layout.record_stem("10.1037--xge0000263 .pdf") is None   # trailing space
    assert inbox_layout.record_stem("README.md") is None
    assert inbox_layout.record_stem(".pdf") is None
