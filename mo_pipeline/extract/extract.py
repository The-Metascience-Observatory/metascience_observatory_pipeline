#!/usr/bin/env python3
"""
Replication data extractor for The Metascience Observatory.

Runs an agentic CLI (claude, or codex) over each paper folder to extract its
replication records. The folder's full text is read through a tier ladder
(`paper_artifacts`): XML/HTML rendition > GROBID body.md > PDF.

The claude agent writes result.json into each paper's directory.
The full claude output (including reasoning) is saved as debug_log.json.

Usage:
    # Single paper
    python extract.py papers/10.1234_some-paper/

    # Batch: all papers in a directory
    python extract.py papers/ --batch

    # Batch with parallel workers
    python extract.py papers/ --batch --workers 4

    # Codex CLI instead of the Claude CLI (set --model explicitly)
    python extract.py papers/ --batch --usecodex --model <codex-model>
"""

import argparse
import csv
import json
import logging
import re
import signal
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from mo_pipeline.shared.fetch_metadata_from_doi import (
    _new_authors_are_better, fetch_metadata_from_doi, format_initials,
)
from mo_pipeline.shared.fetch_metadata_from_title import (
    _title_similarity as _shared_title_similarity, fetch_metadata_from_title)
from mo_pipeline import config as _cfg
from mo_pipeline.corpus.models import RESULT_SUFFIXES, folder_to_doi, folder_to_doi_url
from mo_pipeline.discover.screening_backend import parse_json_reply, primary_model

logger = logging.getLogger(__name__)

# Global flag for graceful shutdown
_shutdown_requested = False
_running_processes = {}  # thread_id -> subprocess.Popen

def signal_handler(signum, frame):
    """Handle Ctrl-C by setting shutdown flag and stopping running Claude instances."""
    global _shutdown_requested
    if not _shutdown_requested:
        _shutdown_requested = True
        print("\n\nShutdown requested. Stopping current papers gracefully...", file=sys.stderr)
        # Send SIGINT to all running Claude processes to stop token generation
        import threading
        current_thread = threading.current_thread().ident
        for thread_id, process in list(_running_processes.items()):
            if thread_id != current_thread and process.poll() is None:
                try:
                    process.send_signal(signal.SIGINT)
                    print(f"  Sent stop signal to running process (thread {thread_id})", file=sys.stderr)
                except Exception:
                    pass
    else:
        print("\n\nForce quit requested. Exiting immediately.", file=sys.stderr)
        sys.exit(1)


class SkipPaper(Exception):
    """Raised when a paper should be skipped (e.g., already in dataset)."""


# Prompt and website paths come from the unified config.
PROMPT_DIR = _cfg.PROMPTS_DIR
PROMPT_FILES = _cfg.PROMPT_FILES
# Shared content (schema, field reference, replication type definitions,
# result classification, discipline list, confidence, edge cases) lives in
# a single file and is appended to every mode-specific prompt at load time.
# This prevents drift — updates to shared rules happen in one place and
# propagate to all modes automatically.
PROMPT_SHARED_CORE = _cfg.PROMPT_SHARED_CORE
ONTOLOGY_PATH = _cfg.ONTOLOGY_PATH
PROMPT_VERSION_FILE = _cfg.EXTRACTOR_VERSION_FILE

# --- Validation constants ---
VALID_RESULTS = {"success", "failure", "inconclusive", "reversal"}
VALID_REPLICATION_TYPES = {"direct", "close experiment", "close extension", "conceptual"}
VALID_CONFIDENCE = {"low", "medium", "high"}
VALID_P_VALUE_TYPES = {"<", "=", ">"}

# The 14 statistical fields. The stat-free renderings (extract_core.py, and
# --level base here) strip these from every entry; collate still emits their
# columns blank so the ingestor sees an unchanged CSV shape.
STAT_FIELDS = (
    "original_n", "original_es", "original_es_type", "original_es_95_CI",
    "original_p_value", "original_p_value_type", "original_p_value_tails",
    "replication_n", "replication_es", "replication_es_type", "replication_es_95_CI",
    "replication_p_value", "replication_p_value_type", "replication_p_value_tails",
)

# Result file each agentic prompt level writes (the core extractor writes
# _result_core.json). Collate priority lives in corpus.models.RESULT_SUFFIXES.
_RESULT_SUFFIX_FOR_LEVEL = {"base": "_result.json", "full": "_result_full.json",
                            "html": "_result_html.json", "pdf_only": "_result_pdf_only.json"}
def _load_ontology() -> dict[str, list[str]]:
    """Load the canonical topic ontology and flatten to discipline → subdisciplines.

    The canonical JSON is a 2-level hierarchy (top-level group → discipline →
    subdiscipline list); downstream code here uses a 1-level discipline map, so
    we drop the top-level grouping.
    """
    with open(ONTOLOGY_PATH) as f:
        raw = json.load(f)
    flat: dict[str, list[str]] = {}
    for _group, disciplines in raw.items():
        for disc, subs in disciplines.items():
            flat[disc] = list(subs)
    return flat


VALID_DISCIPLINES = _load_ontology()
_SUBDISCIPLINE_TO_DISCIPLINE = {
    sub: disc for disc, subs in VALID_DISCIPLINES.items() for sub in subs
}
_ALL_SUBDISCIPLINES = set(_SUBDISCIPLINE_TO_DISCIPLINE)


def _render_discipline_block() -> str:
    """Render the ontology as `discipline [sub1, sub2, ...]` lines for the prompt."""
    lines = []
    for disc, subs in VALID_DISCIPLINES.items():
        if subs:
            lines.append(f"{disc} [{', '.join(subs)}]")
        else:
            lines.append(disc)
    return "\n".join(lines)


# Prompt files carry HTML-comment mode blocks so ONE shared core serves both
# the statistics-bearing prompts and the stat-free ones (invariant 3):
#
#     <!-- mode:full -->   kept only when rendering mode "full"   <!-- /mode -->
#     <!-- mode:core -->   kept only when rendering mode "core"   <!-- /mode -->
#
# Markers sit on their own lines, never nest, and are always removed, so the
# rendered "full" prompt is byte-identical to the file minus its marker lines.
_MODE_OPEN = re.compile(r"^\s*<!--\s*mode:([a-z_]+)\s*-->\s*$")
_MODE_CLOSE = re.compile(r"^\s*<!--\s*/mode\s*-->\s*$")
# A rendering is selected by a SET of tags, along two independent axes:
#   statistics      "full" (record them) vs "core" (never record them)
#   output channel  "write" (agentic: save result.json with the Write tool)
#                   vs "reply" (single-shot: answer with the JSON inline)
# They are independent, and conflating them is what silently lost two papers:
# `--level base` is stat-free AND agentic, so it needs core+write. Anything not
# listed renders as the normal full agentic mode.
_TAGS_FOR_LEVEL = {
    "full":     frozenset({"full", "write"}),
    "pdf_only": frozenset({"full", "write"}),
    "html":     frozenset({"full", "write"}),
    "xml":      frozenset({"full", "write"}),
    "base":     frozenset({"core", "write"}),
    "core":     frozenset({"core", "reply"}),
}
_DEFAULT_TAGS = frozenset({"full", "write"})
# The levels whose prompt omits the statistics, so their rows stay identifiable
# in the database as "<prompt version>-<level>".
STAT_FREE_LEVELS = frozenset({"base", "core"})


def _render_mode(text: str, tags) -> str:
    """Keep `<!-- mode:X -->` blocks whose X is an active tag, strip all markers."""
    keep = frozenset({tags} if isinstance(tags, str) else tags)
    out: list[str] = []
    active: str | None = None
    for line in text.splitlines(keepends=True):
        m = _MODE_OPEN.match(line)
        if m:
            if active is not None:
                raise ValueError(f"nested <!-- mode:{m.group(1)} --> inside mode:{active}")
            active = m.group(1)
            continue
        if _MODE_CLOSE.match(line):
            if active is None:
                raise ValueError("<!-- /mode --> without an open block")
            active = None
            continue
        if active is None or active in keep:
            out.append(line)
    if active is not None:
        raise ValueError(f"unclosed <!-- mode:{active} --> block")
    rendered = "".join(out)
    if "core" in keep:
        # Dropping the trailing stat keys of a JSON example leaves `...,\n}`.
        # A trailing comma is never valid there, so collapsing it is always right.
        rendered = re.sub(r",([ \t]*\n[ \t]*\})", r"\1", rendered)
    return rendered


def load_system_prompt(level: str = "full") -> str:
    """Load the system prompt for a given mode level.

    Concatenates the mode-specific workflow prompt (title, intro, file
    structure, workflow passes) with the shared core (output schema, field
    reference, replication type, result classification, discipline list,
    confidence, edge cases). The mode file comes first so the model reads
    input-handling instructions before the shared schema/definitions.

    "full" (and the pdf_only/html/xml variants) render the statistics blocks.
    "base" renders prompt_full.md and "core" renders prompt_core.md with the
    statistics blocks removed -- see _render_mode.
    """
    tags = _TAGS_FOR_LEVEL.get(level, _DEFAULT_TAGS)
    mode_text = _render_mode(PROMPT_FILES[level].read_text(), tags)
    shared_text = _render_mode(PROMPT_SHARED_CORE.read_text(), tags)
    shared_text = shared_text.replace("{{DISCIPLINE_LIST}}", _render_discipline_block())
    return mode_text.rstrip() + "\n\n" + shared_text


# ── The artifact tier ladder ─────────────────────────────────────────────────
# A paper folder can hold the same text in up to three renditions. They are not
# equal: the publisher's own JATS keeps table structure and cannot suffer the
# glyph corruption a broken PDF ToUnicode CMap causes, scraped publisher HTML is
# the same idea one step down in trust, and GROBID's markdown is a reconstruction
# of the PDF. Highest present tier is the PRIMARY the agent reads first; the rest
# stay in the folder as named fallbacks, because tables published as images and
# bibliographies GROBID mangled are only recoverable from the PDF.
#
# Ordered best-first. Globs, not names: the stem is the encoded DOI.
FULLTEXT_TIERS = (
    ("xml", "*_from_xml.md", "publisher XML (JATS), rendered to Markdown"),
    ("html", "*_from_html.md", "publisher HTML, rendered to Markdown"),
    ("grobid", "body.md", "GROBID reconstruction of the PDF"),
)


def _first_match(paper_dir: Path, pattern: str) -> Path | None:
    """The single file matching `pattern`, or None. Deterministic when several."""
    matches = sorted(paper_dir.glob(pattern))
    return matches[0] if matches else None


