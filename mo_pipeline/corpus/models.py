"""
Corpus data model: how a single paper folder on the PDF drive is interpreted.

The filesystem is the source of truth. A paper lives in one folder named after
its DOI (slashes encoded as ``--``). Its processing *status* is derived from
which files are present; its *findings* are read from the extraction result
JSON(s). The only thing a filesystem scan cannot recover — historical origin
(``source_batch``), stage timestamps, and the ingested record — is persisted in
a tiny per-folder ``paper.json`` passport, which makes the whole catalog
rebuildable with ``mo_pipeline.corpus.catalog scan``.

Folder layout (unchanged by this module — the user's kept invariant):

    papers/10.1001--archneurol.2010.292/
        10.1001--archneurol.2010.292.pdf      # PDF, moved in at conversion
        abstract.md  body.md                  # conversion output (pdf4llm)
        references.json  provenance.json
        replication_check.json                # optional screening verdict
        paper.json                            # NEW passport (this module)
        sonnet_v8_4/                          # one dir per extraction run (tag)
            *_result_full.json  debug_log.json
"""
from __future__ import annotations

import json
from datetime import datetime
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

# ── Processing status (ordered from least to most processed) ─────────────────
STATUS_ORDER = ["empty", "downloaded", "converted", "screened", "extracted", "ingested"]

# Files that mark each stage.
_CONVERTED_MARKERS = ("abstract.md", "body.md")
# The other route to readable full text: fetchpdf's markdown rendition of the
# publisher's XML/HTML. A paper that arrived as markup and never went through
# GROBID has no abstract.md, but extraction can read it perfectly well — so it
# counts as converted too, or `include-list --status converted` would hide it
# from the very stage that prefers it.
_RENDITION_GLOBS = ("*_from_xml.md", "*_from_html.md")
_STRUCTURED_GLOBS = ("*.xml", "*.fulltext.html")
_SCREEN_FILE = "replication_check.json"
_PASSPORT_FILE = "paper.json"
# `_result_core.json` is the single-shot core-fields extractor (extract_core.py);
# `_result.json` is `extract.py --level base`. Both count as extracted: they carry
# result + replication_type, which is all the catalog reads.
#
# Matched as `{paper folder stem}{suffix}`, never as a bare `*` glob. Both
# extractors name their output after the paper folder, so anchoring costs
# nothing -- and an unanchored `*_result.json` also matched the claim-centrality
# pilot's `centrality_result.json`, which made the catalog read a centrality run
# as an extraction and blank out the paper's real verdict. Every one of the 163
# unanchored result files on the drive was a centrality file.
_RESULT_SUFFIXES = ("_result_full.json", "_result_pdf_only.json",
                    "_result_html.json", "_result_xml.json",
                    "_result_core.json", "_result.json")


# Characters NTFS/exFAT forbid in filenames, other than '/' (encoded as '--')
# and ':' (encoded as the short, readable '~'). These almost never occur in DOIs
# — the exception is ancient Wiley SICI DOIs, e.g.
# '10.1002/1099-0879(200007)7:3<220::aid-cpp243>3.0.co;2-f' — so they get a hex
# '~XX~' token. `;`, `(`, `)` are filesystem-safe and left alone.
_FS_FORBIDDEN = '<>"\\|?*'


def doi_to_folder(doi: str) -> str:
    """'10.1001/archneurol.2010.292' -> '10.1001--archneurol.2010.292'.

    Reversible, filesystem-safe encoding (must match
    fetch_pdf_from_doi.doi_to_safe_filename, which names downloaded PDFs whose
    stem becomes the folder name):
      '/' -> '--'          DOI path separator
      ':' -> '~'           colon (common in old Springer/Kluwer DOIs)
      < > " \\ | ? * -> '~XX~'  hex-escaped (rare; Wiley SICI DOIs)
      '-' -> '~2d~'        only when part of a '--' run or adjacent to a '/',
                           where it would be ambiguous with the slash encoding
                           (e.g. ASEE '10.18260/1-2--47556'); lone hyphens stay raw
    e.g. '10.1023/a:1018769825030' -> '10.1023--a~1018769825030'."""
    s = doi.strip()
    s = re.sub(r"-+(?=/)|(?<=/)-+|-{2,}", lambda m: "~2d~" * len(m.group()), s)
    s = s.replace("/", "--")
    for ch in _FS_FORBIDDEN:
        s = s.replace(ch, f"~{ord(ch):02x}~")
    return s.replace(":", "~")


