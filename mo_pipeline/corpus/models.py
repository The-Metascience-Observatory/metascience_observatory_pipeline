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
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

# ── Processing status (ordered from least to most processed) ─────────────────
STATUS_ORDER = ["empty", "downloaded", "converted", "screened", "extracted", "ingested"]

# Files that mark each stage.
_CONVERTED_MARKERS = ("abstract.md", "body.md")
_SCREEN_FILE = "replication_check.json"
_PASSPORT_FILE = "paper.json"
_RESULT_GLOBS = ("*_result_full.json", "*_result_pdf_only.json",
                 "*_result_html.json", "*_result_xml.json")


def doi_to_folder(doi: str) -> str:
    """'10.1001/archneurol.2010.292' -> '10.1001--archneurol.2010.292'."""
    return doi.strip().replace("/", "--")


def folder_to_doi(folder_name: str) -> str:
    """'10.1001--archneurol.2010.292' -> '10.1001/archneurol.2010.292'.

    Only the FIRST '--' is the DOI slash; DOIs can legitimately contain '--'
    after that, so we split on the registrant prefix (10.NNNN)."""
    name = folder_name
    # Strip a trailing " (1)"-style dedup suffix if present.
    name = re.sub(r"\s*\(\d+\)$", "", name)
    m = re.match(r"^(10\.\d+)--(.+)$", name)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    # Fallback: replace all '--'.
    return name.replace("--", "/")


def is_doi_folder(name: str) -> bool:
    return bool(re.match(r"^10\.\d+--", re.sub(r"\s*\(\d+\)$", "", name)))


@dataclass
class TagFindings:
    """Summary of one extraction run (one tag subfolder)."""
    tag: str
    ai_version: str | None = None
    contains_replications: bool | None = None
    n_replications: int = 0
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

    def as_row(self) -> dict:
        d = asdict(self)
        d["tags"] = ",".join(self.tags)
        return d


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _tag_findings(tag_dir: Path) -> TagFindings | None:
    """Read the result JSON in one tag subfolder into a TagFindings."""
    result_path = None
    for g in _RESULT_GLOBS:
        hits = sorted(tag_dir.glob(g))
        if hits:
            result_path = hits[0]
            break
    if result_path is None:
        return None
    data = _read_json(result_path)
    if data is None:
        return TagFindings(tag=tag_dir.name)
    reps = data.get("replications", []) or []
    ai_version = None
    if reps and isinstance(reps[0], dict):
        ai_version = reps[0].get("ai_version")
    return TagFindings(
        tag=tag_dir.name,
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
      converted — abstract.md + body.md present
      downloaded— a .pdf present
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
    converted = all((folder / m).exists() for m in _CONVERTED_MARKERS)

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
        tf = _tag_findings(sub)
        if tf is not None:
            tag_findings.append(tf)
    paper.tags = [tf.tag for tf in tag_findings]

    if tag_findings:
        # Latest = highest ai_version, then most result entries.
        def _key(tf: TagFindings):
            try:
                ver = tuple(int(x) for x in re.findall(r"\d+", tf.ai_version or ""))
            except Exception:
                ver = ()
            return (ver, tf.n_replications)
        latest = max(tag_findings, key=_key)
        paper.latest_tag = latest.tag
        paper.ai_version = latest.ai_version
        paper.contains_replications = latest.contains_replications
        paper.n_replications = latest.n_replications

    # Derive status.
    if paper.ingested_db_version:
        paper.status = "ingested"
    elif tag_findings:
        paper.status = "extracted"
    elif isinstance(screen, dict):
        paper.status = "screened"
    elif converted:
        paper.status = "converted"
    elif paper.has_pdf:
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