def paper_artifacts(paper_dir: Path, force_tier: str | None = None) -> dict:
    """Inventory one paper folder against the tier ladder.

    `force_tier` ("xml" | "html" | "grobid" | "pdf") restricts the ladder to that
    rung for tier-paired benchmark runs; a paper lacking the forced tier falls
    through to the PDF (the benchmark harness only forces tiers on papers that
    have them).

    Returns {primary_tier, primary, fallbacks, supporting, pdf, has_fulltext}.
    `primary` is the best full text present; `fallbacks` are the lower tiers that
    are also on disk. Used by BOTH extract_paper and extract_batch so discovery
    and validation can never disagree about what is extractable.
    """
    tiers = [(tier, path, label)
             for tier, pattern, label in FULLTEXT_TIERS
             if (path := _first_match(paper_dir, pattern)) is not None]
    if force_tier == "pdf":
        tiers = []
    elif force_tier:
        tiers = [t for t in tiers if t[0] == force_tier]

    supporting = [paper_dir / name for name in
                  ("abstract.md", "references.json", "tables.md", "metadata.json")
                  if (paper_dir / name).exists()]
    pdf = _first_match(paper_dir, "*.pdf")

    # The raw markup, kept addressable for ONE reason: fetchpdf's converter walks
    # JATS <body> only, and a JATS bibliography lives in <back><ref-list>. So the
    # rendition -- excellent for prose and tables -- has no reference list at
    # all, while the .xml beside it does. Verified against the converter.
    structured_raw = (_first_match(paper_dir, "*.xml")
                      or _first_match(paper_dir, "*.fulltext.html"))

    # Last rung. Every converter can fail on the same document -- Wiley's
    # <component> schema defeats all of ours -- and for a record that arrived as
    # markup with no PDF (429 such folders on 2026-09-03) that leaves the paper
    # with no full text at all while a complete article sits right there. Raw
    # markup runs about 3.7x the tokens of its rendition, so it is strictly a
    # last resort: offered only when no tier and no PDF exist. Never substituted
    # under --force-tier, which exists to hold a benchmark to one rung.
    if not tiers and pdf is None and structured_raw is not None and force_tier is None:
        tiers = [("raw_xml", structured_raw,
                  "the publisher's raw markup — no rendition could be made of "
                  "this paper and there is no PDF")]

    return {
        "primary_tier": tiers[0][0] if tiers else None,
        "primary": tiers[0][1] if tiers else None,
        "primary_label": tiers[0][2] if tiers else None,
        "fallbacks": [(tier, path, label) for tier, path, label in tiers[1:]],
        "supporting": supporting,
        "structured_raw": structured_raw,
        "pdf": pdf,
        # A PDF alone is full text too -- the agent can read it directly, which
        # is exactly what --onlypdf does. It just sits at the bottom of the
        # ladder, so it does not count as a *tier* above.
        "has_fulltext": bool(tiers) or pdf is not None,
    }


#: Converted-text lengths below which a folder is suspected of holding no
#: article. Under _HUSK_NO_PDF_CHARS there is less text than an abstract, so
#: nothing can be extracted whatever the PDF says; between the two, a one-page
#: PDF is required to confirm it.
_HUSK_MAX_CHARS = 2000
_HUSK_NO_PDF_CHARS = 500


def _husk_reason(paper_dir: Path, art: dict) -> str:
    """Why this folder holds no article, or "" when it plausibly does.

    Stage 5 has occasionally stored the wrong file under a DOI (a publisher
    advertisement, a cover page). Conversion converts whatever it is handed, so
    the folder looks extractable. Deliberately conservative, because refusing a
    real paper costs more than one bad negative: it fires only when the
    converted text is too short to be an article AND a single-page PDF confirms
    there was never more, or when the text is below the length of an abstract
    and so cannot support any extraction at all. A genuine two-page research
    letter passes. The benchmark harness makes the same call from the other
    side, in benchmarking/harness.py `_document_identity`.
    """
    if art.get("primary_tier") not in (None, "grobid"):
        return ""                      # a publisher rendition means real markup arrived
    primary = art.get("primary")
    try:
        n_chars = len(primary.read_text(errors="replace").strip()) if primary else 0
    except OSError:
        return ""
    if n_chars >= _HUSK_MAX_CHARS:
        return ""
    if n_chars < _HUSK_NO_PDF_CHARS:
        return f"only {n_chars} characters of converted text: shorter than an abstract"
    pdf = art.get("pdf")
    if pdf is None:
        return ""                      # short, but nothing corroborates a husk
    try:
        import fitz
        with fitz.open(str(pdf)) as doc:
            pages = doc.page_count
    except Exception:
        return ""                      # cannot tell: let the agent try
    if pages <= 1:
        return f"a {pages}-page PDF and only {n_chars} characters of converted text"
    return ""


def _describe_artifacts(paper_dir: Path, art: dict) -> str:
    """The file inventory the agent is handed, primary first, fallbacks gated.

    Deliberately not a flat list of everything in the folder: three renditions of
    one paper invite three full reads. Each line says what the file is and, for
    the lower tiers, when it is worth opening.
    """
    if art["primary"] is None:
        # No tier at all, only the PDF. Not an error -- a paper downloaded but
        # never converted lands here, and the agent can read a PDF directly.
        return (
            f"PRIMARY full text: {art['pdf'].name} — the PDF itself; this folder "
            f"has no markdown rendition. Read it with the Read tool's `pages` "
            f"parameter a few pages at a time, never all at once."
            + ("\nSupporting: " + ", ".join(p.name for p in art["supporting"])
               if art["supporting"] else "")
        )

    lines = [f"PRIMARY full text: {art['primary'].name} — {art['primary_label']}."]
    if art["primary_tier"] == "raw_xml":
        lines.append(
            "  It is XML, not Markdown: read the text inside the tags and ignore "
            "the markup. Tables are in the markup with their spans intact, and "
            "unlike a rendition this file DOES carry the full reference list "
            "(JATS `<back><ref-list>`, Elsevier `<ce:bibliography>`), so the "
            "original study's bibliographic details are in here.")
    if art["primary_tier"] in ("xml", "html"):
        note = (
            "  Tables in it are HTML, not Markdown (colspan/rowspan survive that "
            "way and do not survive Markdown). A table the publisher shipped as "
            "an image is marked `[table not machine-readable — published as an "
            "image]`"
        )
        # Only promise a PDF fallback for those tables when there is a PDF.
        note += ("; that marker is your cue to open the PDF for those numbers."
                 if art["pdf"] else ", and those numbers are unrecoverable here — "
                                    "record them as missing rather than guessing.")
        lines.append(note)

    if art["supporting"]:
        names = [p.name for p in art["supporting"]]
        line = "Supporting: " + ", ".join(names)
        if "references.json" in names:
            line += " (references.json is GROBID's structured bibliography)"
        lines.append(line + ".")

    # Where the reference list actually is. The renditions of publisher XML do
    # not carry one, so saying nothing here would send the agent hunting through
    # a file that structurally cannot contain what it wants.
    if art["primary_tier"] == "xml":
        sources = []
        if any(p.name == "references.json" for p in art["supporting"]):
            sources.append("`references.json`")
        if art["structured_raw"]:
            sources.append(f"the `<ref-list>` in `{art['structured_raw'].name}` "
                           f"(raw markup — grep it for the author surname)")
        if art["pdf"]:
            sources.append(f"the References section of `{art['pdf'].name}`")
        lines.append(
            "IMPORTANT — the primary has NO reference list: the rendition covers "
            "the article body only, and a JATS bibliography sits outside it. Take "
            "the citation sentence from the primary, then get the original study's "
            "bibliographic details from " + (", then ".join(sources) if sources
                                             else "the paper's own inline citations")
            + "."
        )

    for _tier, path, label in art["fallbacks"]:
        lines.append(
            f"Lower-tier copy of the same text: {path.name} — {label}. Read it "
            f"only if the primary is empty, truncated, or garbled."
        )

    if art["pdf"]:
        lines.append(
            f"PDF: {art['pdf'].name}. Lowest tier — it may carry OCR/glyph errors, "
            f"so never let it override the primary. Open it only to (a) read a "
            f"table the primary marks as an image, (b) recover bibliographic "
            f"details when references.json is empty, corrupt, or missing the "
            f"target entry, or (c) find a statistic that is genuinely absent above."
        )
    return "\n".join(lines)


def _format_duration(seconds: float) -> str:
    """Format a duration in seconds as 'Xh YYm ZZs', dropping leading zero components."""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{seconds:.1f}s"


def load_version_number() -> str:
    """Load the prompt version from version.txt, verbatim (e.g. "8.9", "8.10").

    Kept as text: parsing it as a float turned "8.10" into 8.1 (sorting below
    8.9) and "9.0" into 9. Returns "0" if the file is missing or malformed.
    """
    try:
        raw = PROMPT_VERSION_FILE.read_text().strip()
    except FileNotFoundError as e:
        logger.warning(f"Failed to load version number from {PROMPT_VERSION_FILE}: {e}")
        return "0"
    if not re.fullmatch(r"\d+(\.\d+)*", raw):
        logger.warning(f"Malformed version number in {PROMPT_VERSION_FILE}: {raw!r}")
        return "0"
    return raw


def normalize_doi_url(url: str) -> str:
    """Normalize DOI URL to lowercase https form for comparison."""
    url = url.strip().lower()
    url = url.replace("http://doi.org/", "https://doi.org/")
    url = url.replace("http://dx.doi.org/", "https://doi.org/")
    url = url.replace("https://dx.doi.org/", "https://doi.org/")
    return url


def load_existing_replication_urls() -> set[str]:
    """https://doi.org/ URLs of every replication in the published database.

    Lowercased, in the form normalize_doi_url gives a paper folder's DOI, so a
    paper already in the database is skipped. Empty (with a warning) when no
    database resolves -- then nothing is skipped.
    """
    from mo_pipeline.shared.production_db import published_dois
    dois, path = published_dois()
    if path is None:
        print("Warning: no published replications database found, skipping duplicate check",
              file=sys.stderr)
        return set()
    print(f"Loaded {len(dois)} existing replication DOIs from {path.name}", file=sys.stderr)
    return {f"https://doi.org/{d}" for d in dois}


def _extract_doi_from_url(url: str) -> str | None:
    """Extract bare DOI from a DOI URL like 'https://doi.org/10.1234/foo'."""
    if not url:
        return None
    m = re.match(r'https?://(?:dx\.)?doi\.org/(10\..+)', url.strip())
    return m.group(1) if m else None



