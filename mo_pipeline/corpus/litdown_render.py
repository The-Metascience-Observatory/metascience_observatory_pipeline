"""litdown as the second rung of the XML rendition ladder.

fetchpdf's converter is the first rung and stays the default: it is proven over
the renditions already on the drive and it is the only one that emits tables as
canonical HTML, so colspan and rowspan survive. But on some JATS flavours it
descends into wrappers it does not recognise and emits their leaf children one
per line -- output that is long, looks like full text, and is not.
`render.MIN_PROSE_LINE` catches that, and until now the paper simply lost its
rendition.

Measured on the 25 XMLs that have no rendition on the drive (2026-09-03), scoring
every output with `render.prose_score`:

    JATS (23)   fetchpdf 11 pass   docling 21   litdown 21 (with the unwrap below)
    Elsevier (2) fetchpdf  0 pass   docling  0   litdown  2

litdown was chosen over docling for that rung on three counts: it clears the gate
on the same JATS husks at consistently higher scores (397-602 against 140-341),
it is the only one of the three that renders Elsevier's
`full-text-retrieval-response` at all -- docling returns two characters -- and it
carries one dependency where docling carries a tree.

This module is only ever reached after the gate has refused fetchpdf's attempt,
which is what makes it safe: a file litdown also fails lands exactly where it
landed before.

Two things litdown needs help with:

* **The PMC wrapper.** litdown dispatches on the root element's local name, and
  PMC deposits arrive as `<pmc-articleset><article>...`, which it rejects with
  `ValueError: unrecognized root element`. `_unwrap_articleset` hands it the
  inner `<article>`. Five of the seven litdown failures in the trial were this.
  (The other two are Wiley's `<component>` schema, which no converter here
  reads.)

* **Tables.** litdown expands colspan/rowspan into a rectangular grid and writes
  a Markdown pipe table -- values stay under the right columns, but the span
  structure and multi-row headers flatten. `_splice_tables` puts fetchpdf's
  canonical HTML back in their place: fetchpdf's table walker is good on these
  files even when its prose walker is not, which is exactly the split this rung
  exists to exploit.
"""
from __future__ import annotations

import re
from pathlib import Path

from fetchpdf.retrieval.normalize import token_count
from fetchpdf.retrieval.to_markdown import Conversion

#: What `front_matter(extra=...)` records, so a rendition names its own converter.
try:  # pragma: no cover - absent only if litdown is not installed
    from importlib.metadata import version as _pkg_version

    CONVERTER = f"litdown-{_pkg_version('litdown')}"
except Exception:  # pragma: no cover
    CONVERTER = "litdown"

#: A run of consecutive Markdown table lines, as litdown writes them.
_PIPE_TABLE_RE = re.compile(r"^\|.*(?:\n\|.*)*", re.MULTILINE)

#: One canonical HTML table as fetchpdf emits it, inline in its Markdown.
_HTML_TABLE_RE = re.compile(r"<table>.*?</table>", re.DOTALL)


def _unwrap_articleset(raw: bytes) -> bytes:
    """The inner `<article>` of a PMC articleset, or `raw` unchanged.

    Only the root wrapper is removed; the article is handed over byte for byte,
    so nothing about the content is reinterpreted on the way.
    """
    if b"<pmc-articleset" not in raw[:2000]:
        return raw
    start = raw.find(b"<article")
    end = raw.rfind(b"</article>")
    if start < 0 or end < 0:
        return raw
    return b'<?xml version="1.0"?>' + raw[start:end + len(b"</article>")]


def _splice_tables(markdown: str, fetchpdf_markdown: str, result: Conversion) -> str:
    """Replace litdown's pipe tables with fetchpdf's canonical HTML ones.

    Both converters walk the document in reading order, so the nth table of one
    is the nth table of the other. Spliced only when the counts agree: a
    mismatch means one of them found a table the other did not, and pairing them
    by position would then put the wrong table under the wrong caption -- worse
    than the flattened grid this is trying to improve on.
    """
    html_tables = _HTML_TABLE_RE.findall(fetchpdf_markdown)
    pipe_tables = [m for m in _PIPE_TABLE_RE.finditer(markdown) if m.group(0).count("\n") >= 1]
    if not html_tables:
        return markdown
    if len(html_tables) != len(pipe_tables):
        missing = len(html_tables) - len(pipe_tables)
        result.failures.append(
            f"table counts differ (fetchpdf {len(html_tables)}, litdown {len(pipe_tables)}); "
            + (f"litdown rendered {missing} table(s) fewer, so that content is absent"
               if missing > 0 else
               "not spliced, so litdown's expanded grids keep flattened spans"))
        return markdown
    out, last = [], 0
    for match, html in zip(pipe_tables, html_tables):
        out.append(markdown[last:match.start()])
        out.append(html)
        last = match.end()
        result.n_tables += 1
        result.table_tokens += token_count(html)
    out.append(markdown[last:])
    return "".join(out)


def convert_xml(path, fetchpdf_markdown: str = "") -> Conversion | None:
    """Render publisher XML through litdown. None when litdown cannot read it.

    `fetchpdf_markdown` is the first rung's output for the same file, already in
    hand by the time this is called; its canonical HTML tables are spliced over
    litdown's grids. Passing nothing simply keeps litdown's own tables.

    Never raises: one unreadable document is one bad record, not a dead run --
    the same contract as fetchpdf's `write_markdown`.
    """
    try:
        import litdown
    except ImportError:
        return None

    try:
        markdown = litdown.convert(_unwrap_articleset(Path(path).read_bytes()))
    except Exception:
        # Unsupported roots (Wiley's <component>) and malformed documents alike.
        return None
    if not markdown.strip():
        return None

    result = Conversion(markdown="")
    result.markdown = _splice_tables(markdown, fetchpdf_markdown, result)
    result.prose_tokens = max(token_count(result.markdown) - result.table_tokens, 0)
    return result
