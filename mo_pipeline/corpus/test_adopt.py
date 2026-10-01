"""adopt-structured: what stage 6 left in inbox/{doi}/ moves into papers/{doi}/."""
from pathlib import Path

from mo_pipeline.corpus import adopt

STEM = "10.1000--abc.1"


def _touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _drive(tmp_path):
    inbox, papers = tmp_path / "inbox", tmp_path / "papers"
    inbox.mkdir()
    papers.mkdir()
    return inbox, papers


def test_a_converted_record_is_moved_whole_and_its_inbox_folder_removed(tmp_path):
    inbox, papers = _drive(tmp_path)
    for name in [f"{STEM}.xml", f"{STEM}_from_xml.md", f"{STEM}.provenance.json"]:
        _touch(inbox / STEM / name)
    _touch(papers / STEM / "body.md")

    dry = adopt.adopt_structured(execute=False, inbox=inbox, papers=papers)
    assert dry["adopted"] == 1 and dry["files"] == 3
    assert (inbox / STEM / f"{STEM}.xml").exists(), "dry run must move nothing"

    done = adopt.adopt_structured(execute=True, inbox=inbox, papers=papers)
    assert done["adopted"] == 1 and done["files"] == 3
    assert sorted(p.name for p in (papers / STEM).iterdir()) == sorted(
        ["body.md", f"{STEM}.xml", f"{STEM}_from_xml.md", f"{STEM}.provenance.json"])
    assert not (inbox / STEM).exists()


def test_a_record_whose_pdf_is_still_in_the_inbox_waits_for_conversion(tmp_path):
    inbox, papers = _drive(tmp_path)
    _touch(inbox / STEM / f"{STEM}.pdf")
    _touch(inbox / STEM / f"{STEM}.xml")
    _touch(papers / STEM / "body.md")

    summary = adopt.adopt_structured(execute=True, inbox=inbox, papers=papers)

    assert summary["awaiting_conversion"] == 1 and summary["adopted"] == 0
    assert (inbox / STEM / f"{STEM}.xml").exists()


def test_a_record_with_no_full_text_is_left_alone(tmp_path):
    """Sidecars with no paper folder are not worth creating one for."""
    inbox, papers = _drive(tmp_path)
    _touch(inbox / STEM / f"{STEM}.provenance.json")

    summary = adopt.adopt_structured(execute=True, inbox=inbox, papers=papers)

    assert summary["no_paper_folder"] == 1 and summary["created"] == 0
    assert (inbox / STEM / f"{STEM}.provenance.json").exists()


def test_an_xml_only_record_gets_the_paper_folder_stage_7_never_made(tmp_path):
    """No PDF means stage 6 never created papers/{stem}/, so adoption must."""
    inbox, papers = _drive(tmp_path)
    _touch(inbox / STEM / f"{STEM}.xml")
    _touch(inbox / STEM / f"{STEM}_from_xml.md")

    summary = adopt.adopt_structured(execute=True, inbox=inbox, papers=papers)

    assert summary["adopted"] == 1 and summary["created"] == 1
    assert sorted(p.name for p in (papers / STEM).iterdir()) == sorted(
        ["doi.txt", f"{STEM}.xml", f"{STEM}_from_xml.md"])
    assert not (inbox / STEM).exists()


def test_the_doi_txt_written_for_a_new_folder_round_trips_to_its_name(tmp_path):
    """Invariant 4: `repair-names --check` verifies exactly this."""
    from mo_pipeline.corpus.models import doi_to_folder

    inbox, papers = _drive(tmp_path)
    stem = doi_to_folder("10.1016/j.jesp.2017.04.009")
    _touch(inbox / stem / f"{stem}.xml")

    adopt.adopt_structured(execute=True, inbox=inbox, papers=papers)

    doi = (papers / stem / "doi.txt").read_text().strip()
    assert doi == "10.1016/j.jesp.2017.04.009"
    assert doi_to_folder(doi) == stem


def test_a_pdf_bearing_record_with_no_paper_folder_still_waits_for_stage_7(tmp_path):
    inbox, papers = _drive(tmp_path)
    _touch(inbox / STEM / f"{STEM}.pdf")
    _touch(inbox / STEM / f"{STEM}.xml")

    summary = adopt.adopt_structured(execute=True, inbox=inbox, papers=papers)

    assert summary["awaiting_conversion"] == 1 and summary["created"] == 0
    assert not (papers / STEM).exists()


def test_corpus_files_are_never_overwritten_and_the_leftover_keeps_its_folder(tmp_path):
    inbox, papers = _drive(tmp_path)
    _touch(inbox / STEM / f"{STEM}.xml", "inbox copy")
    _touch(inbox / STEM / f"{STEM}_from_xml.md")
    _touch(papers / STEM / f"{STEM}.xml", "corpus copy")

    summary = adopt.adopt_structured(execute=True, inbox=inbox, papers=papers)

    assert summary["already_in_corpus"] == 1 and summary["files"] == 1
    assert (papers / STEM / f"{STEM}.xml").read_text() == "corpus copy"
    assert (inbox / STEM / f"{STEM}.xml").read_text() == "inbox copy"
    assert (papers / STEM / f"{STEM}_from_xml.md").exists()


def test_flat_files_at_the_inbox_root_are_not_records(tmp_path):
    inbox, papers = _drive(tmp_path)
    _touch(inbox / f"{STEM}.xml")
    _touch(papers / STEM / "body.md")

    summary = adopt.adopt_structured(execute=True, inbox=inbox, papers=papers)

    assert summary["adopted"] == 0
    assert (inbox / f"{STEM}.xml").exists()