def _title_similarity(a: str, b: str, threshold: float = 0.55) -> float:
    """Similarity of two titles, or 0.0 when they are not plausibly the same work.

    Delegates to the shared implementation rather than a bare SequenceMatcher
    ratio, because it also accepts the case a metadata API creates constantly:
    a record carrying only the main title of a paper whose full title has a
    subtitle. A plain ratio scores that pair around 0.4, and every caller here
    compares against 0.55, so a correct DOI was being discarded.
    """
    return _shared_title_similarity(a, b, threshold=threshold)


def _normalize_enum(rep: dict, field: str, valid, prefix: str, msgs: list[str],
                    aliases: dict[str, str] | None = None) -> None:
    """Lowercase an enum value, map a known alias, or warn that it is not valid."""
    val = rep.get(field, "")
    if not val:
        return
    low = val.lower()
    if aliases and low in aliases:
        rep[field] = aliases[low]
        msgs.append(f"{prefix} {field}=\"{low}\" → \"{aliases[low]}\"")
    elif low in valid and val != low:
        msgs.append(f"{prefix} {field}=\"{val}\" → \"{low}\"")
        rep[field] = low
    elif low not in valid:
        msgs.append(f"{prefix} {field}=\"{val}\" not in {sorted(valid)}")


def _check_discipline(rep: dict, prefix: str, msgs: list[str]) -> None:
    """Canonicalize discipline case, lift a subdiscipline given as the discipline,
    and check the subdiscipline belongs to the discipline."""
    disc = rep.get("discipline", "")
    if disc and disc.lower() != "other":
        disc_lower = disc.lower()
        if disc_lower in {d.lower() for d in VALID_DISCIPLINES}:
            canonical = next(d for d in VALID_DISCIPLINES if d.lower() == disc_lower)
            if disc != canonical:
                msgs.append(f"{prefix} discipline=\"{disc}\" → \"{canonical}\"")
                rep["discipline"] = canonical
        elif disc_lower in {s.lower() for s in _ALL_SUBDISCIPLINES}:
            canonical_sub = next(s for s in _ALL_SUBDISCIPLINES if s.lower() == disc_lower)
            parent = _SUBDISCIPLINE_TO_DISCIPLINE[canonical_sub]
            rep["discipline"] = parent
            if not rep.get("subdiscipline"):
                rep["subdiscipline"] = canonical_sub
            msgs.append(f"{prefix} discipline=\"{disc}\" → \"{parent}\" (was subdiscipline)")
        else:
            msgs.append(f"{prefix} discipline=\"{disc}\" not in valid list")

    subdisc = rep.get("subdiscipline", "")
    current_disc = rep.get("discipline", "")
    if subdisc and subdisc.lower() != "other" and current_disc in VALID_DISCIPLINES:
        valid_subs = {s.lower() for s in VALID_DISCIPLINES[current_disc]}
        if subdisc.lower() not in valid_subs:
            msgs.append(f"{prefix} subdiscipline=\"{subdisc}\" not in {current_disc} list")


def _check_statistics(rep: dict, prefix: str, msgs: list[str]) -> None:
    """n fields coerced to int, p-values in [0, 1], known p-value types, and
    every effect size paired with its type."""
    for n_field in ("original_n", "replication_n"):
        val = rep.get(n_field, "")
        if val != "" and val is not None:
            try:
                n_int = int(float(str(val)))
                if n_int <= 0:
                    msgs.append(f"{prefix} {n_field}={val} should be positive")
                elif val != n_int:
                    rep[n_field] = n_int  # silent: "120" -> 120 is benign
            except (ValueError, TypeError):
                msgs.append(f"{prefix} {n_field}=\"{val}\" is not a valid integer")

    for pv_field in ("original_p_value", "replication_p_value"):
        val = rep.get(pv_field, "")
        if val != "" and val is not None:
            try:
                pv = float(str(val))
                if pv < 0 or pv > 1:
                    msgs.append(f"{prefix} {pv_field}={pv} outside [0, 1]")
            except (ValueError, TypeError):
                msgs.append(f"{prefix} {pv_field}=\"{val}\" is not a valid number")

    for pvt_field in ("original_p_value_type", "replication_p_value_type"):
        val = rep.get(pvt_field, "")
        if val and val not in VALID_P_VALUE_TYPES:
            msgs.append(f"{prefix} {pvt_field}=\"{val}\" not in {sorted(VALID_P_VALUE_TYPES)}")

    for side in ("original", "replication"):
        es = rep.get(f"{side}_es", "")
        es_type = rep.get(f"{side}_es_type", "")
        if es and not es_type:
            msgs.append(f"{prefix} {side}_es={es} but {side}_es_type is empty")
        elif es_type and not es:
            msgs.append(f"{prefix} {side}_es_type=\"{es_type}\" but {side}_es is empty")


def validate_extraction(data: dict) -> tuple[dict, list[str]]:
    """Validate and auto-correct extracted data. Returns (data, log_messages).

    Checks enum fields, required fields, statistical types, and structural consistency.
    Auto-corrects where safe (case normalization, known aliases). Logs warnings for issues.
    """
    msgs = []
    has_reps = data.get("contains_replications", False)
    reps = data.get("replications", [])
    if has_reps and not reps:
        msgs.append("  ⚠️  SANITY: contains_replications=true but replications array is empty")
    if not has_reps and reps:
        msgs.append("  ⚠️  SANITY: contains_replications=false but replications array has entries")

    for i, rep in enumerate(reps):
        prefix = f"  ⚠️  SANITY entry {i}:"
        _normalize_enum(rep, "result", VALID_RESULTS, prefix, msgs)
        _normalize_enum(rep, "replication_type", VALID_REPLICATION_TYPES, prefix, msgs,
                        aliases={"close": "close experiment"})
        _normalize_enum(rep, "confidence", VALID_CONFIDENCE, prefix, msgs,
                        aliases={"moderate": "medium"})
        _check_discipline(rep, prefix, msgs)

        for field in ("description", "result", "confidence", "discipline", "replication_type"):
            if not rep.get(field):
                msgs.append(f"{prefix} missing required field \"{field}\"")
        if not rep.get("citation_sentence"):
            msgs.append(f"{prefix} missing citation_sentence")

        # original_url normalization is silent: always correct, never surprising.
        if rep.get("original_url"):
            rep["original_url"] = normalize_doi_url(rep["original_url"])

        _check_statistics(rep, prefix, msgs)

    return data, msgs


def format_authors_string(authors_str: str) -> str:
    """Format semicolon-separated authors, adding periods after initials."""
    if not authors_str or not isinstance(authors_str, str):
        return authors_str
    return '; '.join(format_initials(a.strip()) for a in authors_str.split(';'))


def _fill_metadata_fields(entry: dict, prefix: str, meta: dict) -> int:
    """Fill empty {prefix}_journal/volume/issue/pages/year from API metadata.

    Also fills title (if empty) and upgrades authors (if API has better names).
    Returns number of fields filled.
    """
    filled = 0
    for meta_key, entry_key in [
        ("journal", f"{prefix}_journal"), ("volume", f"{prefix}_volume"),
        ("issue", f"{prefix}_issue"), ("pages", f"{prefix}_pages"),
        ("year", f"{prefix}_year"),
    ]:
        if not entry.get(entry_key) and meta.get(meta_key):
            val = meta[meta_key]
            if meta_key == "year":
                try:
                    y = int(float(val))
                    if not (1800 <= y <= 2030):
                        continue
                    val = y
                except (ValueError, TypeError):
                    continue
            entry[entry_key] = val
            filled += 1
    # Title: only fill if empty (Claude's extracted title is usually fine)
    if not entry.get(f"{prefix}_title") and meta.get("title"):
        entry[f"{prefix}_title"] = meta["title"]
        filled += 1
    # Authors: upgrade if API has better (more complete) names
    if meta.get("authors"):
        formatted = format_authors_string(meta["authors"])
        existing = entry.get(f"{prefix}_authors", "")
        if not existing or _new_authors_are_better(existing, formatted):
            entry[f"{prefix}_authors"] = formatted
            filled += 1
    return filled


def validate_original_dois(
    data: dict, email: str = "dan@metascienceobservatory.org"
) -> tuple[list[dict], dict[str, dict]]:
    """Validate original_url DOIs by fetching metadata and comparing titles.

    Returns (mismatches, doi_metadata_cache):
      mismatches: [{"entry_idx": 0, "doi": "10.1234/foo", "agent_title": "...", ...}, ...]
      doi_metadata_cache: {doi_string: metadata_dict_or_None, ...}
    """
    mismatches = []
    seen_dois = {}  # cache DOI -> metadata to avoid duplicate API calls

    for i, rep in enumerate(data.get("replications", [])):
        original_url = rep.get("original_url", "")
        doi = _extract_doi_from_url(original_url)
        if not doi:
            continue

        # Fetch metadata (with cache for repeated DOIs across entries)
        if doi not in seen_dois:
            try:
                seen_dois[doi] = fetch_metadata_from_doi(doi, email=email, delay=0.1)
            except Exception as e:
                logger.warning(f"DOI validation failed for {doi}: {e}")
                seen_dois[doi] = None

        meta = seen_dois[doi]

        agent_title = rep.get("original_title", "")
        if not agent_title or not meta or not meta.get("title"):
            continue

        api_title = meta["title"]
        sim = _title_similarity(agent_title, api_title)

        if sim < 0.55:
            mismatches.append({
                "entry_idx": i,
                "doi": doi,
                "agent_title": agent_title,
                "api_title": api_title,
                "api_authors": meta.get("authors", ""),
                "api_year": meta.get("year"),
                "similarity": round(sim, 2),
            })

    return mismatches, seen_dois


def _extract_last_names(orig_authors: str) -> list[str]:
    """Last names from a semicolon-separated author string, in either order.

    Both conventions occur and must work: the prompt asks for "Smith, John" but
    metadata enrichment rewrites authors into "John Smith", and every external
    ground-truth set uses the latter. Taking the text before the comma handles
    only the first, and returned the whole name for the second -- so the
    citation cross-check below could not find the author in the sentence and
    flagged a mismatch that was not one, which then cost a review round.
    """
    names = []
    for author in (orig_authors or "").split(";"):
        author = author.strip()
        if not author:
            continue
        # "Smith, John" -> before the comma; "John A. Smith" -> the final token.
        last = author.split(",")[0].strip() if "," in author else author.split()[-1]
        last = last.strip(".").strip()
        if len(last) > 1:
            names.append(last)
    return names


def _fold(text: str) -> str:
    """Lowercase and strip diacritics, so "Acemoglu" matches "Acemoğlu"."""
    return "".join(c for c in unicodedata.normalize("NFKD", (text or "").lower())
                   if not unicodedata.combining(c))