def folder_to_doi(folder_name: str) -> str:
    """Inverse of doi_to_folder: '10.1001--archneurol.2010.292' -> '10.1001/…'.

    Decode order matters: '~2d~' escaped-hyphen tokens to a sentinel, then the
    '~XX~' tokens, then every bare '~' back to ':', then every '--' back to '/',
    then the sentinel back to '-'. The '--' rule handles multi-slash DOIs (OSF,
    many 10.1093/10.1002/10.1023/10.1027 journals) e.g. '10.1093--jpepsy--jsy104'
    -> '10.1093/jpepsy/jsy104'; '~' restores colon DOIs. Single literal hyphens
    ('1015-5759') are never escaped by the encoder, so they are left untouched.
    (A DOI containing a literal '~' or a literal ':XX:' that mimics a hex token is
    not round-trippable, but such DOIs are vanishingly rare in practice.)"""
    # Strip trailing whitespace (incl. unicode) and a " (1)"-style dedup suffix.
    name = re.sub(r"\s*\(\d+\)$", "", folder_name.strip())
    name = name.replace("~2d~", "\x00")
    for ch in _FS_FORBIDDEN:
        name = name.replace(f"~{ord(ch):02x}~", ch)
    return name.replace("~", ":").replace("--", "/").replace("\x00", "-")


def folder_to_doi_url(folder_name: str) -> str:
    return "https://doi.org/" + folder_to_doi(folder_name)


def is_doi_folder(name: str) -> bool:
    return bool(re.match(r"^10\.\d+--", re.sub(r"\s*\(\d+\)$", "", name)))


@dataclass
class TagFindings:
    """Summary of one extraction run (one tag subfolder)."""
    tag: str
    ai_version: str | None = None
    contains_replications: bool | None = None
    n_replications: int = 0
    run_at: float = 0.0             # provenance.json timestamp, else file mtime
    results: list[str] = field(default_factory=list)          # per-entry result category
    replication_types: list[str] = field(default_factory=list)


@dataclass
class Paper:
    """A catalog record for one paper folder, derived from disk."""
    doi: str
    folder: str                     # absolute path
    status: str = "empty"
    contains_replications: bool | None = None
    n_replications: int = 0
    latest_tag: str | None = None
    ai_version: str | None = None
    source_batch: str | None = None
    screened_confidence: str | None = None
    ingested_db_version: str | None = None
    tags: list[str] = field(default_factory=list)
    has_pdf: bool = False
    has_structured: bool = False    # publisher XML / HTML on disk
    has_rendition: bool = False     # ...rendered to markdown, i.e. extractable

    def as_row(self) -> dict:
        d = asdict(self)
        d["tags"] = ",".join(self.tags)
        return d


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _run_timestamp(tag_dir: Path, result_path: Path) -> float:
    """When this run happened: provenance.json's timestamp, else file mtime.

    Every run since 2026-09-03 writes provenance.json; older ones do not, so the
    result file's mtime is the fallback. Both beat the previous tie-break, which
    was the alphabetical order of the tag folder names.
    """
    prov = _read_json(tag_dir / "provenance.json")
    if isinstance(prov, dict) and prov.get("timestamp"):
        try:
            return datetime.fromisoformat(str(prov["timestamp"])).timestamp()
        except ValueError:
            pass
    try:
        return result_path.stat().st_mtime
    except OSError:
        return 0.0


def _tag_findings(tag_dir: Path, stem: str) -> TagFindings | None:
    """Read the result JSON in one tag subfolder into a TagFindings.

    `stem` is the paper folder's name; result files are named after it.
    """
    result_path = None
    for suffix in _RESULT_SUFFIXES:
        candidate = tag_dir / f"{stem}{suffix}"
        if candidate.exists():
            result_path = candidate
            break
    if result_path is None:
        return None
    data = _read_json(result_path)
    if data is None:
        return TagFindings(tag=tag_dir.name)
    reps = data.get("replications", []) or []
    # First entry that carries a version, not reps[0] alone: a "no replications"
    # result has no entries at all, and a mixed batch can leave the first entry
    # unstamped, both of which used to yield ai_version=None and collapse the
    # ordering key.
    ai_version = next(
        (r.get("ai_version") for r in reps
         if isinstance(r, dict) and r.get("ai_version") is not None),
        None)
    return TagFindings(
        tag=tag_dir.name,
        run_at=_run_timestamp(tag_dir, result_path),
        ai_version=str(ai_version) if ai_version is not None else None,
        contains_replications=data.get("contains_replications"),
        n_replications=len(reps),
        results=[r.get("result") for r in reps if isinstance(r, dict) and r.get("result")],
        replication_types=[r.get("replication_type") for r in reps
                           if isinstance(r, dict) and r.get("replication_type")],
    )


