"""Move what stage 7 left behind in inbox/{doi}/ into papers/{doi}/.

Stage 6 writes each record into its own inbox folder -- {stem}.pdf plus the
structured copy ({stem}.xml, else {stem}.fulltext.html), its Markdown rendition
and fetchpdf's sidecars. Stage 7 moves only the PDF: pdf4llm's --movepdf takes
the file it converted and nothing else. Without this pass the XML that made the
trip stays behind in the inbox while its paper lives in papers/, which is both a
lost artifact and a growing inbox that looks like unconverted work.

Safety rule: a record with a {stem}.pdf still in the inbox is left alone. That
PDF means conversion has not run yet, and the artifacts must stay together for
it.

Where papers/{stem}/ does not exist the record is adopted anyway, provided it
holds full text of its own. That folder is normally created by stage 7 moving
the PDF across -- so a record that arrived as XML or HTML with no PDF at all had
no route into the corpus and sat in the inbox unreachable by extraction: 358 of
them on 2026-09-03. A record with no full text is still left where it is; an
empty folder is not a paper.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from mo_pipeline import config
from mo_pipeline.corpus.models import folder_to_doi, is_doi_folder

#: What counts as a record bringing its own full text, and so as worth a folder
#: of its own: the publisher's markup, or a rendition of it.
_FULLTEXT_SUFFIXES = (".xml", ".fulltext.html", "_from_xml.md", "_from_html.md")


def adopt_structured(execute: bool = False, inbox: Path | None = None,
                     papers: Path | None = None) -> dict:
    """Move each converted record's remaining inbox files into its paper folder.

    Dry-run unless `execute`. Returns a summary dict.
    """
    inbox = inbox or config.INBOX_DIR
    papers = papers or config.PAPERS_DIR
    summary = {"adopted": 0, "created": 0, "files": 0, "awaiting_conversion": 0,
               "no_paper_folder": 0, "already_in_corpus": 0, "examples": []}
    if not inbox.exists():
        return summary

    for record in sorted(inbox.iterdir()):
        if not record.is_dir() or not is_doi_folder(record.name):
            continue
        stem = record.name
        if (record / f"{stem}.pdf").exists():
            # Not converted yet: stage 7 still needs this record intact.
            summary["awaiting_conversion"] += 1
            continue
        files = sorted(record.iterdir())
        target = papers / stem
        creating = not target.is_dir()
        if creating and not any(f.name.endswith(_FULLTEXT_SUFFIXES) for f in files):
            # Sidecars alone (provenance, metadata) are not a paper.
            summary["no_paper_folder"] += 1
            continue
        if not files:
            if execute:
                record.rmdir()
            continue
        summary["adopted"] += 1
        summary["created"] += int(creating)
        summary["files"] += len(files)
        if len(summary["examples"]) < 5:
            summary["examples"].append(f"{stem}: {[f.name for f in files]}")
        if not execute:
            continue
        if creating:
            target.mkdir(parents=True, exist_ok=True)
            # The name encodes the DOI, but only one decoder is authoritative
            # (invariant 4), and `repair-names --check` verifies this file
            # round-trips to the folder it sits in.
            (target / "doi.txt").write_text(folder_to_doi(stem) + "\n",
                                            encoding="utf-8")
        for f in files:
            dest = target / f.name
            if dest.exists():
                # Never overwrite what the corpus already holds -- a backfill
                # run may have written straight into the paper folder. Counted
                # so the leftover inbox copy is visible rather than silently
                # permanent.
                summary["already_in_corpus"] += 1
                summary["files"] -= 1
                continue
            shutil.move(str(f), str(dest))
        try:
            record.rmdir()
        except OSError:
            pass  # a leftover copy keeps the folder; it shows up in the summary
    return summary
