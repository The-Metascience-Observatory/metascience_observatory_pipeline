"""litdown_render: the second rung -- its PMC unwrap and its table splice."""
from fetchpdf.retrieval.to_markdown import Conversion

from mo_pipeline.corpus import litdown_render

STEM = "10.1000--abc.1"

ARTICLE = b"<article><body><p>Hello.</p></body></article>"
ARTICLESET = (b'<?xml version="1.0"?><!DOCTYPE pmc-articleset PUBLIC "-//NLM//DTD '
              b'ARTICLE SET 2.0//EN" "nlm-articleset-2.0.dtd"><pmc-articleset>'
              + ARTICLE + b"</pmc-articleset>")

# Two tables the same paper renders two ways: fetchpdf keeps the spans, litdown
# expands them into a flat grid.
HTML_TABLES = ("<table><tr><th colspan=\"2\" rowspan=\"3\">A</th></tr></table>",
               "<table><tr><td>B</td></tr></table>")
PIPE = ("| A | A |\n| --- | --- |\n| 1 | 2 |")


def _md_with_pipe_tables(n: int) -> str:
    return "Prose paragraph one.\n\n" + "\n\nMore prose.\n\n".join([PIPE] * n) + "\n\nEnd.\n"


def test_a_pmc_articleset_is_unwrapped_to_its_inner_article():
    unwrapped = litdown_render._unwrap_articleset(ARTICLESET)

    assert unwrapped.endswith(b"</article>")
    assert b"pmc-articleset" not in unwrapped
    assert b"<body><p>Hello.</p></body>" in unwrapped


def test_a_document_that_is_not_an_articleset_is_handed_over_untouched():
    assert litdown_render._unwrap_articleset(ARTICLE) == ARTICLE


def test_the_splice_puts_fetchpdfs_html_tables_over_litdowns_grids():
    result = Conversion(markdown="")
    fetchpdf_md = f"words {HTML_TABLES[0]} words {HTML_TABLES[1]} words"

    spliced = litdown_render._splice_tables(_md_with_pipe_tables(2), fetchpdf_md, result)

    assert HTML_TABLES[0] in spliced and HTML_TABLES[1] in spliced
    assert 'colspan="2"' in spliced and 'rowspan="3"' in spliced
    assert "| --- |" not in spliced, "every grid should have been replaced"
    assert result.n_tables == 2
    assert not result.failures


def test_a_count_mismatch_leaves_the_grids_alone_and_says_the_content_is_absent():
    result = Conversion(markdown="")
    fetchpdf_md = f"words {HTML_TABLES[0]} words {HTML_TABLES[1]} words"

    spliced = litdown_render._splice_tables(_md_with_pipe_tables(0), fetchpdf_md, result)

    # Pairing by position across differing counts would file a table under the
    # wrong caption, so nothing is spliced and the loss is stated instead.
    assert "<table" not in spliced
    assert result.n_tables == 0
    assert len(result.failures) == 1
    assert "2 table(s) fewer" in result.failures[0]


def test_nothing_to_splice_when_fetchpdf_found_no_tables():
    result = Conversion(markdown="")
    markdown = _md_with_pipe_tables(1)

    assert litdown_render._splice_tables(markdown, "prose only", result) == markdown
    assert not result.failures


def test_an_unreadable_document_returns_none_rather_than_raising(tmp_path):
    broken = tmp_path / f"{STEM}.xml"
    broken.write_bytes(b"<component xmlns='http://www.wiley.com/namespaces/wiley'/>")

    assert litdown_render.convert_xml(broken) is None
