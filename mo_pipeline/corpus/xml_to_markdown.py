"""
Convert Elsevier full-text-retrieval-response XML into the per-paper markdown
layout (abstract.md + body.md + references.json + provenance.json), matching
what pdf4llm produces so the catalog and downstream extract treat these papers
identically.

Used by the one-time backfill for the ~30 papers where fetch_pdf_from_doi got
Elsevier fulltext XML (via allow_xml_fallback) instead of a PDF. Elsevier's XML
is proprietary (namespaces: dc = Dublin Core for metadata, ce = common element
DTD for article structure), not JATS, so pdf4llm/docling can't parse it — hence
this focused parser.

Content sources (validated across the real files):
  title    <- dc:title           (all)
  abstract <- ce:abstract text, else dc:description   (dc:description in all 30)
  body     <- ce:section / ce:para tree as markdown headings + paragraphs (28/30);
              falls back to the abstract when the structured body is thin (old papers)
  refs     <- ce:bib-reference / sb:reference textual entries (best-effort)
"""
from __future__ import annotations

import json
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from mo_pipeline.corpus.models import doi_to_folder, folder_to_doi

_THIN_BODY = 500   # chars; below this the structured body is treated as absent


def _local(tag: str) -> str:
    return re.sub(r"\{.*\}", "", tag)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _text(el) -> str:
    return _clean("".join(el.itertext()))


def parse_elsevier_xml(path: Path) -> dict:
    """Return {title, abstract, body_md, references:[...]} from one XML file."""
    root = ET.parse(path).getroot()

    title = ""
    abstract = ""
    abstract_ce = ""
    refs: list[str] = []

    for el in root.iter():
        ln = _local(el.tag)
        if ln == "title" and not title and el.text:
            title = _clean(el.text)
        elif ln == "description" and not abstract:
            abstract = _text(el)                       # dc:description
        elif ln == "abstract" and not abstract_ce:
            # ce:abstract — prefer its simple-paras, skip the "Highlights" graphical one
            cls = (el.get("class") or "").lower()
            if "graphical" not in cls and "highlights" not in cls:
                abstract_ce = _clean(" ".join(
                    _text(p) for p in el.iter() if _local(p.tag) in ("simple-para", "para")))
        elif ln in ("bib-reference", "reference"):
            t = _text(el)
            if t:
                refs.append(t)

    if abstract_ce and len(abstract_ce) > len(abstract):
        abstract = abstract_ce

    # Body: walk ce:section (nested) -> heading; ce:para -> paragraph.
    body: list[str] = []

    def walk(el, depth: int):
        ln = _local(el.tag)
        if ln == "section-title":
            t = _text(el)
            if t:
                body.append("#" * min(depth, 4) + " " + t)
            return
        if ln == "para":
            t = _text(el)
            if len(t) > 1:
                body.append(t)
            return
        child_depth = depth + 1 if ln == "section" else depth
        for c in el:
            walk(c, child_depth)

    walk(root, 1)
    body_md = "\n\n".join(body)

    # Fallbacks for the handful of thin/old papers.
    if len(body_md) < _THIN_BODY and abstract:
        body_md = abstract
    if not abstract and body_md:
        abstract = body_md[:1500]

    return {"title": title, "abstract": abstract, "body_md": body_md, "references": refs}


def write_paper_folder(doi: str, xml_path: Path, papers_dir: Path,
                       parsed: dict | None = None, move_xml: bool = True) -> Path:
    """Create papers/{doi}/ with abstract.md/body.md/references.json/provenance.json.

    Returns the folder path. Idempotent-ish: overwrites the markdown files (cheap)
    and moves the XML in as provenance."""
    parsed = parsed or parse_elsevier_xml(xml_path)
    folder = papers_dir / doi_to_folder(doi)
    folder.mkdir(parents=True, exist_ok=True)

    title = parsed["title"] or doi
    (folder / "abstract.md").write_text(f"# {title}\n\n{parsed['abstract']}\n")
    (folder / "body.md").write_text(f"# {title}\n\n{parsed['body_md']}\n")
    (folder / "references.json").write_text(json.dumps(
        [{"raw": r} for r in parsed["references"]], indent=2))
    (folder / "provenance.json").write_text(json.dumps({
        "extraction_mode": "elsevier-xml",
        "source": "elsevier-fulltext-api",
        "thin_body": len(parsed["body_md"]) < _THIN_BODY,
        "n_references": len(parsed["references"]),
    }, indent=2))
    if move_xml:
        dest = folder / (doi_to_folder(doi) + ".xml")
        if xml_path.resolve() != dest.resolve():
            shutil.move(str(xml_path), str(dest))
    return folder


def convert_dir(xml_dir: Path, papers_dir: Path, verbose: bool = True) -> dict:
    """Convert every *.xml in xml_dir into papers_dir/{doi}/. Returns a summary."""
    xmls = sorted(xml_dir.glob("*.xml"))
    done, thin, failed = 0, 0, []
    for x in xmls:
        doi = folder_to_doi(x.stem)
        try:
            parsed = parse_elsevier_xml(x)
            if not parsed["abstract"] and not parsed["body_md"]:
                failed.append(x.name)
                continue
            write_paper_folder(doi, x, papers_dir, parsed=parsed)
            done += 1
            if len(parsed["body_md"]) < _THIN_BODY:
                thin += 1
            if verbose:
                print(f"  {doi}  abstract={len(parsed['abstract'])} "
                      f"body={len(parsed['body_md'])} refs={len(parsed['references'])}"
                      f"{'  [thin]' if len(parsed['body_md']) < _THIN_BODY else ''}", flush=True)
        except Exception as e:
            failed.append(f"{x.name}: {e}")
    return {"converted": done, "thin": thin, "failed": failed, "total": len(xmls)}