def _reference_matches(ref: dict, last_names: list[str], year: str) -> bool:
    """Does this bibliography entry look like the study an extracted row names?

    Reads the entry's whole text rather than its parsed `year` and `authors`
    fields. GROBID's parse of the corpus is thin -- a sampled median of 4
    references per paper, with 88% of papers having no parsed year on any entry
    -- and the Elsevier path writes {"raw": "..."} carrying no fields at all.
    Requiring parsed fields therefore made this corroboration impossible for
    most papers, which silently turned it from a safety net into dead code: the
    citation check then flagged mismatches it could have cleared, and every one
    of those buys a review round that can change a label. The year and the
    surname are almost always present in the entry's text even when GROBID did
    not split them out.
    """
    parts: list[str] = []
    for value in ref.values():
        if isinstance(value, (str, int, float)):
            parts.append(str(value))
        elif isinstance(value, list):
            parts.extend(str(v) for v in value)
    blob = _fold(" ".join(parts))
    if not blob:
        return False
    if year and str(year) not in blob:
        return False
    return any(_fold(n) in blob for n in last_names)


def validate_citation_sentences(data: dict, references: list[dict] | None = None) -> list[dict]:
    """Check that citation_sentence mentions the same author/year as extracted original.

    Returns list of mismatches where neither the citation sentence nor the paper's
    references.json entries corroborate the extracted original_authors / original_year,
    suggesting the wrong original study was identified.

    The citation_sentence field holds a narrative snippet from paper prose (see
    prompt_shared_core.md), so substring-matching against it alone produces false
    positives whenever the surrounding sentence doesn't repeat the formal citation.
    When a references.json is available, we use it as a secondary corroboration:
    finding a matching bibliography entry is strong evidence the extracted original
    is correct even if the prose sentence reads differently.
    """
    mismatches = []
    for i, rep in enumerate(data.get("replications", [])):
        citation = rep.get("citation_sentence", "")
        if not citation:
            continue

        orig_authors = rep.get("original_authors", "")
        orig_year = rep.get("original_year", "")

        last_names = _extract_last_names(orig_authors)
        author_found = any(name.lower() in citation.lower() for name in last_names)
        year_found = bool(orig_year and str(orig_year) in citation)

        if author_found and year_found:
            continue

        # Sentence check failed — try to corroborate via references.json
        if references and last_names and orig_year:
            if any(_reference_matches(ref, last_names, str(orig_year)) for ref in references):
                continue

        mismatches.append({
            "entry_idx": i,
            "citation": citation[:200],
            "extracted_authors": orig_authors,
            "extracted_year": orig_year,
            "author_found": author_found,
            "year_found": year_found,
        })

    return mismatches


def enrich_metadata(
    data: dict,
    replication_doi: str,
    original_doi_cache: dict[str, dict],
    email: str = "dan@metascienceobservatory.org",
) -> tuple[dict, list[str]]:
    """Enrich result data with metadata from APIs.

    Performs:
      A. Replication paper metadata (from replication_doi — one API call per paper)
      B. Original study metadata enrichment (reuse cache + title search for missing DOIs)
      C. Author name formatting (add periods after initials)

    Modifies data in-place. Returns (data, log_messages).
    All API failures are logged but never raise — enrichment is best-effort.
    """
    msgs = []

    # A. Fetch replication paper metadata (shared across all entries in this paper)
    if replication_doi:
        try:
            rep_meta = fetch_metadata_from_doi(replication_doi, email=email, delay=0.1)
            if rep_meta:
                if rep_meta.get("authors"):
                    rep_meta["authors"] = format_authors_string(rep_meta["authors"])
                data["replication_metadata"] = rep_meta
                title_snippet = (rep_meta.get("title") or "?")[:60]
                year = rep_meta.get("year", "?")
                msgs.append(f"  📖  Replication: \"{title_snippet}\" ({year})")
            else:
                msgs.append(f"  !  Replication DOI {replication_doi}: no metadata returned")
        except Exception as e:
            msgs.append(f"  !  Replication DOI {replication_doi} fetch failed: {e}")

    # B. Enrich original study metadata per entry
    for i, rep in enumerate(data.get("replications", [])):
        original_url = rep.get("original_url", "")
        doi = _extract_doi_from_url(original_url)

        if doi:
            # B1. Have DOI — use cache from validate_original_dois, or fetch if missing
            meta = original_doi_cache.get(doi)
            if meta is None and doi not in original_doi_cache:
                try:
                    meta = fetch_metadata_from_doi(doi, email=email, delay=0.1)
                    original_doi_cache[doi] = meta
                except Exception as e:
                    logger.warning(f"Enrichment fetch failed for original DOI {doi}: {e}")
                    meta = None

            if meta:
                filled = _fill_metadata_fields(rep, "original", meta)
                if filled:
                    msgs.append(f"  +  Entry {i} original: enriched {filled} field(s) from DOI")

        elif not original_url and rep.get("original_title"):
            # B2. No DOI but have title — try title search
            try:
                meta = fetch_metadata_from_title(
                    rep["original_title"],
                    email=email,
                    authors=rep.get("original_authors"),
                    year=rep.get("original_year"),
                )
                if meta and meta.get("doi"):
                    # Sanity check: title similarity
                    api_title = meta.get("title", "")
                    if api_title and _title_similarity(rep["original_title"], api_title) >= 0.55:
                        rep["original_url"] = f"https://doi.org/{meta['doi']}"
                        filled = _fill_metadata_fields(rep, "original", meta)
                        msgs.append(
                            f"  +  Entry {i}: found DOI {meta['doi']} from title "
                            f"({filled} field(s) enriched)"
                        )
                    else:
                        logger.debug(f"Entry {i}: title search DOI failed similarity check")
                elif meta and meta.get("pmid"):
                    api_title = meta.get("title", "")
                    if api_title and _title_similarity(rep["original_title"], api_title) >= 0.55:
                        rep["original_url"] = f"https://pubmed.ncbi.nlm.nih.gov/{meta['pmid']}/"
                        filled = _fill_metadata_fields(rep, "original", meta)
                        msgs.append(
                            f"  +  Entry {i}: found PMID {meta['pmid']} from title "
                            f"({filled} field(s) enriched)"
                        )
            except Exception as e:
                logger.warning(f"Title search failed for entry {i}: {e}")

        # C. Format author names (add periods after initials)
        if rep.get("original_authors"):
            rep["original_authors"] = format_authors_string(rep["original_authors"])

    return data, msgs


_PROV_STATIC: dict = {}


def _provenance_static() -> dict:
    """CLI version, git commit, prompt hashes — computed once per process."""
    if _PROV_STATIC:
        return _PROV_STATIC
    import hashlib
    def sha(p: Path) -> str:
        try:
            return hashlib.sha256(Path(p).read_bytes()).hexdigest()
        except OSError:
            return ""
    def run(cmd: list[str]) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                                  cwd=_cfg.REPO_ROOT).stdout.strip()
        except Exception:
            return ""
    from mo_pipeline import version as _version
    _PROV_STATIC.update({
        "cli_version": run(["claude", "--version"]),
        # pipeline_version, code_fingerprint, git commit and dirty flags. The
        # fingerprint is what identifies a run made on a dirty tree, where the
        # commit describes code that is not what ran. See mo_pipeline/version.py.
        **_version.manifest(),
        "prompt_version": str(load_version_number()),
        "prompt_sha256": {"prompt_shared_core.md": sha(_cfg.PROMPT_SHARED_CORE),
                          **{p.name: sha(p) for p in _cfg.PROMPT_FILES.values()}},
    })
    return _PROV_STATIC