def scan_folder(folder: Path) -> Paper:
    """Derive a Paper record from a single on-disk paper folder.

    Status precedence (highest present wins):
      ingested  — paper.json has an 'ingested' record
      extracted — >=1 tag subfolder with a result JSON
      screened  — replication_check.json present
      converted — abstract.md + body.md present, or a markdown rendition of the
                  publisher's XML/HTML (either is readable full text)
      downloaded— a .pdf or a raw .xml/.fulltext.html present
      empty     — none of the above
    """
    doi = folder_to_doi(folder.name)
    paper = Paper(doi=doi, folder=str(folder))

    passport = _read_json(folder / _PASSPORT_FILE) or {}
    paper.source_batch = passport.get("source_batch")
    ingested_rec = passport.get("ingested")
    if isinstance(ingested_rec, dict):
        paper.ingested_db_version = ingested_rec.get("db_version")

    paper.has_pdf = any(folder.glob("*.pdf"))
    paper.has_structured = any(any(folder.glob(g)) for g in _STRUCTURED_GLOBS)
    paper.has_rendition = any(any(folder.glob(g)) for g in _RENDITION_GLOBS)
    converted = (all((folder / m).exists() for m in _CONVERTED_MARKERS)
                 or paper.has_rendition)

    # Screening verdict (from replication_check.json), if any.
    screen = _read_json(folder / _SCREEN_FILE)
    if isinstance(screen, dict):
        paper.screened_confidence = screen.get("confidence")
        # Screen verdict is a fallback signal for contains_replications.
        if paper.contains_replications is None:
            paper.contains_replications = screen.get("contains_replications")

    # Extraction runs (tag subfolders).
    tag_findings: list[TagFindings] = []
    for sub in sorted(p for p in folder.iterdir() if p.is_dir()):
        tf = _tag_findings(sub, folder.name)
        if tf is not None:
            tag_findings.append(tf)
    paper.tags = [tf.tag for tf in tag_findings]

    if tag_findings:
        # Latest = highest ai_version, then most recent run, then most entries.
        #
        # The run timestamp is the load-bearing addition. ai_version is absent
        # from every "no replications" result and every pre-stamping run, so the
        # version tuple collapses to () and used to leave `max()` returning the
        # first element of an alphabetically sorted directory listing -- which
        # reported sonnetv5 as later than sonnetv6, and decided the verdict for
        # 670 of 1,647 multi-tag papers by folder name.
        def _key(tf: TagFindings):
            try:
                ver = tuple(int(x) for x in re.findall(r"\d+", tf.ai_version or ""))
            except Exception:
                ver = ()
            return (ver, tf.run_at, tf.n_replications)
        latest = max(tag_findings, key=_key)
        paper.latest_tag = latest.tag
        paper.ai_version = latest.ai_version
        paper.n_replications = latest.n_replications
        # Only overwrite a verdict with a verdict. A result JSON that carries no
        # `contains_replications` key must not erase one derived from screening.
        if latest.contains_replications is not None:
            paper.contains_replications = latest.contains_replications

    # Derive status.
    if paper.ingested_db_version:
        paper.status = "ingested"
    elif tag_findings:
        paper.status = "extracted"
    elif isinstance(screen, dict):
        paper.status = "screened"
    elif converted:
        paper.status = "converted"
    elif paper.has_pdf or paper.has_structured:
        paper.status = "downloaded"
    else:
        paper.status = "empty"

    return paper


def write_passport(folder: Path, *, source_batch: str | None = None,
                   ingested: dict | None = None, extra: dict | None = None) -> None:
    """Create/update the paper.json passport, merging with any existing content."""
    path = folder / _PASSPORT_FILE
    doc = _read_json(path) or {}
    doc.setdefault("doi", folder_to_doi(folder.name))
    if source_batch is not None:
        doc.setdefault("source_batch", source_batch)
    if ingested is not None:
        doc["ingested"] = ingested
    if extra:
        doc.update(extra)
    path.write_text(json.dumps(doc, indent=2))
