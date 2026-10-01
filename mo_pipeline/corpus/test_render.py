"""render: one writer for renditions, and nothing gets past the prose gate."""
from pathlib import Path

from mo_pipeline.corpus import render

STEM = "10.1000--abc.1"

_SENTENCE = ("This is a full sentence of ordinary prose that runs well past the "
             "gate's sixty-character median line length on its own.")

GOOD_JATS = f"""<article><front><article-meta><title-group>
<article-title>A paper</article-title></title-group></article-meta></front>
<body><sec><title>Introduction</title>
<p>{_SENTENCE} {_SENTENCE}</p><p>{_SENTENCE}</p>
</sec></body></article>""".encode()

# An unknown wrapper full of leaf citation labels: the converter descends into
# the wrapper and emits each leaf as its own line, so the median line is short.
HUSK_JATS = ("<article><body><sec><title>Introduction</title><wrapper>"
             + "".join(f"<leaf>Author {i} (20{i:02d})</leaf>" for i in range(20))
             + "</wrapper></sec></body></article>").encode()


def _record(root: Path, stem: str, xml: bytes) -> Path:
    folder = root / stem
    folder.mkdir(parents=True)
    (folder / f"{stem}.xml").write_bytes(xml)
    return folder


def test_search_dirs_covers_paper_folders_inbox_record_folders_and_the_inbox_root(tmp_path):
    papers, inbox = tmp_path / "papers", tmp_path / "inbox"
    (papers / STEM).mkdir(parents=True)
    (inbox / "10.2000--xyz").mkdir(parents=True)
    (inbox / "not-a-record").mkdir()

    dirs = render._search_dirs(papers, inbox)

    assert dirs == [papers / STEM, inbox / "10.2000--xyz", inbox]


def test_render_dirs_writes_a_rendition_that_passes_the_gate_and_refuses_a_husk(tmp_path):
    good = _record(tmp_path, STEM, GOOD_JATS)
    husk = _record(tmp_path, "10.2000--husk", HUSK_JATS)

    summary = render.render_dirs([good, husk], execute=True)

    assert summary["converted"] == 1
    assert summary["rejected_no_prose"] == 1
    rendition = good / f"{STEM}_from_xml.md"
    assert rendition.exists()
    assert "## Introduction" in rendition.read_text()
    assert not list(husk.glob("*.md")), "a rejected rendition must not touch disk"


def test_dry_run_reports_pending_work_and_writes_nothing(tmp_path):
    good = _record(tmp_path, STEM, GOOD_JATS)

    summary = render.render_dirs([good], execute=False)

    assert summary["pending"] == 1 and summary["converted"] == 0
    assert not list(good.glob("*.md"))


def test_an_existing_rendition_is_left_alone_unless_overwrite(tmp_path):
    good = _record(tmp_path, STEM, GOOD_JATS)
    (good / f"{STEM}_from_xml.md").write_text("keep me")

    summary = render.render_dirs([good], execute=True)

    assert summary["already_present"] == 1 and summary["converted"] == 0
    assert (good / f"{STEM}_from_xml.md").read_text() == "keep me"


def test_prose_score_ignores_headings_quotes_and_table_markup():
    text = "# Heading\n> quote\n<table><tr><td>1</td></tr></table>\n" + _SENTENCE + "\n"
    assert render.prose_score(text) == len(_SENTENCE)


# -- the fallback rung ------------------------------------------------------


def _conversion(markdown):
    from fetchpdf.retrieval.to_markdown import Conversion
    return Conversion(markdown=markdown)


GOOD_MARKDOWN = f"# A paper\n\n{_SENTENCE}\n\n{_SENTENCE}\n"


def test_litdown_gets_a_turn_only_when_the_gate_refuses_fetchpdf(tmp_path):
    husk = _record(tmp_path, STEM, HUSK_JATS)
    calls = []

    def fake_litdown(path, fetchpdf_markdown=""):
        calls.append(Path(path).name)
        return _conversion(GOOD_MARKDOWN)

    render.litdown_render.convert_xml, original = fake_litdown, render.litdown_render.convert_xml
    try:
        summary = render.render_dirs([husk], execute=True)
    finally:
        render.litdown_render.convert_xml = original

    assert calls == [f"{STEM}.xml"], "the husk should have been handed down the ladder"
    assert summary["converted"] == 1
    assert summary["by_converter"] == {render.litdown_render.CONVERTER: 1}
    rendition = (husk / f"{STEM}_from_xml.md").read_text()
    assert f"converter: {render.litdown_render.CONVERTER}" in rendition


def test_a_rendition_fetchpdf_can_make_never_reaches_the_second_rung(tmp_path):
    good = _record(tmp_path, STEM, GOOD_JATS)
    calls = []

    def fake_litdown(path, fetchpdf_markdown=""):
        calls.append(path)
        return _conversion(GOOD_MARKDOWN)

    render.litdown_render.convert_xml, original = fake_litdown, render.litdown_render.convert_xml
    try:
        summary = render.render_dirs([good], execute=True)
    finally:
        render.litdown_render.convert_xml = original

    assert calls == []
    assert summary["by_converter"] == {render.FETCHPDF: 1}


def test_both_rungs_failing_leaves_the_record_exactly_as_it_was(tmp_path):
    husk = _record(tmp_path, STEM, HUSK_JATS)

    render.litdown_render.convert_xml, original = (
        lambda path, fetchpdf_markdown="": None, render.litdown_render.convert_xml)
    try:
        summary = render.render_dirs([husk], execute=True)
    finally:
        render.litdown_render.convert_xml = original

    assert summary["converted"] == 0 and summary["rejected_no_prose"] == 1
    assert not list(husk.glob("*.md"))


def test_the_header_states_the_table_format_it_actually_wrote(tmp_path):
    husk = _record(tmp_path, STEM, HUSK_JATS)
    grid = f"{_SENTENCE}\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n{_SENTENCE}\n"

    render.litdown_render.convert_xml, original = (
        lambda path, fetchpdf_markdown="": _conversion(grid),
        render.litdown_render.convert_xml)
    try:
        render.render_dirs([husk], execute=True)
    finally:
        render.litdown_render.convert_xml = original

    header = (husk / f"{STEM}_from_xml.md").read_text()
    # fetchpdf's header hard-codes canonical-html; claiming it over a flattened
    # grid would tell a reader the spans are authoritative when they are gone.
    assert "table_format: markdown-grid" in header
    assert "colspan` and `rowspan` are " not in header