def _write_provenance(output_dir: Path, *, model_id: str, prompt_level: str, artifacts: dict | None,
                      html_mode: bool, pdf_only: bool, force_tier: str | None, dontcheck: bool, cli_name: str = "claude") -> None:
    st = _provenance_static()
    if artifacts and artifacts.get("primary_tier"):
        tier, primary = artifacts["primary_tier"], artifacts["primary"].name
    elif html_mode:
        tier, primary = "html", ""
    else:
        tier, primary = "pdf", ""
    cli_version = st["cli_version"]
    if cli_name != "claude":
        cli_version = subprocess.check_output([cli_name, "--version"], text=True, timeout=10).strip()
    mode_file = _cfg.PROMPT_FILES.get(prompt_level)
    prov = {
        "model_id": model_id,
        "prompt_version": st["prompt_version"],
        "prompt_level": prompt_level,
        "prompt_files_used": ["prompt_shared_core.md"] + ([mode_file.name] if mode_file else []),
        "prompt_sha256": st["prompt_sha256"],
        "primary_tier": tier, "primary_file": primary, "force_tier": force_tier,
        "cli_name": cli_name, "cli_version": cli_version, "git_commit": st["git_commit"],
        "git_dirty_prompts": st["git_dirty_prompts"],
        "git_dirty_pipeline": st["git_dirty_pipeline"],
        "pipeline_version": st["pipeline_version"],
        "code_fingerprint": st["code_fingerprint"], "dontcheck": dontcheck,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        (output_dir / "provenance.json").write_text(json.dumps(prov, indent=2))
    except OSError as e:
        logger.warning(f"could not write provenance.json: {e}")


def _run_cli(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    """Run an agent CLI to completion: (returncode, stdout, stderr).

    The process is registered in _running_processes for the duration, so a
    Ctrl+C can forward the signal to it. On timeout it is killed and
    subprocess.TimeoutExpired propagates.
    """
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    thread_id = threading.current_thread().ident
    _running_processes[thread_id] = process
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise
        return process.returncode, stdout, stderr
    finally:
        _running_processes.pop(thread_id, None)


def _first_pass_prompt(paper_dir: Path, output_dir: Path, *, html_mode: bool, pdf_only: bool,
                       force_tier: str | None) -> tuple[str, dict | None]:
    """(user prompt, artifact inventory) for the first pass; raises FileNotFoundError
    when the folder holds nothing the selected mode can read.

    There is no XML auto-detect: the default path reads every tier present in
    the folder, so an XML-only paper is an ordinary paper with a shorter
    ladder. --html and --onlypdf remain as explicit single-format overrides for
    corpora that only ever had the one file. The inventory is None for those.
    """
    save = f"Save your result to {output_dir}/result.json"
    if html_mode:
        html_files = list(paper_dir.glob("*.html"))
        if not html_files:
            raise FileNotFoundError(f"Missing HTML file in {paper_dir}")
        html_file = html_files[0]
        image_files = sorted(list(paper_dir.glob("*.png")) + list(paper_dir.glob("*.jpg"))
                             + list(paper_dir.glob("*.jpeg")))
        if not image_files:
            return (f"Extract replication data from the paper in: {paper_dir}\n"
                    f"The paper is available as an HTML file: {html_file.name}\n"
                    f"Read {paper_dir}/{html_file.name} and extract all replication data.\n"
                    f"{save}"), None
        image_list = "\n".join(f"  - {img.name}" for img in image_files)
        return (f"Extract replication data from the paper in: {paper_dir}\n"
                f"The paper is available as an HTML file: {html_file.name}\n"
                f"The following image files contain figures, tables, and diagrams:\n{image_list}\n\n"
                f"Read {paper_dir}/{html_file.name} and ALL the image files above to extract complete replication data.\n"
                f"The images often contain critical statistical information (effect sizes, p-values, sample sizes, graphs).\n"
                f"{save}"), None

    if pdf_only:
        pdf_files = list(paper_dir.glob("*.pdf"))
        if not pdf_files:
            raise FileNotFoundError(f"Missing PDF file in {paper_dir}")
        return (f"Extract replication data from the paper in: {paper_dir}\n"
                f"The paper is available as a PDF file: {pdf_files[0].name}\n"
                f"Start by reading the first few pages (1-3) of {paper_dir}/{pdf_files[0].name} using the Read tool with the pages parameter.\n"
                f"{save}"), None

    # Default mode: whatever tiers this folder has; the only hard requirement
    # is that SOMETHING readable is present.
    artifacts = paper_artifacts(paper_dir, force_tier=force_tier)
    if not artifacts["has_fulltext"]:
        raise FileNotFoundError(
            f"No readable full text in {paper_dir} "
            f"(expected a *_from_xml.md / *_from_html.md rendition, a body.md, or a PDF)")
    husk = _husk_reason(paper_dir, artifacts)
    if husk:
        # Refusing beats extracting: the agent would read the husk, find no
        # replications, and that confident negative is indistinguishable
        # downstream from a real paper that has none.
        raise FileNotFoundError(f"No article text in {paper_dir}: {husk}")
    # The folder holds up to three renditions of the same paper. Naming one
    # primary and gating the rest is what keeps the agent from reading all of
    # them; the prompt file explains the ladder, this says which rungs THIS
    # paper actually has.
    return (f"Extract replication data from the paper in: {paper_dir}\n"
            f"{_describe_artifacts(paper_dir, artifacts)}\n"
            f"{save}"), artifacts


def _agent_command(paper_dir: Path, model: str, system_prompt: str, user_prompt: str,
                   use_codex: bool) -> tuple[list[str], str]:
    """(argv, cli name) for the first-pass agent."""
    if use_codex:
        from mo_pipeline.extract.codex_backend import build_command
        return build_command(paper_dir, model, system_prompt, user_prompt), "codex"
    return [
        "claude",
        "--print",
        "--output-format", "json",
        "--model", model,
        "--max-turns", "40",
        "--system-prompt", system_prompt,
        "--allowedTools", "Read", "Grep", "Glob", "Write",
        "--add-dir", str(paper_dir),
        "--dangerously-skip-permissions",
        user_prompt,
    ], "claude"


def _usage(cli_output: dict, model_id: str, wall_time_ms: int) -> dict:
    """Token, cost and timing figures from a CLI JSON envelope."""
    u = cli_output.get("usage", {})
    return {
        "model": model_id,
        "input_tokens": u.get("input_tokens", 0),
        "output_tokens": u.get("output_tokens", 0),
        "cache_creation_tokens": u.get("cache_creation_input_tokens", 0),
        "cache_read_tokens": u.get("cache_read_input_tokens", 0),
        "cost_usd": cli_output.get("total_cost_usd", 0),
        "duration_ms": cli_output.get("duration_ms", 0),
        "wall_time_ms": wall_time_ms,
        "num_turns": cli_output.get("num_turns", 0),
    }


def _read_result_json(result_path: Path, cli_output: dict, paper_dir: Path,
                      log_messages: list[str]) -> dict:
    """The result.json the agent wrote, salvaged from its reply if it answered
    inline instead (that is not a lost paper)."""
    if not result_path.exists():
        salvaged = salvage_inline_result(cli_output.get("result", ""))
        if salvaged is None:
            raise RuntimeError(
                f"Agent did not write result.json for {paper_dir}.\n"
                f"Agent output: {cli_output.get('result', '')[:500]}")
        result_path.write_text(json.dumps(salvaged, indent=2))
        log_messages.append("  ⚠️  agent replied inline instead of writing result.json — salvaged from the reply")
    # Retry the parse briefly: the file can be read before the agent's write is flushed.
    for attempt in range(3):
        try:
            return json.loads(result_path.read_text())
        except json.JSONDecodeError:
            if attempt < 2:
                time.sleep(1)
    raise RuntimeError(f"Agent wrote invalid JSON to {result_path}:\n{result_path.read_text()[:500]}")


def _stamp(data: dict, replication_url: str, ai_version: str) -> None:
    """Set this paper's DOI URL and the extractor version on every entry."""
    for rep in data.get("replications", []):
        rep["replication_url"] = replication_url
        rep["ai_version"] = ai_version


def _load_references(paper_dir: Path):
    try:
        return json.loads((paper_dir / "references.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _check_entries(data: dict, paper_dir: Path, log_messages: list[str]):
    """Run the post-extraction validators.

    Returns (data, low/medium-confidence entries, DOI mismatches, citation
    mismatches, original-DOI metadata cache). Anything flagged is what the
    review round re-examines.
    """
    data, sanity_msgs = validate_extraction(data)
    log_messages.extend(sanity_msgs)

    doi_mismatches, original_doi_cache = [], {}
    if not _shutdown_requested:
        doi_mismatches, original_doi_cache = validate_original_dois(data)
        for mm in doi_mismatches:
            log_messages.append(
                f"  ❌  DOI mismatch entry {mm['entry_idx']} (similarity={mm['similarity']}) "
                f"— agent: \"{mm['agent_title'][:60]}\" vs API: \"{mm['api_title'][:60]}\"")

    # references.json lets the validator corroborate authors/year through the
    # bibliography, so a narrative citation_sentence is not a false positive.
    citation_mismatches = validate_citation_sentences(data, _load_references(paper_dir))
    for cm in citation_mismatches:
        parts = []
        if not cm["author_found"]:
            parts.append("author not found in citation")
        if not cm["year_found"]:
            parts.append("year not found in citation")
        log_messages.append(
            f"  ❌  Citation mismatch entry {cm['entry_idx']} ({', '.join(parts)}) "
            f"— citation: \"{cm['citation'][:80]}\" vs extracted: "
            f"\"{cm['extracted_authors'][:40]}\" ({cm['extracted_year']})")

    low_med_entries = [
        (i, rep.get("confidence", "").lower(), rep.get("result", "unknown"),
         rep.get("description", "")[:80])
        for i, rep in enumerate(data.get("replications", []))
        if rep.get("confidence", "").lower() in ("low", "medium")
    ]
    return data, low_med_entries, doi_mismatches, citation_mismatches, original_doi_cache


def _review_prompt(paper_dir: Path, output_dir: Path, low_med_entries, doi_mismatches,
                   citation_mismatches) -> str:
    """Instructions for the independent reviewer that re-examines flagged entries."""
    pdf_files = list(paper_dir.glob("*.pdf"))
    pdf_name = pdf_files[0].name if pdf_files else "the PDF"
    files_list = ", ".join(sorted(f.name for f in paper_dir.iterdir()
                                  if f.is_file() and f.suffix in (".md", ".json", ".pdf")))
    prompt_parts = [
        f"You are an independent reviewer for The Metascience Observatory. "
        f"A previous agent extracted replication data from a paper and saved it to {output_dir}/result.json. "
        f"Your job is to critically review flagged entries — NOT to rubber-stamp them.\n",
        f"The paper directory is: {paper_dir}\n"
        f"Available files: {files_list}\n",
    ]

    if low_med_entries:
        entry_details = "\n".join(
            f"  - Entry {idx} (confidence: {conf}, result: {res}): {desc}"
            for idx, conf, res, desc in low_med_entries
        )
        prompt_parts.append(
            f"ENTRIES TO REVIEW (flagged as low/medium confidence):\n{entry_details}\n\n"
            f"For each flagged entry:\n"
            f"1. Read {output_dir}/result.json to see the full entry including the explanation\n"
            f"2. Read the paper's body.md Discussion/Conclusion sections and the PDF ({paper_dir}/{pdf_name}) "
            f"to independently verify the result classification and other fields\n"
            f"3. Make your own determination:\n"
            f"   - If you find clear evidence that resolves the ambiguity, update the entry and set confidence to 'high'\n"
            f"   - If the ambiguity is GENUINE (the paper itself is unclear, the authors don't state a clear conclusion, "
            f"or reasonable people could disagree), KEEP confidence as 'medium' or 'low' — this is the honest answer\n"
            f"   - Update the explanation field to describe what you found in your review\n"
            f"   - If you disagree with the result classification, change it\n\n"
            f"Result rules you must apply when reconsidering a label (they are the same "
            f"rules the first pass was given):\n"
            f"   - A global claim of support (\"supports\", \"largely verified\", \"bolstered\") does NOT "
            f"override an abstract or conclusion that also reports \"differing results\", \"some "
            f"differences\", \"partially\", \"mixed\": that combination is inconclusive.\n"
            f"   - Change 'inconclusive' to 'success' only if EVERY sub-measure named in the entry's "
            f"description replicated. If only some did, narrow the description instead of widening "
            f"the label.\n"
            f"   - Partial support is inconclusive, not success. A single significant result does not "
            f"settle a multi-measure entry.\n"
            f"   - If you change a result label, leave confidence at 'medium': a changed label is by "
            f"definition a case the evidence did not make obvious.\n\n"
            f"IMPORTANT: Upgrading to 'high' requires finding specific new evidence. "
            f"Simply re-reading and agreeing is NOT sufficient grounds for upgrading. "
            f"Keeping medium/low is a valid and expected outcome when ambiguity is real.\n"
        )

    if doi_mismatches:
        mismatch_details = "\n".join(
            f"  - Entry {mm['entry_idx']}: DOI {mm['doi']} — "
            f"extracted title: \"{mm['agent_title']}\" but the DOI resolves to "
            f"title: \"{mm['api_title']}\" by {mm['api_authors'] or 'unknown authors'} ({mm['api_year'] or '?'}). "
            f"Title similarity: {mm['similarity']}"
            for mm in doi_mismatches
        )
        prompt_parts.append(
            f"DOI MISMATCHES — the original_url DOI does not match the original_title:\n"
            f"{mismatch_details}\n"
            f"For each mismatch, check references.json and the PDF to determine:\n"
            f"  a) The DOI is wrong — find the correct DOI or clear original_url to \"\"\n"
            f"  b) The title is wrong — update original_title to match what the DOI points to\n"
            f"  c) The API returned wrong metadata (false positive) — keep as-is if you verify the DOI is correct\n"
        )

    if citation_mismatches:
        citation_details = "\n".join(
            f"  - Entry {cm['entry_idx']}: citation_sentence says \"{cm['citation']}\" "
            f"but extracted original is \"{cm['extracted_authors']}\" ({cm['extracted_year']}). "
            f"{'Author not found in citation. ' if not cm['author_found'] else ''}"
            f"{'Year not found in citation.' if not cm['year_found'] else ''}"
            for cm in citation_mismatches
        )
        prompt_parts.append(
            f"CITATION MISMATCHES — the citation_sentence does not mention the extracted original author/year:\n"
            f"{citation_details}\n"
            f"For each mismatch, re-read the Introduction to find the correct replication target, "
            f"search references.json for the matching reference, and update original_title/authors/year/url "
            f"to match the study actually named in the citation sentence.\n"
        )

    prompt_parts.append(
        f"After your review, write the updated result to {output_dir}/result.json (preserving all fields)."
    )
    return "\n".join(prompt_parts)


def _review_round(*, paper_dir: Path, output_dir: Path, result_path: Path, model: str,
                  data: dict, usage: dict, debug_log: dict, debug_path: Path,
                  low_med_entries, doi_mismatches, citation_mismatches,
                  replication_url: str, ai_version: str, log_messages: list[str]) -> dict:
    """A fresh agent re-examines the flagged entries and rewrites result.json.

    Returns the reviewed data, or `data` unchanged when the review fails or
    times out. Adds the reviewer's cost to `usage` and its narration to the
    debug log, so a label flipped in review stays visible afterwards.
    """
    reasons = []
    if low_med_entries:
        reasons.append(f"{len(low_med_entries)} low/medium confidence")
    if doi_mismatches:
        reasons.append(f"{len(doi_mismatches)} DOI mismatches")
    if citation_mismatches:
        reasons.append(f"{len(citation_mismatches)} citation mismatches")
    log_messages.append(f"  ⚠️  {', '.join(reasons)} — starting review round")
    for idx, conf, res, desc in low_med_entries:
        log_messages.append(f"      Entry {idx}: [{conf}] result={res} — {desc}")

    refine_cmd = [
        "claude",
        "--print",
        "--output-format", "json",
        # Same model as the first pass: without this the reviewer ran on the
        # CLI default, so a run's provenance did not describe what reviewed it.
        "--model", model,
        "--max-turns", "20",
        "--allowedTools", "Read", "Grep", "Glob", "Write",
        "--dangerously-skip-permissions",
        "-p", _review_prompt(paper_dir, output_dir, low_med_entries, doi_mismatches,
                             citation_mismatches),
    ]
    refine_start = time.monotonic()
    try:
        refine_returncode, refine_stdout, _ = _run_cli(refine_cmd, timeout=300)
    except subprocess.TimeoutExpired:
        log_messages.append("  ⚠️  Review timed out")
        refine_returncode, refine_stdout = -1, ""
    refine_wall = time.monotonic() - refine_start

    if refine_returncode != 0:
        log_messages.append(f"  ⚠️  Review failed (exit {refine_returncode}), keeping original")
        return data
    try:
        refined = json.loads(result_path.read_text())
    except (json.JSONDecodeError, FileNotFoundError):
        log_messages.append("  ⚠️  Review produced invalid result, keeping original")
        return data

    flagged = {idx for idx, _, _, _ in low_med_entries}
    upgraded = sum(1 for i, rep in enumerate(refined.get("replications", []))
                   if rep.get("confidence", "").lower() == "high" and i in flagged)
    _stamp(refined, replication_url, ai_version)
    refined, post_review_msgs = validate_extraction(refined)
    log_messages.extend(post_review_msgs)

    kept = len(low_med_entries) - upgraded
    parts = []
    if low_med_entries:
        parts.append(f"{upgraded} upgraded, {kept} kept" if kept else f"{upgraded} upgraded")
    if doi_mismatches:
        parts.append(f"{len(doi_mismatches)} DOI(s) reviewed")
    log_messages.append(f"  ✅  Review done: {', '.join(parts)} ({refine_wall:.1f}s)")

    try:
        refine_output = json.loads(refine_stdout)
    except json.JSONDecodeError:
        return refined
    debug_log["review"] = {
        "model": model,
        "reasons": {"low_medium": len(low_med_entries),
                    "doi_mismatches": len(doi_mismatches),
                    "citation_mismatches": len(citation_mismatches)},
        "entries_reviewed": [{"index": idx, "confidence_before": conf,
                              "result_before": res, "description": desc}
                             for idx, conf, res, desc in low_med_entries],
        "upgraded_to_high": upgraded,
        "assistant_text": refine_output.get("result", "")[:20000],
        "wall_sec": round(refine_wall, 1),
    }
    debug_path.write_text(json.dumps(debug_log, indent=2))
    usage["input_tokens"] += refine_output.get("usage", {}).get("input_tokens", 0)
    usage["output_tokens"] += refine_output.get("usage", {}).get("output_tokens", 0)
    usage["cost_usd"] += refine_output.get("total_cost_usd", 0)
    usage["num_turns"] += refine_output.get("num_turns", 0)
    usage["wall_time_ms"] += int(refine_wall * 1000)
    usage["refinement_round"] = True
    return refined


def extract_paper(
    paper_dir: Path,
    model: str = "sonnet",
    level: str = "base",
    existing_urls: set[str] | None = None,
    tag: str | None = None,
    use_codex: bool = False,
    pdf_only: bool = False,
    html_mode: bool = False,
    force_tier: str | None = None,
) -> tuple[dict, dict, list[str]]:
    """Run the selected agent CLI against a single paper directory.

    The agent writes result.json into paper_dir (or paper_dir/tag if tag is
    provided); the validated result is saved as {folder}{suffix} beside it.
    Returns (result_dict, usage_dict, log_messages). Raises SkipPaper if the
    paper already has a result or is already in the dataset.
    """
    paper_dir = paper_dir.resolve()
    log_messages = []  # Collected for caller to print with batch prefix
    output_dir = paper_dir / tag if tag else paper_dir
    output_dir.mkdir(exist_ok=True)
    prompt_level = "html" if html_mode else "pdf_only" if pdf_only else level

    # Skip if any agentic result already exists, whatever mode wrote it (this is
    # what makes a re-run resume). A core result does not count: the agentic
    # extractor is its benchmark control arm.
    for existing_suffix in RESULT_SUFFIXES:
        if existing_suffix == "_result_core.json":
            continue
        existing_path = output_dir / f"{paper_dir.name}{existing_suffix}"
        if existing_path.exists():
            raise SkipPaper(f"Output already exists: {existing_path.name}")
    doi_url = normalize_doi_url(folder_to_doi_url(paper_dir.name))
    if existing_urls and doi_url in existing_urls:
        raise SkipPaper(f"Already in dataset: {doi_url}")

    # ---- first pass ----
    user_prompt, artifacts = _first_pass_prompt(paper_dir, output_dir, html_mode=html_mode,
                                                pdf_only=pdf_only, force_tier=force_tier)
    if artifacts is not None:
        log_messages.append(f"  tier: {artifacts['primary_tier'] or 'pdf'}")
    system_prompt = load_system_prompt(level=prompt_level)
    cmd, cli_name = _agent_command(paper_dir, model, system_prompt, user_prompt, use_codex)

    # A killed earlier run can leave result.json behind; if this agent then
    # exits 0 without writing, the stale file would be read as its output.
    result_path = output_dir / "result.json"
    result_path.unlink(missing_ok=True)

    start = time.monotonic()
    try:
        returncode, stdout, stderr = _run_cli(cmd, timeout=600)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Timeout after 10 minutes processing {paper_dir}")
    wall_time_ms = int((time.monotonic() - start) * 1000)
    if returncode != 0:
        combined = ((stderr or "") + (stdout or "")).strip()
        raise RuntimeError(f"{cli_name} CLI failed for {paper_dir}:\n{combined}")

    try:
        if use_codex:
            from mo_pipeline.extract.codex_backend import parse_events
            cli_output = parse_events(stdout)
        else:
            cli_output = json.loads(stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"Failed to parse {cli_name} CLI output for {paper_dir}:\n{stdout[:500]}")

    model_usage = cli_output.get("modelUsage", {})
    model_id = primary_model(model_usage, model)
    usage = _usage(cli_output, model_id, wall_time_ms)

    # Full output, reasoning included, as a debug log.
    debug_log = {
        "model": model_id,
        "modelUsage": model_usage,
        "assistant_text": cli_output.get("result", ""),
        "usage": usage,
        "session_id": cli_output.get("session_id", ""),
    }
    debug_path = output_dir / "debug_log.json"
    debug_path.write_text(json.dumps(debug_log, indent=2))

    # Provenance sidecar: everything a benchmark needs to say WHAT produced this
    # result (model, prompt bytes, input tier, CLI, commit). The result JSON
    # itself carries only ai_version; debug_log.json only the model. The
    # benchmark harness reads this file (benchmarking/harness.py:load_extraction).
    _write_provenance(output_dir, model_id=model_id, prompt_level=prompt_level,
                      artifacts=artifacts, html_mode=html_mode, pdf_only=pdf_only,
                      force_tier=force_tier, dontcheck=existing_urls is None, cli_name=cli_name)

    data = _read_result_json(result_path, cli_output, paper_dir, log_messages)
    replication_url = folder_to_doi_url(paper_dir.name)
    ai_version = load_version_number()
    if prompt_level in STAT_FREE_LEVELS:
        # Stat-free rendering of the same prompt family: keep its rows
        # distinguishable in the database ("8.7-base").
        ai_version = f"{ai_version}-{prompt_level}"
    _stamp(data, replication_url, ai_version)

    # ---- validation, then a review round for anything flagged ----
    data, low_med_entries, doi_mismatches, citation_mismatches, original_doi_cache = \
        _check_entries(data, paper_dir, log_messages)
    needs_review = low_med_entries or doi_mismatches or citation_mismatches
    if needs_review and not use_codex and not _shutdown_requested:
        data = _review_round(
            paper_dir=paper_dir, output_dir=output_dir, result_path=result_path, model=model,
            data=data, usage=usage, debug_log=debug_log, debug_path=debug_path,
            low_med_entries=low_med_entries, doi_mismatches=doi_mismatches,
            citation_mismatches=citation_mismatches, replication_url=replication_url,
            ai_version=ai_version, log_messages=log_messages)
    elif needs_review:
        # Logged without review (Codex has no review round; or a shutdown is pending).
        if low_med_entries:
            log_messages.append(f"  ⚠️  {len(low_med_entries)} entries with low/medium confidence (no review)")
            for idx, conf, res, desc in low_med_entries:
                log_messages.append(f"      Entry {idx}: [{conf}] result={res} — {desc}")
        if doi_mismatches:
            log_messages.append(f"  ❌  {len(doi_mismatches)} DOI mismatches (no review)")

    # ---- metadata enrichment, on the final data ----
    if not _shutdown_requested:
        try:
            data, enrich_msgs = enrich_metadata(
                data,
                replication_doi=_extract_doi_from_url(replication_url) or "",
                original_doi_cache=original_doi_cache,
            )
            log_messages.extend(enrich_msgs)
        except Exception as e:
            log_messages.append(f"  !  Metadata enrichment failed: {e}")

    # Rename result.json to {folder_name}{suffix}. _result_xml.json is no longer
    # produced -- the default multi-format path writes _result_full.json whatever
    # tier it read -- but historical files with that name are still recognized.
    final_path = output_dir / f"{paper_dir.name}{_RESULT_SUFFIX_FOR_LEVEL[prompt_level]}"
    if prompt_level in STAT_FREE_LEVELS:
        for rep in data.get("replications", []):
            for field in STAT_FIELDS:
                rep.pop(field, None)
    final_path.write_text(json.dumps(data, indent=2))
    result_path.unlink()

    return data, usage, log_messages


def salvage_inline_result(reply_text: str) -> dict | None:
    """The result envelope from an agent reply that carried the JSON inline
    (fenced or bare) instead of writing result.json; None if the reply holds
    no usable envelope."""
    data = parse_json_reply(reply_text or "")
    if not isinstance(data, dict) or "replications" not in data:
        return None
    if not isinstance(data.get("replications"), list):
        return None
    data.setdefault("contains_replications", bool(data["replications"]))
    return data


def is_usage_limit_error(error_text: str) -> bool:
    """Check if error is due to API usage limits or Claude Code session limits."""
    usage_limit_indicators = [
        "usage limit",
        "rate limit",
        "quota exceeded",
        "too many requests",
        "overloaded_error",
        "usage_limit_reached",
        "claude code session",
        "429",  # HTTP status code for rate limiting
    ]
    if any(indicator in error_text.lower() for indicator in usage_limit_indicators):
        return True
    # Silent exit: CLI exited non-zero with no message — session limit signature.
    # Other failures (missing PDF, parse errors, timeouts) always produce non-empty messages.
    body = error_text.split(":\n", 1)[-1].strip()
    return body == ""


def probe_session_available(model: str) -> bool:
    """Return True if a minimal claude call succeeds, indicating the session limit has lifted."""
    try:
        r = subprocess.run(
            [
                "claude", "--print", "--output-format", "json",
                "--model", model, "--max-turns", "1",
                "--dangerously-skip-permissions",
                "Reply with the single word: ok",
            ],
            capture_output=True, text=True, timeout=30,
        )
        return r.returncode == 0
    except Exception:
        return False



def wait_for_session(model: str, should_stop) -> None:
    """Block until the Claude session limit lifts, probing every 30 minutes.

    Returns early when `should_stop()` turns true (a shutdown was requested).
    """
    while not should_stop():
        time.sleep(1800)
        print("[Probe] Checking if session limit has reset...", file=sys.stderr)
        if probe_session_available(model):
            print("[Probe] Session available — resuming", file=sys.stderr)
            return
        print("[Probe] Still limited — waiting another 30 minutes", file=sys.stderr)


def discover_papers(papers_dir: Path, include: set[str] | None, limit: int | None,
                    *, html_mode: bool = False, pdf_only: bool = False) -> list[Path]:
    """Paper folders a batch should process, sorted, optionally capped at `limit`.

    Default: any folder with readable full text, judged by the same inventory
    extract_paper validates against, so discovery never queues a paper that
    extraction then refuses (or skips one it would take). --html / --onlypdf
    instead want a folder holding that one format.
    """
    def wanted(p: Path) -> bool:
        if html_mode:
            return any(p.glob("*.html"))
        if pdf_only:
            return any(p.glob("*.pdf"))
        return paper_artifacts(p)["has_fulltext"]

    # Cheap name filter first: the inventory costs several globs per folder.
    dirs = sorted(p for p in papers_dir.iterdir()
                  if p.is_dir() and (include is None or p.name in include) and wanted(p))
    if limit is not None and len(dirs) > limit:
        print(f"Limiting batch from {len(dirs)} to {limit} papers (--limit)", file=sys.stderr)
        dirs = dirs[:limit]
    return dirs


def _log_kind(msg: str) -> str | None:
    """Which batch-summary counter an extract_paper log line feeds, if any."""
    if "SANITY" in msg:
        return "sanity_corrections" if "→" in msg else "sanity_warnings"
    if "📖  Replication:" in msg:
        return "enrichment_rep_ok"
    if "+  Entry" in msg and "from DOI" in msg:
        return "enrichment_orig_doi"
    if "+  Entry" in msg and "found DOI" in msg:
        return "enrichment_title_found"
    return None


def _print_batch_summary(results: list[dict], skipped: list, errors: list[dict],
                         total_usage: dict, log_counts: Counter) -> None:
    refinement_count = sum(1 for r in results if r.get("usage", {}).get("refinement_round"))
    confidence_counts = {"high": 0, "medium": 0, "low": 0}
    total_entries = 0
    for r in results:
        for rep in r.get("replications", []):
            conf = rep.get("confidence", "").lower()
            if conf in confidence_counts:
                confidence_counts[conf] += 1
            total_entries += 1

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Papers processed: {len(results)}  |  Skipped: {len(skipped)}  |  Errors: {len(errors)}", file=sys.stderr)
    print(
        f"Total tokens: {total_usage['input_tokens'] + total_usage['output_tokens']:,} "
        f"(in: {total_usage['input_tokens']:,}, out: {total_usage['output_tokens']:,})",
        file=sys.stderr,
    )
    print(f"Total cost: ${total_usage['cost_usd']:.4f}", file=sys.stderr)
    print(f"Total time: {_format_duration(total_usage['duration_ms'] / 1000)}", file=sys.stderr)
    if refinement_count:
        print(f"Refinement rounds: {refinement_count}", file=sys.stderr)
    if total_entries:
        print(
            f"Confidence: {confidence_counts['high']} high, "
            f"{confidence_counts['medium']} medium, "
            f"{confidence_counts['low']} low "
            f"({total_entries} total entries)",
            file=sys.stderr,
        )
    c = log_counts
    if c["sanity_corrections"] or c["sanity_warnings"]:
        print(f"Validation: {c['sanity_corrections']} auto-corrections, {c['sanity_warnings']} warnings",
              file=sys.stderr)
    if c["enrichment_rep_ok"] or c["enrichment_orig_doi"] or c["enrichment_title_found"]:
        print(
            f"Enrichment: {c['enrichment_rep_ok']} replication DOIs, "
            f"{c['enrichment_orig_doi']} original DOIs enriched, "
            f"{c['enrichment_title_found']} DOIs found from title",
            file=sys.stderr,
        )
    print(f"{'='*60}", file=sys.stderr)


def extract_batch(
    papers_dir: Path,
    model: str = "sonnet",
    workers: int = 1,
    output_file: Path | None = None,
    level: str = "base",
    skip_check: bool = False,
    tag: str | None = None,
    include_papers: set[str] | None = None,
    use_codex: bool = False,
    pdf_only: bool = False,
    html_mode: bool = False,
    force_tier: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Process all paper directories under papers_dir.

    If include_papers is provided, only process directories whose names are in the set.
    """

    paper_dirs = discover_papers(papers_dir, include_papers, limit,
                                 html_mode=html_mode, pdf_only=pdf_only)

    if not paper_dirs:
        print(f"No paper directories found in {papers_dir}", file=sys.stderr)
        return []

    # Load existing dataset URLs once for the whole batch
    existing_urls = None if skip_check else load_existing_replication_urls()

    print(f"Found {len(paper_dirs)} papers to process", file=sys.stderr)

    results = []
    skipped = []
    errors = []
    total_usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "cost_usd": 0.0,
        "duration_ms": 0,
    }

    def process_one(paper_dir: Path) -> tuple[Path, dict | None, dict | None, str | None, list[str]]:
        # Check if shutdown was requested before starting work
        if _shutdown_requested:
            return (paper_dir, "skip", None, "Shutdown requested", [])

        try:
            data, usage, log_msgs = extract_paper(
                paper_dir,
                model=model,
                level=level,
                existing_urls=existing_urls,
                tag=tag,
                use_codex=use_codex,
                pdf_only=pdf_only,
                html_mode=html_mode,
                force_tier=force_tier,
            )
            return (paper_dir, data, usage, None, log_msgs)
        except SkipPaper as e:
            return (paper_dir, "skip", None, str(e), [])
        except Exception as e:
            return (paper_dir, None, None, str(e), [])

    log_counts = Counter()  # validation / enrichment outcomes, for the summary

    def process_batch_of_papers(papers_to_process: list[Path], batch_name: str = "Initial") -> list[Path]:
        """Process a batch of papers and return list of papers that failed due to usage limits."""
        usage_limit_failures = []

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(process_one, d): d for d in papers_to_process
            }
            for i, future in enumerate(as_completed(futures), 1):
                paper_dir, data, usage, error, log_msgs = future.result()
                name = paper_dir.name
                prefix = f"[{batch_name} {i}/{len(papers_to_process)}]"
                if data == "skip":
                    print(f"{prefix} SKIP  {name}: {error}", file=sys.stderr)
                    skipped.append(name)
                    continue
                elif error:
                    # Check if this is a usage limit error
                    if is_usage_limit_error(error):
                        print(f"{prefix} LIMIT {name}: {error}", file=sys.stderr)
                        usage_limit_failures.append(paper_dir)
                    else:
                        print(f"{prefix} FAIL  {name}: {error}", file=sys.stderr)
                        errors.append({"paper": name, "error": error})
                else:
                    for key in total_usage:
                        total_usage[key] += usage.get(key, 0)
                    n = len(data.get("replications", []))
                    label = f"{n} replication(s)" if data.get("contains_replications") else "no replications"
                    tokens = usage["input_tokens"] + usage["output_tokens"]
                    cost = f"${usage['cost_usd']:.4f}"
                    time_sec = usage['wall_time_ms'] / 1000
                    print(
                        f"{prefix} OK    {name}: {label}  "
                        f"({tokens:,} tokens, {cost}, {time_sec:.1f}s)",
                        file=sys.stderr,
                    )
                    results.append({"paper": name, "usage": usage, **data})
                # Print any log messages from extract_paper with the batch prefix
                for msg in log_msgs:
                    print(f"{prefix} {name}{msg}", file=sys.stderr)
                    kind = _log_kind(msg)
                    if kind:
                        log_counts[kind] += 1

        return usage_limit_failures

    # Process initial batch
    usage_limit_failures = process_batch_of_papers(paper_dirs, "Initial")

    # Retry papers that hit usage limits
    retry_attempt = 1
    max_retries = 100  # Prevent infinite loops

    while usage_limit_failures and retry_attempt <= max_retries and not _shutdown_requested:
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"Session usage limit hit: {len(usage_limit_failures)} papers paused.", file=sys.stderr)
        print("Probing every 30 minutes until session resets...", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)

        if use_codex:
            time.sleep(1800)  # no cheap probe for Codex: wait one window, then retry
        else:
            wait_for_session(model, lambda: _shutdown_requested)

        # Retry the failed papers
        retry_papers = usage_limit_failures
        usage_limit_failures = process_batch_of_papers(retry_papers, f"Retry-{retry_attempt}")

        retry_attempt += 1

    if usage_limit_failures:
        print(f"\nWarning: {len(usage_limit_failures)} papers still hitting usage limits after {max_retries} retries", file=sys.stderr)
        for paper_dir in usage_limit_failures:
            errors.append({"paper": paper_dir.name, "error": "Usage limit after max retries"})

    _print_batch_summary(results, skipped, errors, total_usage, log_counts)

    # Combine all results
    output = {"results": results, "errors": errors, "total_usage": total_usage}

    if output_file:
        output_file.write_text(json.dumps(output, indent=2))
        print(f"Results written to {output_file}", file=sys.stderr)

    # Collate all result JSONs into a single CSV spreadsheet
    collate_results(papers_dir, tag=tag)

    return results


def collate_results(papers_dir: Path, tag: str | None = None) -> Path:
    """Scan all result JSON files and produce a single collated CSV.

    Reads from papers_dir/*/tag/*_result*.json (or papers_dir/*/*_result*.json
    if no tag). Returns path to the written CSV.
    """
    COLUMNS = [
        "replication_doi", "replication_url", "replication_title", "replication_journal",
        "replication_volume", "replication_issue", "replication_pages", "replication_year",
        "replication_authors",
        "contains_replications",
        "original_url", "original_authors", "original_title",
        "original_journal", "original_volume", "original_issue",
        "original_pages", "original_year", "description", "result",
        "replication_type", "discipline", "subdiscipline", "explanation", "confidence",
        "citation_sentence",
        "original_n", "original_es", "original_es_type", "original_es_95_CI",
        "original_p_value", "original_p_value_type", "original_p_value_tails",
        "replication_n", "replication_es", "replication_es_type", "replication_es_95_CI",
        "replication_p_value", "replication_p_value_type", "replication_p_value_tails",
        "ai_version", "validated",
    ]

    rows = []
    skipped_no_tag = 0
    for paper_dir in sorted(papers_dir.iterdir()):
        if not paper_dir.is_dir():
            continue

        # Find result JSON. When a tag is given the tag subdir is REQUIRED: falling
        # back to the paper root would collate whatever older, untagged extraction
        # happens to sit there, silently mixing previous runs into this run's output
        # (and re-ingesting stale results under the new tag's name).
        if tag:
            search_dir = paper_dir / tag
            if not search_dir.is_dir():
                skipped_no_tag += 1
                continue
        else:
            search_dir = paper_dir
        result_file = None
        for suffix in RESULT_SUFFIXES:
            candidate = search_dir / f"{paper_dir.name}{suffix}"
            if candidate.exists():
                result_file = candidate
                break
        if result_file is None:
            continue

        try:
            data = json.loads(result_file.read_text())
        except (json.JSONDecodeError, IOError):
            continue

        replication_doi = folder_to_doi(paper_dir.name)

        # Read paper-level replication metadata if enriched during extraction
        rep_meta = data.get("replication_metadata", {})
        rep_base = {
            "replication_doi": replication_doi,
            "replication_url": f"https://doi.org/{replication_doi}",
            "replication_title": rep_meta.get("title", ""),
            "replication_journal": rep_meta.get("journal", ""),
            "replication_volume": rep_meta.get("volume", ""),
            "replication_issue": rep_meta.get("issue", ""),
            "replication_pages": rep_meta.get("pages", ""),
            "replication_year": rep_meta.get("year", ""),
            "replication_authors": rep_meta.get("authors", ""),
        }

        if not data.get("contains_replications") or not data.get("replications"):
            rows.append({
                **rep_base,
                "contains_replications": False,
                **{k: "" for k in COLUMNS if k not in rep_base and k != "contains_replications"},
            })
            continue

        for rep in data["replications"]:
            row = {
                **rep_base,
                "replication_url": rep.get("replication_url", rep_base["replication_url"]),
                "contains_replications": True,
            }
            # Fill remaining columns from extracted data
            for k in COLUMNS:
                if k not in row:
                    row[k] = rep.get(k, "")
            row["validated"] = "no"
            rows.append(row)

    # Write CSV
    csv_name = f"collated_results_{tag}.csv" if tag else "collated_results.csv"
    out_path = papers_dir / csv_name
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    n_rep = sum(1 for r in rows if r["contains_replications"])
    n_norep = sum(1 for r in rows if not r["contains_replications"])
    n_papers = len(set(r["replication_doi"] for r in rows if r["contains_replications"]))
    print(f"\nCollated {len(rows)} rows → {out_path}", file=sys.stderr)
    print(f"  Replications: {n_rep} rows ({n_papers} papers)  |  No replications: {n_norep} papers", file=sys.stderr)
    if skipped_no_tag:
        print(f"  Skipped (no '{tag}/' subdir — not extracted under this tag): {skipped_no_tag} papers",
              file=sys.stderr)

    return out_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract replication data from academic papers using Claude"
    )
    parser.add_argument(
        "path",
        type=Path,
        help="Path to a single paper directory, or parent directory for --batch",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Process all paper subdirectories under the given path",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel workers for batch mode (default: 1)",
    )
    parser.add_argument(
        "--model",
        default="sonnet",
        help="Claude model to use (default: sonnet)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output file for batch results (default: stdout)",
    )
    parser.add_argument(
        "--level",
        choices=["base", "full"],
        default="full",
        help="Extraction level: full (every field incl. statistics; the normal mode) or "
             "base (the same agentic workflow with the statistics blocks stripped from the "
             "prompt -- the control arm for extract_core.py; writes _result.json).",
    )
    parser.add_argument(
        "--dontcheck",
        action="store_true",
        help="Skip checking if the paper DOI is already in the latest dataset",
    )
    parser.add_argument(
        "--tag",
        type=str,
        default=None,
        help="Tag for organizing outputs into subdirectories (e.g., 'sonnet_test_02_2026')",
    )
    parser.add_argument(
        "--include-list",
        type=Path,
        default=None,
        help="File listing paper folder names to include (one per line). Only these papers will be processed.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N papers (after discovery and filtering). Useful for test runs or capping large batches.",
    )
    parser.add_argument(
        "--collate-only",
        action="store_true",
        help="Only collate existing result JSONs into a CSV — no extraction",
    )
    parser.add_argument(
        "--onlypdf",
        action="store_true",
        help="PDF-only mode: process papers with only PDF files (no markdown/JSON). Uses prompt_full_pdf_only.md",
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="HTML mode: process papers with single HTML file. Uses prompt_full.md",
    )
    parser.add_argument(
        "--force-tier",
        choices=["xml", "html", "grobid", "pdf"],
        default=None,
        help="Benchmark only: restrict the full-text tier ladder to one rung (papers lacking it fall through to the PDF). Recorded in provenance.json.",
    )
    parser.add_argument("--usecodex", action="store_true", help="Use Codex CLI (set --model explicitly)")
    return parser


def main():
    # Register signal handler for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)

    parser = build_parser()
    args = parser.parse_args()
    if args.usecodex and args.model == "sonnet":
        parser.error("--usecodex requires an explicit Codex --model")

    start = time.monotonic()

    if args.collate_only:
        collate_results(args.path, tag=args.tag)
        elapsed = time.monotonic() - start
        print(f"Runtime: {_format_duration(elapsed)}", file=sys.stderr)
        sys.exit(0)

    include_papers = None
    if args.include_list:
        include_papers = set(
            line.strip() for line in args.include_list.read_text().splitlines() if line.strip()
        )

    if args.batch:
        extract_batch(
            args.path,
            model=args.model,
            workers=args.workers,
            output_file=args.output,
            level=args.level,
            skip_check=args.dontcheck,
            tag=args.tag,
            include_papers=include_papers,
            use_codex=args.usecodex,
            pdf_only=args.onlypdf,
            html_mode=args.html,
            force_tier=args.force_tier,
            limit=args.limit,
        )
    else:
        existing_urls = None if args.dontcheck else load_existing_replication_urls()
        try:
            data, usage, log_msgs = extract_paper(
                args.path,
                model=args.model,
                level=args.level,
                existing_urls=existing_urls,
                tag=args.tag,
                use_codex=args.usecodex,
                pdf_only=args.onlypdf,
                html_mode=args.html,
                force_tier=args.force_tier,
            )
            tokens = usage["input_tokens"] + usage["output_tokens"]
            print(
                f"Tokens: {tokens:,} (in: {usage['input_tokens']:,}, out: {usage['output_tokens']:,})  "
                f"Cost: ${usage['cost_usd']:.4f}  "
                f"Turns: {usage['num_turns']}",
                file=sys.stderr,
            )
        except SkipPaper as e:
            print(f"SKIP: {e}", file=sys.stderr)

        # Auto-collate the parent papers directory so the CSV stays in sync
        # with the per-paper result JSONs. Same behavior as --batch mode.
        # Falls through from either successful extraction or a SkipPaper.
        collate_results(args.path.parent, tag=args.tag)

    elapsed = time.monotonic() - start
    print(f"Runtime: {_format_duration(elapsed)}", file=sys.stderr)


if __name__ == "__main__":
    main()
