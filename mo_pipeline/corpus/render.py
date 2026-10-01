"""Render structured full text on the drive to Markdown, behind a prose gate.

Stage 8 reads `{stem}_from_xml.md` / `{stem}_from_html.md` as its primary full
text, unchecked -- any such file in a paper folder outranks GROBID's body.md. So
every rendition goes through one gate before it is written: stage 6 calls
`render_dirs` on the record folders it just filled, and the `render-markdown`
corpus command calls it over everything already on the drive. Nothing else
writes these files; fetchpdf's own `--to-markdown` is never passed because it
converts unconditionally.

The conversion itself is fetchpdf's (`retrieval/to_markdown.py`) -- prose becomes
Markdown, every table stays canonical HTML so colspan/rowspan survive, and a table
published as an image is marked as not machine-readable rather than dropped. This
module decides *which* files to point it at, whether to keep the result, and --
when the gate refuses it -- whether a second converter does better.

That second rung is `litdown_render`, and it only ever runs on a file the gate
has already rejected, so it cannot make any record worse than it is today. It
earns its place on the XMLs that have no rendition at all: of the 25 on the drive
on 2026-09-03, fetchpdf cleared the gate on 11 and the two rungs together on 23.

Dry-run unless `--execute`, matching `adopt-structured`.
"""
from __future__ import annotations

import os
from pathlib import Path

import statistics

import fetchpdf
from fetchpdf.retrieval.to_markdown import (
    CONVERTIBLE,
    TABLE_FORMAT_NOTE,
    Conversion,
    convert_file,
    front_matter,
    markdown_path_for,
    record_stem_for,
)

from mo_pipeline import config
from mo_pipeline.corpus import litdown_render
from mo_pipeline.corpus.models import is_doi_folder

#: Recorded in each rendition's front matter, so a file names its own converter
#: and a later regression can be traced to the rung that wrote it.
FETCHPDF = f"fetchpdf-{getattr(fetchpdf, '__version__', 'unknown')}"


#: Minimum median prose-line length for a rendition to be trusted as full text.
#: A rendition that fails this is not merely thin -- it is a file that LOOKS like
#: full text and is not, which is worse than having none, because extraction
#: would read it as the primary tier and never open the PDF.
#:
#: Calibrated on the corpus (551 XMLs, 2026-08-26): JATS renditions score 106+ at
#: the 10th percentile and PMC 563+, while renditions of Elsevier's
#: `full-text-retrieval-response` schema scored 20 at the 90th -- fetchpdf's
#: walker did not know the `ce:` tag names and shredded every paragraph into its
#: citation labels. That converter gap was fixed upstream on 2026-09-02
#: (fetchpdf 0.1.1): the same 352 convertible Elsevier XMLs then scored 177 at
#: the minimum and 720 at the median, none under 60. The gate stays as the guard
#: against the next converter regression, because the failure it catches is
#: silent. The asymmetry is deliberate: a rejected good rendition just falls
#: back to GROBID's body.md, while an accepted bad one corrupts an extraction.
MIN_PROSE_LINE = 60


def prose_score(markdown: str) -> float:
    """Median length of the prose lines in a rendition.

    The failure mode this catches: a converter that recognises only some of a
    schema's tag names descends into the wrappers it does not know and emits
    their leaf children -- citation labels, section numbers -- one per line,
    with none of the prose between them. The output is a long list of very
    short lines.
    """
    lines = [line.strip() for line in markdown.splitlines()]
    # Headings, block quotes and table markup are legitimately short; judging
    # them would penalise a table-heavy paper for being table-heavy.
    lines = [line for line in lines if line and not line.startswith(("#", ">", "|", "<"))]
    if not lines:
        return 0.0
    return statistics.median(len(line) for line in lines)


def _gate(conversion: Conversion | None) -> tuple[bool, float]:
    """(passes the prose gate, its score). False if there is nothing to judge."""
    if conversion is None or not conversion.ok or not conversion.markdown.strip():
        return False, 0.0
    score = prose_score(conversion.markdown)
    return score >= MIN_PROSE_LINE, score


def best_conversion(artifact_path: Path) -> tuple[Conversion | None, str, float]:
    """(conversion, converter, score) for one artifact, down the rungs.

    fetchpdf first, always. Only if the gate refuses its output does litdown get
    a turn, and only for XML -- litdown reads no HTML, and the HTML tier is not
    where papers are being lost. `None` means both rungs failed and the record
    keeps whatever tier it already had.

    The returned score is the best one seen, so a rejection can be reported with
    a number that means something.
    """
    try:
        first = convert_file(str(artifact_path))
    except Exception:
        first = None
    ok, score = _gate(first)
    if ok:
        return first, FETCHPDF, score

    if str(artifact_path).endswith(".xml"):
        # fetchpdf's tables are good on these files even where its prose is not,
        # so hand its markdown over for the table splice.
        second = litdown_render.convert_xml(
            artifact_path, first.markdown if first else "")
        ok, second_score = _gate(second)
        if ok:
            return second, litdown_render.CONVERTER, second_score
        score = max(score, second_score)
    return None, "", score


def write_rendition(artifact_path: Path, conversion: Conversion,
                    converter: str, verbose: bool = False) -> bool:
    """Write one gate-passing rendition beside its source, atomically.

    fetchpdf's own `write_markdown` is not used here because it re-converts the
    file it is given, which would both throw away the conversion the gate just
    judged and silently write fetchpdf's output over litdown's.
    """
    out_path = Path(markdown_path_for(str(artifact_path)))
    # Read the format off the markdown rather than assuming it: fetchpdf's
    # header hard-codes canonical-html, which is a lie for a litdown rendition
    # whose tables could not be spliced, and the note beneath it tells a reader
    # the span attributes are authoritative when they have been flattened.
    table_format = ("canonical-html" if "<table" in conversion.markdown
                    else "markdown-grid (colspan/rowspan expanded, headers flattened)"
                    if "\n|" in conversion.markdown else "none")
    header = front_matter(str(artifact_path), conversion,
                          {"converter": converter, "table_format": table_format})
    if table_format != "canonical-html":
        header = header.replace(TABLE_FORMAT_NOTE, f"Table format: {table_format}.")
    text = header + conversion.markdown
    tmp = out_path.with_name(out_path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, out_path)
    except OSError as e:
        if verbose:
            print(f"  markdown: could not write {out_path}: {e}")
        return False
    if verbose:
        print(f"  markdown: {out_path.name} via {converter} "
              f"({conversion.n_tables} tables)")
    return True


def _best_sources(directory: Path) -> dict[str, Path]:
    """{record stem -> best convertible artifact} for one directory.

    CONVERTIBLE is ordered best-source-first (.xml before .fulltext.html), so a
    record holding both renders from the publisher's own JATS rather than from
    scraped HTML. Mirrors fetchpdf's own grouping in `convert_directory`.
    """
    best: dict[str, tuple[int, Path]] = {}
    for entry in sorted(directory.iterdir()):
        if not entry.is_file() or not entry.name.endswith(CONVERTIBLE):
            continue
        rank = next(i for i, suffix in enumerate(CONVERTIBLE)
                    if entry.name.endswith(suffix))
        record = record_stem_for(str(entry))
        current = best.get(record)
        if current is None or rank < current[0]:
            best[record] = (rank, entry)
    return {record: path for record, (_rank, path) in best.items()}


def _record_folders(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [p for p in sorted(root.iterdir()) if p.is_dir() and is_doi_folder(p.name)]


def _search_dirs(papers: Path, inbox: Path) -> list[Path]:
    """Every directory that can hold artifacts.

    papers/ and inbox/ are both one folder per DOI now; the inbox root is kept
    for flat leftovers from before `inbox-subfolders`. Both trees are scanned so
    a record is covered whether or not `adopt-structured` has moved it across.
    """
    dirs = _record_folders(papers) + _record_folders(inbox)
    if inbox.is_dir():
        dirs.append(inbox)
    return dirs


def render_dirs(dirs, execute: bool = False, limit: int | None = None,
                overwrite: bool = False, verbose: bool = False) -> dict:
    """Write the missing, gate-passing rendition for each directory's best artifact.

    Dry-run unless `execute`. Returns a summary dict.
    """
    summary = {"scanned_dirs": 0, "records": 0, "pending": 0, "converted": 0,
               "already_present": 0, "failed": 0, "rejected_no_prose": 0,
               "by_converter": {}, "examples": [], "rejected_examples": []}

    for directory in dirs:
        if limit is not None and summary["pending"] >= limit:
            break
        sources = _best_sources(Path(directory))
        if not sources:
            continue
        summary["scanned_dirs"] += 1
        summary["records"] += len(sources)
        pending = [path for path in sources.values()
                   if overwrite or not Path(markdown_path_for(str(path))).exists()]
        summary["already_present"] += len(sources) - len(pending)
        if not pending:
            continue
        if limit is not None:
            pending = pending[: limit - summary["pending"]]
        summary["pending"] += len(pending)
        if len(summary["examples"]) < 5:
            summary["examples"].append(
                f"{Path(directory).name}: {[Path(markdown_path_for(str(p))).name for p in pending]}")
        if not execute:
            continue
        for path in pending:
            conversion, converter, score = best_conversion(path)
            if conversion is None:
                # Convertible-but-useless and outright-unconvertible both land
                # here on purpose: neither should leave a file on disk that
                # extraction would then read as its primary full text.
                summary["rejected_no_prose"] += 1
                if len(summary["rejected_examples"]) < 5:
                    summary["rejected_examples"].append(f"{path.name} (prose score {score:.0f})")
                if verbose:
                    print(f"  rejected: {path.name} — best prose score {score:.0f} "
                          f"< {MIN_PROSE_LINE}; leaving it to the PDF tier")
                continue
            if write_rendition(path, conversion, converter, verbose=verbose):
                summary["converted"] += 1
                summary["by_converter"][converter] = \
                    summary["by_converter"].get(converter, 0) + 1
            else:
                summary["failed"] += 1

    return summary


def render_markdown(execute: bool = False, limit: int | None = None,
                    overwrite: bool = False, verbose: bool = False,
                    papers: Path | None = None, inbox: Path | None = None) -> dict:
    """Write the missing markdown rendition for artifacts already on disk."""
    papers = papers or config.PAPERS_DIR
    inbox = inbox or config.INBOX_DIR
    return render_dirs(_search_dirs(papers, inbox), execute=execute, limit=limit,
                       overwrite=overwrite, verbose=verbose)
