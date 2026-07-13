#!/usr/bin/env python3
"""
Replication data extractor for The Metascience Observatory.

Calls the claude CLI to extract replication data from academic papers
that have been pre-processed into split files (abstract.md, body.md,
references.json, metadata.json) via GROBID.

The claude agent writes result.json into each paper's directory.
The full claude output (including reasoning) is saved as debug_log.json.

Usage:
    # Single paper
    python extract.py papers/10.1234_some-paper/

    # Batch: all papers in a directory
    python extract.py papers/ --batch

    # Batch with parallel workers
    python extract.py papers/ --batch --workers 4

    # Use Cursor Agent CLI instead of Claude CLIXue_PLoSOne_2014_HCVNS3-4A.pdf
    python extract.py papers/ --batch --usecursor
"""

import argparse
import csv
import difflib
import json
import logging
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from fetch_metadata_from_doi import fetch_metadata_from_doi, _new_authors_are_better
from fetch_metadata_from_title import fetch_metadata_from_title

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


PROMPT_DIR = Path(__file__).parent
PROMPT_FILES = {
    "base": PROMPT_DIR / "prompt.md",
    "mid": PROMPT_DIR / "prompt_mid.md",
    "full": PROMPT_DIR / "prompt_full.md",
    "pdf_only": PROMPT_DIR / "prompt_full_pdf_only.md",
    "html": PROMPT_DIR / "prompt_full_html.md",  # HTML-specific prompt with image reading
    "xml": PROMPT_DIR / "prompt_full_xml.md",  # XML/JATS-specific prompt
}
# Shared content (schema, field reference, replication type definitions,
# result classification, discipline list, confidence, edge cases) lives in
# a single file and is appended to every mode-specific prompt at load time.
# This prevents drift — updates to shared rules happen in one place and
# propagate to all modes automatically.
PROMPT_SHARED_CORE = PROMPT_DIR / "prompt_shared_core.md"
DATA_DIR = Path("/home/dan/Dropbox/AAA_METASCIENCE_OBSERVATORY/metascience_observatory_website/data")
ONTOLOGY_PATH = DATA_DIR / "metascience_observatory_topic_ontology.json"
VERSION_FILE = DATA_DIR / "version_history.txt"
VERSION_NUMBER_FILE = PROMPT_DIR / "version.txt"

# --- Validation constants ---
VALID_RESULTS = {"success", "failure", "inconclusive", "reversal"}
VALID_REPLICATION_TYPES = {"direct", "close experiment", "close extension", "conceptual"}
VALID_CONFIDENCE = {"low", "medium", "high"}
VALID_P_VALUE_TYPES = {"<", "=", ">"}
VALID_P_VALUE_TAILS = {"one-sided", "two-sided"}
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


def load_system_prompt(level: str = "base") -> str:
    """Load the system prompt for a given mode level.

    Concatenates the mode-specific workflow prompt (title, intro, file
    structure, workflow passes) with the shared core (output schema, field
    reference, replication type, result classification, discipline list,
    confidence, edge cases). The mode file comes first so the model reads
    input-handling instructions before the shared schema/definitions.
    """
    mode_text = PROMPT_FILES[level].read_text()
    shared_text = PROMPT_SHARED_CORE.read_text()
    shared_text = shared_text.replace("{{DISCIPLINE_LIST}}", _render_discipline_block())
    return mode_text.rstrip() + "\n\n" + shared_text


def _format_duration(seconds: float) -> str:
    """Format a duration in seconds as 'Xh YYm ZZs', dropping leading zero components."""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{seconds:.1f}s"


def load_version_number() -> float | int:
    """Load the version number from version.txt.

    Returns the version as a number (int or float), or 0 if the file doesn't exist or is invalid.
    """
    try:
        raw = VERSION_NUMBER_FILE.read_text().strip()
        val = float(raw)
        return int(val) if val == int(val) else val
    except (FileNotFoundError, ValueError) as e:
        logger.warning(f"Failed to load version number from {VERSION_NUMBER_FILE}: {e}")
        return 0


def normalize_doi_url(url: str) -> str:
    """Normalize DOI URL to lowercase https form for comparison."""
    url = url.strip().lower()
    url = url.replace("http://doi.org/", "https://doi.org/")
    url = url.replace("http://dx.doi.org/", "https://doi.org/")
    url = url.replace("https://dx.doi.org/", "https://doi.org/")
    return url


def load_existing_replication_urls() -> set[str]:
    """Load replication_url values from the latest dataset CSV.

    Reads version_history.txt to find the latest filename, then extracts
    all replication_url values, normalized for http/https comparison.
    """
    if not VERSION_FILE.exists():
        print("Warning: version_history.txt not found, skipping duplicate check", file=sys.stderr)
        return set()

    # Get last non-empty, non-comment-only line
    latest_file = None
    for line in VERSION_FILE.read_text().strip().splitlines():
        line = line.split("#")[0].strip()
        if line:
            latest_file = line

    if not latest_file:
        print("Warning: no valid entry in version_history.txt", file=sys.stderr)
        return set()

    csv_path = DATA_DIR / latest_file
    if not csv_path.exists():
        print(f"Warning: dataset {csv_path} not found, skipping duplicate check", file=sys.stderr)
        return set()

    urls = set()
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = row.get("replication_url", "").strip()
            if url:
                urls.add(normalize_doi_url(url))

    print(f"Loaded {len(urls)} existing replication URLs from {latest_file}", file=sys.stderr)
    return urls


def folder_to_doi_url(folder_name: str) -> str:
    """Convert folder name like '10.1002--acp.3769' to 'https://doi.org/10.1002/acp.3769'."""
    return "https://doi.org/" + folder_name.replace("--", "/")


def _extract_doi_from_url(url: str) -> str | None:
    """Extract bare DOI from a DOI URL like 'https://doi.org/10.1234/foo'."""
    if not url:
        return None
    m = re.match(r'https?://(?:dx\.)?doi\.org/(10\..+)', url.strip())
    return m.group(1) if m else None


def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation/whitespace for fuzzy comparison."""
    return re.sub(r'[^a-z0-9\s]', '', title.lower()).strip()


def _title_similarity(a: str, b: str) -> float:
    """Return 0-1 similarity between two titles."""
    return difflib.SequenceMatcher(None, _normalize_title(a), _normalize_title(b)).ratio()


def validate_extraction(data: dict) -> tuple[dict, list[str]]:
    """Validate and auto-correct extracted data. Returns (data, log_messages).

    Checks enum fields, required fields, statistical types, and structural consistency.
    Auto-corrects where safe (case normalization, known aliases). Logs warnings for issues.
    """
    msgs = []
    corrections = 0

    # Structural check: contains_replications consistency
    has_reps = data.get("contains_replications", False)
    reps = data.get("replications", [])
    if has_reps and not reps:
        msgs.append("  ⚠️  SANITY: contains_replications=true but replications array is empty")
    if not has_reps and reps:
        msgs.append("  ⚠️  SANITY: contains_replications=false but replications array has entries")

    for i, rep in enumerate(reps):
        prefix = f"  ⚠️  SANITY entry {i}:"

        # --- Enum: result ---
        result = rep.get("result", "")
        if result:
            if result.lower() in VALID_RESULTS and result != result.lower():
                msgs.append(f"{prefix} result=\"{result}\" → \"{result.lower()}\"")
                rep["result"] = result.lower()
                corrections += 1
            elif result.lower() not in VALID_RESULTS:
                msgs.append(f"{prefix} result=\"{result}\" not in {sorted(VALID_RESULTS)}")

        # --- Enum: replication_type ---
        rtype = rep.get("replication_type", "")
        if rtype:
            if rtype.lower() == "close":
                rep["replication_type"] = "close experiment"
                corrections += 1
                msgs.append(f"{prefix} replication_type=\"close\" → \"close experiment\"")
            elif rtype.lower() in VALID_REPLICATION_TYPES and rtype != rtype.lower():
                msgs.append(f"{prefix} replication_type=\"{rtype}\" → \"{rtype.lower()}\"")
                rep["replication_type"] = rtype.lower()
                corrections += 1
            elif rtype.lower() not in VALID_REPLICATION_TYPES:
                msgs.append(f"{prefix} replication_type=\"{rtype}\" not in {sorted(VALID_REPLICATION_TYPES)}")

        # --- Enum: confidence ---
        conf = rep.get("confidence", "")
        if conf:
            if conf.lower() == "moderate":
                rep["confidence"] = "medium"
                corrections += 1
                msgs.append(f"{prefix} confidence=\"moderate\" → \"medium\"")
            elif conf.lower() in VALID_CONFIDENCE and conf != conf.lower():
                msgs.append(f"{prefix} confidence=\"{conf}\" → \"{conf.lower()}\"")
                rep["confidence"] = conf.lower()
                corrections += 1
            elif conf.lower() not in VALID_CONFIDENCE:
                msgs.append(f"{prefix} confidence=\"{conf}\" not in {sorted(VALID_CONFIDENCE)}")

        # --- Enum: discipline ---
        disc = rep.get("discipline", "")
        if disc and disc.lower() != "other":
            disc_lower = disc.lower()
            if disc_lower in {d.lower() for d in VALID_DISCIPLINES}:
                # Normalize case
                canonical = next(d for d in VALID_DISCIPLINES if d.lower() == disc_lower)
                if disc != canonical:
                    msgs.append(f"{prefix} discipline=\"{disc}\" → \"{canonical}\"")
                    rep["discipline"] = canonical
                    corrections += 1
            elif disc_lower in {s.lower() for s in _ALL_SUBDISCIPLINES}:
                # Subdiscipline used as discipline — auto-correct
                canonical_sub = next(s for s in _ALL_SUBDISCIPLINES if s.lower() == disc_lower)
                parent = _SUBDISCIPLINE_TO_DISCIPLINE[canonical_sub]
                rep["discipline"] = parent
                if not rep.get("subdiscipline"):
                    rep["subdiscipline"] = canonical_sub
                corrections += 1
                msgs.append(f"{prefix} discipline=\"{disc}\" → \"{parent}\" (was subdiscipline)")
            else:
                msgs.append(f"{prefix} discipline=\"{disc}\" not in valid list")

        # --- Enum: subdiscipline ---
        subdisc = rep.get("subdiscipline", "")
        current_disc = rep.get("discipline", "")
        if subdisc and subdisc.lower() != "other" and current_disc in VALID_DISCIPLINES:
            valid_subs = {s.lower() for s in VALID_DISCIPLINES[current_disc]}
            if subdisc.lower() not in valid_subs:
                msgs.append(f"{prefix} subdiscipline=\"{subdisc}\" not in {current_disc} list")

        # --- Required fields ---
        for field in ("description", "result", "confidence", "discipline", "replication_type"):
            if not rep.get(field):
                msgs.append(f"{prefix} missing required field \"{field}\"")

        # --- Citation sentence ---
        if not rep.get("citation_sentence"):
            msgs.append(f"{prefix} missing citation_sentence")

        # --- original_url normalization (silent: always correct, never surprising) ---
        orig_url = rep.get("original_url", "")
        if orig_url:
            normalized = normalize_doi_url(orig_url)
            if normalized != orig_url:
                rep["original_url"] = normalized
                corrections += 1

        # --- Statistical: n fields (coerce string -> int, silent for benign case) ---
        for n_field in ("original_n", "replication_n"):
            val = rep.get(n_field, "")
            if val != "" and val is not None:
                try:
                    n_int = int(float(str(val)))
                    if n_int <= 0:
                        msgs.append(f"{prefix} {n_field}={val} should be positive")
                    elif val != n_int:
                        rep[n_field] = n_int
                        corrections += 1
                except (ValueError, TypeError):
                    msgs.append(f"{prefix} {n_field}=\"{val}\" is not a valid integer")

        # --- Statistical: p_value range ---
        for pv_field in ("original_p_value", "replication_p_value"):
            val = rep.get(pv_field, "")
            if val != "" and val is not None:
                try:
                    pv = float(str(val))
                    if pv < 0 or pv > 1:
                        msgs.append(f"{prefix} {pv_field}={pv} outside [0, 1]")
                except (ValueError, TypeError):
                    msgs.append(f"{prefix} {pv_field}=\"{val}\" is not a valid number")

        # --- Statistical: p_value_type ---
        for pvt_field in ("original_p_value_type", "replication_p_value_type"):
            val = rep.get(pvt_field, "")
            if val and val not in VALID_P_VALUE_TYPES:
                msgs.append(f"{prefix} {pvt_field}=\"{val}\" not in {sorted(VALID_P_VALUE_TYPES)}")

        # --- Statistical: es paired with es_type ---
        for side in ("original", "replication"):
            es = rep.get(f"{side}_es", "")
            es_type = rep.get(f"{side}_es_type", "")
            if es and not es_type:
                msgs.append(f"{prefix} {side}_es={es} but {side}_es_type is empty")
            elif es_type and not es:
                msgs.append(f"{prefix} {side}_es_type=\"{es_type}\" but {side}_es is empty")

    return data, msgs


def _format_author_initial(name: str) -> str:
    """Add periods after single-letter initials. 'J Schooler' -> 'J. Schooler'"""
    if not name:
        return name
    parts = name.split()
    return ' '.join(p + '.' if len(p) == 1 and p.isalpha() else p for p in parts)


def format_authors_string(authors_str: str) -> str:
    """Format semicolon-separated authors, adding periods after initials."""
    if not authors_str or not isinstance(authors_str, str):
        return authors_str
    return '; '.join(_format_author_initial(a.strip()) for a in authors_str.split(';'))


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
    """Pull last names from a semicolon-separated author string like 'Smith, John; Jones, Alice'."""
    names = []
    for author in orig_authors.split(";"):
        last = author.strip().split(",")[0].strip()
        if last and len(last) > 1:
            names.append(last)
    return names


def _reference_matches(ref: dict, last_names: list[str], year: str) -> bool:
    """Check whether a references.json entry matches the extracted author last name(s) and year."""
    ref_year = ref.get("year")
    if ref_year is None or str(ref_year) != str(year):
        return False
    ref_authors = ref.get("authors") or []
    authors_blob = " ".join(ref_authors).lower() if isinstance(ref_authors, list) else str(ref_authors).lower()
    return any(name.lower() in authors_blob for name in last_names)


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


def extract_paper(
    paper_dir: Path,
    model: str = "sonnet",
    level: str = "base",
    existing_urls: set[str] | None = None,
    tag: str | None = None,
    use_cursor: bool = False,
    pdf_only: bool = False,
    html_mode: bool = False,
) -> tuple[dict, dict, list[str]]:
    """Run the selected agent CLI against a single paper directory.

    The agent writes result.json into paper_dir (or paper_dir/tag if tag is provided).
    Returns (result_dict, usage_dict, log_messages).
    Raises SkipPaper if already in the dataset.
    """
    paper_dir = paper_dir.resolve()
    log_messages = []  # Collected for caller to print with batch prefix

    # Determine output directory based on tag
    if tag:
        output_dir = paper_dir / tag
        output_dir.mkdir(exist_ok=True)
    else:
        output_dir = paper_dir

    # Auto-detect XML mode: folder has .xml but no .html, .pdf, or abstract.md
    xml_mode = False
    if not html_mode and not pdf_only:
        has_xml = any(paper_dir.glob("*.xml"))
        has_html = any(paper_dir.glob("*.html"))
        has_pdf = any(paper_dir.glob("*.pdf"))
        has_abstract = (paper_dir / "abstract.md").exists()
        if has_xml and not has_html and not has_pdf and not has_abstract:
            xml_mode = True

    # Check if output already exists for this level (enables resuming)
    if html_mode:
        suffixes = {"html": "_result_html.json"}
        prompt_level = "html"
    elif xml_mode:
        suffixes = {"xml": "_result_xml.json"}
        prompt_level = "xml"
    elif pdf_only:
        suffixes = {"pdf_only": "_result_pdf_only.json"}
        prompt_level = "pdf_only"
    else:
        suffixes = {"base": "_result.json", "mid": "_result_mid.json", "full": "_result_full.json"}
        prompt_level = level

    final_path = output_dir / f"{paper_dir.name}{suffixes.get(prompt_level, suffixes.get(level))}"

    # Check if ANY result file already exists (allows skipping regardless of mode)
    all_suffixes = ["_result_xml.json", "_result_html.json", "_result_pdf_only.json", "_result_full.json", "_result_mid.json", "_result.json"]
    for existing_suffix in all_suffixes:
        existing_path = output_dir / f"{paper_dir.name}{existing_suffix}"
        if existing_path.exists():
            raise SkipPaper(f"Output already exists: {existing_path.name}")

    # Check if this paper is already in the dataset
    doi_url = normalize_doi_url(folder_to_doi_url(paper_dir.name))
    if existing_urls and doi_url in existing_urls:
        raise SkipPaper(f"Already in dataset: {doi_url}")

    # Validate expected files exist
    if html_mode:
        # For HTML mode, find the HTML file
        html_files = list(paper_dir.glob("*.html"))
        if not html_files:
            raise FileNotFoundError(f"Missing HTML file in {paper_dir}")
        html_file = html_files[0]  # Use first HTML if multiple exist
        pdf_file = None
        xml_file = None
    elif xml_mode:
        # For XML mode, find the XML file
        xml_files = list(paper_dir.glob("*.xml"))
        if not xml_files:
            raise FileNotFoundError(f"Missing XML file in {paper_dir}")
        xml_file = xml_files[0]  # Use first XML if multiple exist
        pdf_file = None
        html_file = None
    else:
        # Find PDF for all non-HTML/non-XML modes
        pdf_files = list(paper_dir.glob("*.pdf"))
        if not pdf_files:
            raise FileNotFoundError(f"Missing PDF file in {paper_dir}")
        pdf_file = pdf_files[0]  # Use first PDF if multiple exist
        html_file = None
        xml_file = None

        # Validate abstract.md exists for non-PDF-only modes
        if not pdf_only:
            abstract = paper_dir / "abstract.md"
            if not abstract.exists():
                raise FileNotFoundError(f"Missing abstract.md in {paper_dir}")

    system_prompt = load_system_prompt(level=prompt_level)

    if html_mode:
        # Find all image files (figures, tables, diagrams)
        image_files = sorted(
            list(paper_dir.glob("*.png")) +
            list(paper_dir.glob("*.jpg")) +
            list(paper_dir.glob("*.jpeg"))
        )

        if image_files:
            image_list = "\n".join([f"  - {img.name}" for img in image_files])
            user_prompt = (
                f"Extract replication data from the paper in: {paper_dir}\n"
                f"The paper is available as an HTML file: {html_file.name}\n"
                f"The following image files contain figures, tables, and diagrams:\n{image_list}\n\n"
                f"Read {paper_dir}/{html_file.name} and ALL the image files above to extract complete replication data.\n"
                f"The images often contain critical statistical information (effect sizes, p-values, sample sizes, graphs).\n"
                f"Save your result to {output_dir}/result.json"
            )
        else:
            user_prompt = (
                f"Extract replication data from the paper in: {paper_dir}\n"
                f"The paper is available as an HTML file: {html_file.name}\n"
                f"Read {paper_dir}/{html_file.name} and extract all replication data.\n"
                f"Save your result to {output_dir}/result.json"
            )
    elif xml_mode:
        user_prompt = (
            f"Extract replication data from the paper in: {paper_dir}\n"
            f"The paper is available as an XML file: {xml_file.name}\n"
            f"Read {paper_dir}/{xml_file.name} and extract all replication data.\n"
            f"The XML contains structured markup (JATS, TEI, or similar) — read through the tags to extract text, tables, references, and statistics.\n"
            f"Save your result to {output_dir}/result.json"
        )
    elif pdf_only:
        user_prompt = (
            f"Extract replication data from the paper in: {paper_dir}\n"
            f"The paper is available as a PDF file: {pdf_file.name}\n"
            f"Start by reading the first few pages (1-3) of {paper_dir}/{pdf_file.name} using the Read tool with the pages parameter.\n"
            f"Save your result to {output_dir}/result.json"
        )
    else:
        # Build list of available files
        available_files = [
            f"{paper_dir}/abstract.md",
            f"{paper_dir}/body.md",
            f"{paper_dir}/references.json"
        ]

        # Check if tables.md exists
        tables_file = paper_dir / "tables.md"
        if tables_file.exists():
            available_files.append(f"{paper_dir}/tables.md")

        # Add PDF
        available_files.append(f"{paper_dir}/{pdf_file.name}")

        files_list = ", ".join(available_files)
        user_prompt = (
            f"Extract replication data from the paper in: {paper_dir}\n"
            f"The files you are working with are {files_list}.\n"
            f"Save your result to {output_dir}/result.json"
        )

    if use_cursor:
        # Cursor CLI does not expose a dedicated system prompt flag,
        # so we prepend the system instructions to the user prompt.
        cursor_prompt = (
            f"System instructions:\n{system_prompt}\n\n"
            f"Task instructions:\n{user_prompt}"
        )
        cmd = [
            "cursor", "agent",
            "--print",
            "--output-format", "json",
            "--model", model,
            "--workspace", str(paper_dir),
            "--force",
            cursor_prompt,
        ]
        cli_name = "cursor"
    else:
        cmd = [
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
        ]
        cli_name = "claude"

    start = time.monotonic()

    # Use Popen instead of run to allow signal forwarding
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Register process for graceful shutdown
    thread_id = threading.current_thread().ident
    _running_processes[thread_id] = process

    try:
        stdout, stderr = process.communicate(timeout=600)  # 10 minute timeout per paper
        returncode = process.returncode
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise RuntimeError(f"Timeout after 10 minutes processing {paper_dir}")
    finally:
        # Unregister process
        _running_processes.pop(thread_id, None)

    wall_time_ms = int((time.monotonic() - start) * 1000)

    # Create result object compatible with subprocess.run
    class Result:
        def __init__(self, returncode, stdout, stderr):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    result = Result(returncode, stdout, stderr)

    if result.returncode != 0:
        combined = ((result.stderr or "") + (result.stdout or "")).strip()
        raise RuntimeError(
            f"{cli_name} CLI failed for {paper_dir}:\n{combined}"
        )

    # Parse the selected CLI JSON envelope
    try:
        cli_output = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Failed to parse {cli_name} CLI output for {paper_dir}:\n"
            f"{result.stdout[:500]}"
        )

    # Get the actual model ID from modelUsage keys
    model_usage = cli_output.get("modelUsage", {})
    model_id = next(iter(model_usage), model)

    # Extract usage info
    usage = {
        "model": model_id,
        "input_tokens": cli_output.get("usage", {}).get("input_tokens", 0),
        "output_tokens": cli_output.get("usage", {}).get("output_tokens", 0),
        "cache_creation_tokens": cli_output.get("usage", {}).get("cache_creation_input_tokens", 0),
        "cache_read_tokens": cli_output.get("usage", {}).get("cache_read_input_tokens", 0),
        "cost_usd": cli_output.get("total_cost_usd", 0),
        "duration_ms": cli_output.get("duration_ms", 0),
        "wall_time_ms": wall_time_ms,
        "num_turns": cli_output.get("num_turns", 0),
    }

    # Save full output as debug log (includes reasoning text)
    debug_log = {
        "model": model_id,
        "modelUsage": model_usage,
        "assistant_text": cli_output.get("result", ""),
        "usage": usage,
        "session_id": cli_output.get("session_id", ""),
    }
    debug_path = output_dir / "debug_log.json"
    debug_path.write_text(json.dumps(debug_log, indent=2))

    # Read the result.json the agent should have written
    result_path = output_dir / "result.json"
    if not result_path.exists():
        raise RuntimeError(
            f"Agent did not write result.json for {paper_dir}.\n"
            f"Agent output: {cli_output.get('result', '')[:500]}"
        )

    # Retry JSON parse with short delay to handle filesystem flush race condition
    for attempt in range(3):
        try:
            data = json.loads(result_path.read_text())
            break
        except json.JSONDecodeError:
            if attempt < 2:
                time.sleep(1)
            else:
                raise RuntimeError(
                    f"Agent wrote invalid JSON to {result_path}:\n"
                    f"{result_path.read_text()[:500]}"
                )

    # Inject replication_url (this paper's DOI URL) and ai_version into each replication entry
    replication_url = folder_to_doi_url(paper_dir.name)
    ai_version = load_version_number()
    for rep in data.get("replications", []):
        rep["replication_url"] = replication_url
        rep["ai_version"] = ai_version

    # Sanity-check extracted data (auto-correct known issues, warn on others)
    data, sanity_msgs = validate_extraction(data)
    log_messages.extend(sanity_msgs)

    # Validate original DOIs against external APIs
    doi_mismatches = []
    original_doi_cache = {}
    if not _shutdown_requested:
        doi_mismatches, original_doi_cache = validate_original_dois(data)
        for mm in doi_mismatches:
            log_messages.append(
                f"  ❌  DOI mismatch entry {mm['entry_idx']} (similarity={mm['similarity']}) "
                f"— agent: \"{mm['agent_title'][:60]}\" vs API: \"{mm['api_title'][:60]}\""
            )

    # Validate citation sentences against extracted original authors/year.
    # Pass references.json (when available) so the validator can corroborate
    # authors/year via the bibliography, avoiding false positives when the
    # citation_sentence is narrative prose rather than a formal citation.
    references_path = paper_dir / "references.json"
    references_data = None
    if references_path.exists():
        try:
            with open(references_path) as f:
                references_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            references_data = None
    citation_mismatches = validate_citation_sentences(data, references_data)
    for cm in citation_mismatches:
        parts = []
        if not cm["author_found"]:
            parts.append("author not found in citation")
        if not cm["year_found"]:
            parts.append("year not found in citation")
        log_messages.append(
            f"  ❌  Citation mismatch entry {cm['entry_idx']} ({', '.join(parts)}) "
            f"— citation: \"{cm['citation'][:80]}\" vs extracted: "
            f"\"{cm['extracted_authors'][:40]}\" ({cm['extracted_year']})"
        )

    # Check for low/medium confidence entries and trigger refinement round
    low_med_entries = []
    for i, rep in enumerate(data.get("replications", [])):
        conf = rep.get("confidence", "").lower()
        if conf in ["low", "medium"]:
            desc = rep.get("description", "")[:80]
            result_val = rep.get("result", "unknown")
            low_med_entries.append((i, conf, result_val, desc))

    needs_refinement = low_med_entries or doi_mismatches or citation_mismatches
    session_id = cli_output.get("session_id", "")

    if needs_refinement and not use_cursor and not _shutdown_requested:
        reasons = []
        if low_med_entries:
            reasons.append(f"{len(low_med_entries)} low/medium confidence")
        if doi_mismatches:
            reasons.append(f"{len(doi_mismatches)} DOI mismatches")
        if citation_mismatches:
            reasons.append(f"{len(citation_mismatches)} citation mismatches")
        log_messages.append(
            f"  ⚠️  {', '.join(reasons)} — starting review round"
        )
        for idx, conf, res, desc in low_med_entries:
            log_messages.append(f"      Entry {idx}: [{conf}] result={res} — {desc}")

        # Build reviewer prompt for a fresh agent
        pdf_files = list(paper_dir.glob("*.pdf"))
        pdf_name = pdf_files[0].name if pdf_files else "the PDF"

        # List available paper files for the reviewer
        paper_files = []
        for f in paper_dir.iterdir():
            if f.is_file() and f.suffix in ('.md', '.json', '.pdf'):
                paper_files.append(f.name)
        files_list = ", ".join(sorted(paper_files))

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

        refinement_prompt = "\n".join(prompt_parts)

        refine_cmd = [
            "claude",
            "--print",
            "--output-format", "json",
            "--max-turns", "20",
            "--allowedTools", "Read", "Grep", "Glob", "Write",
            "--dangerously-skip-permissions",
            "-p", refinement_prompt,
        ]

        refine_start = time.monotonic()

        refine_process = subprocess.Popen(
            refine_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        thread_id = threading.current_thread().ident
        _running_processes[thread_id] = refine_process

        try:
            refine_stdout, refine_stderr = refine_process.communicate(timeout=300)  # 5 min for refinement
            refine_returncode = refine_process.returncode
        except subprocess.TimeoutExpired:
            refine_process.kill()
            refine_stdout, refine_stderr = refine_process.communicate()
            log_messages.append(f"  ⚠️  Review timed out")
            refine_returncode = -1
        finally:
            _running_processes.pop(thread_id, None)

        refine_wall = time.monotonic() - refine_start

        if refine_returncode == 0:
            # Re-read result.json which the agent should have updated
            try:
                refined_data = json.loads(result_path.read_text())

                # Count how many entries were upgraded
                upgraded = 0
                for i, rep in enumerate(refined_data.get("replications", [])):
                    if rep.get("confidence", "").lower() == "high":
                        # Check if this was previously low/medium
                        if any(idx == i for idx, _, _, _ in low_med_entries):
                            upgraded += 1

                # Re-inject replication_url and ai_version
                for rep in refined_data.get("replications", []):
                    rep["replication_url"] = replication_url
                    rep["ai_version"] = ai_version

                data = refined_data

                # Re-validate after review round
                data, post_review_msgs = validate_extraction(data)
                log_messages.extend(post_review_msgs)

                kept = len(low_med_entries) - upgraded
                parts = []
                if low_med_entries:
                    parts.append(f"{upgraded} upgraded, {kept} kept" if kept else f"{upgraded} upgraded")
                if doi_mismatches:
                    parts.append(f"{len(doi_mismatches)} DOI(s) reviewed")
                log_messages.append(
                    f"  ✅  Review done: {', '.join(parts)} ({refine_wall:.1f}s)"
                )

                # Update usage with refinement costs
                try:
                    refine_output = json.loads(refine_stdout)
                    usage["input_tokens"] += refine_output.get("usage", {}).get("input_tokens", 0)
                    usage["output_tokens"] += refine_output.get("usage", {}).get("output_tokens", 0)
                    usage["cost_usd"] += refine_output.get("total_cost_usd", 0)
                    usage["num_turns"] += refine_output.get("num_turns", 0)
                    usage["wall_time_ms"] += int(refine_wall * 1000)
                    usage["refinement_round"] = True
                except (json.JSONDecodeError, KeyError):
                    pass

            except (json.JSONDecodeError, FileNotFoundError):
                log_messages.append(f"  ⚠️  Review produced invalid result, keeping original")
        else:
            log_messages.append(f"  ⚠️  Review failed (exit {refine_returncode}), keeping original")

    elif needs_refinement:
        # Log without review (cursor mode or shutdown)
        if low_med_entries:
            log_messages.append(
                f"  ⚠️  {len(low_med_entries)} entries with low/medium confidence (no review)"
            )
            for idx, conf, res, desc in low_med_entries:
                log_messages.append(f"      Entry {idx}: [{conf}] result={res} — {desc}")
        if doi_mismatches:
            log_messages.append(
                f"  ❌  {len(doi_mismatches)} DOI mismatches (no review)"
            )

    # ---- METADATA ENRICHMENT ----
    # Enrich after all refinement is complete, so we work on final data
    if not _shutdown_requested:
        rep_doi = _extract_doi_from_url(replication_url)
        try:
            data, enrich_msgs = enrich_metadata(
                data,
                replication_doi=rep_doi or "",
                original_doi_cache=original_doi_cache,
            )
            log_messages.extend(enrich_msgs)
        except Exception as e:
            log_messages.append(f"  !  Metadata enrichment failed: {e}")

    # Rename result.json to {folder_name}_result.json / _result_mid.json / _result_full.json / _result_pdf_only.json / _result_html.json / _result_xml.json
    if html_mode:
        suffix = "_result_html.json"
    elif xml_mode:
        suffix = "_result_xml.json"
    elif pdf_only:
        suffix = "_result_pdf_only.json"
    else:
        suffixes = {"base": "_result.json", "mid": "_result_mid.json", "full": "_result_full.json"}
        suffix = suffixes[level]
    final_path = output_dir / f"{paper_dir.name}{suffix}"
    final_path.write_text(json.dumps(data, indent=2))
    result_path.unlink()

    return data, usage, log_messages


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


SCREEN_PROMPT_FILE = PROMPT_DIR / "prompt_screen.md"


def _extract_pdf_text_local(pdf_path: Path, max_chars: int = 4000) -> str:
    """Extract leading text from a PDF using fitz (no claude call needed)."""
    try:
        import fitz  # pymupdf
        doc = fitz.open(str(pdf_path))
        text = ""
        for page in doc[:4]:
            text += page.get_text()
            if len(text) >= max_chars:
                break
        return text[:max_chars]
    except Exception as e:
        raise RuntimeError(f"fitz PDF extraction failed: {e}")


def screen_paper(
    paper_dir: Path,
    model: str = "haiku",
    tag: str | None = None,
    pdf_only: bool = False,
) -> tuple[dict, dict]:
    """Run a cheap Haiku screen to check if a paper contains any type of replication.

    Returns (result_dict, usage_dict). Writes {paper_name}_screen.json to tag dir.
    Raises RuntimeError on failure, SkipPaper if screen already exists.
    """
    paper_dir = paper_dir.resolve()

    output_dir = paper_dir / tag if tag else paper_dir
    output_dir.mkdir(exist_ok=True)

    screen_path = output_dir / f"{paper_dir.name}_screen.json"
    if screen_path.exists():
        raise SkipPaper(f"Screen already exists: {screen_path.name}")

    # Gather input text locally — no claude tool calls needed
    if pdf_only:
        pdfs = list(paper_dir.glob("*.pdf"))
        if not pdfs:
            raise RuntimeError(f"No PDF found in {paper_dir}")
        paper_text = _extract_pdf_text_local(pdfs[0])
    else:
        abstract_path = paper_dir / "abstract.md"
        if not abstract_path.exists():
            raise RuntimeError(f"No abstract.md in {paper_dir}")
        paper_text = abstract_path.read_text()
        # Also prepend intro if body.md exists and abstract is short
        body_path = paper_dir / "body.md"
        if body_path.exists() and len(paper_text) < 500:
            body_text = body_path.read_text()
            paper_text += "\n\n" + body_text[:2000]

    screen_prompt = SCREEN_PROMPT_FILE.read_text()
    user_prompt = screen_prompt + "\n\n" + paper_text.strip()

    start = time.monotonic()
    r = subprocess.run(
        [
            "claude", "--print", "--output-format", "json",
            "--model", model, "--max-turns", "1",
            "--dangerously-skip-permissions",
            user_prompt,
        ],
        capture_output=True, text=True, timeout=60,
    )
    wall_time_ms = int((time.monotonic() - start) * 1000)

    combined = ((r.stderr or "") + (r.stdout or "")).strip()
    if r.returncode != 0:
        raise RuntimeError(f"claude screen failed for {paper_dir}:\n{combined}")

    try:
        cli_output = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"Failed to parse screen CLI output:\n{r.stdout[:300]}")

    model_usage = cli_output.get("modelUsage", {})
    model_id = next(iter(model_usage), model)
    usage = {
        "model": model_id,
        "input_tokens": cli_output.get("usage", {}).get("input_tokens", 0),
        "output_tokens": cli_output.get("usage", {}).get("output_tokens", 0),
        "cost_usd": cli_output.get("total_cost_usd", 0),
        "wall_time_ms": wall_time_ms,
    }

    raw_result = cli_output.get("result", "")
    # Extract JSON object robustly — ignore surrounding markdown fences or explanation text
    start_idx = raw_result.find("{")
    end_idx = raw_result.rfind("}")
    if start_idx != -1 and end_idx != -1:
        raw_result = raw_result[start_idx:end_idx + 1]

    try:
        result = json.loads(raw_result)
    except json.JSONDecodeError:
        raise RuntimeError(f"Screen result is not valid JSON:\n{raw_result[:300]}")

    result["paper"] = paper_dir.name
    result["tag"] = tag
    result["cost_usd"] = usage["cost_usd"]

    screen_path.write_text(json.dumps(result, indent=2))
    return result, usage


def screen_batch(
    papers_dir: Path,
    model: str = "haiku",
    workers: int = 4,
    tag: str | None = None,
    pdf_only: bool = False,
    limit: int | None = None,
    include_papers: set[str] | None = None,
) -> None:
    """Run Haiku screening across all papers and write a positive-list file."""
    papers_dir = papers_dir.resolve()

    if pdf_only:
        paper_dirs = sorted(
            p for p in papers_dir.iterdir()
            if p.is_dir() and any(p.glob("*.pdf"))
            and (include_papers is None or p.name in include_papers)
        )
    else:
        paper_dirs = sorted(
            p for p in papers_dir.iterdir()
            if p.is_dir() and (p / "abstract.md").exists()
            and (include_papers is None or p.name in include_papers)
        )

    if limit is not None and len(paper_dirs) > limit:
        print(f"Limiting screen from {len(paper_dirs)} to {limit} papers (--limit)", file=sys.stderr)
        paper_dirs = paper_dirs[:limit]

    if not paper_dirs:
        print(f"No paper directories found in {papers_dir}", file=sys.stderr)
        return

    print(f"Screening {len(paper_dirs)} papers with {model}...", file=sys.stderr)

    positives: list[str] = []
    skipped = errors = 0
    total_cost = 0.0
    start = time.monotonic()

    def _screen_one(paper_dir: Path):
        try:
            result, usage = screen_paper(paper_dir, model=model, tag=tag, pdf_only=pdf_only)
            return paper_dir, result, usage, None
        except SkipPaper as e:
            return paper_dir, None, {}, str(e)
        except Exception as e:
            return paper_dir, None, {}, str(e)

    def _run_screen_batch(dirs_to_screen: list[Path], batch_name: str) -> list[Path]:
        """Run a screening pass; return dirs that failed due to usage limits."""
        usage_limit_failures = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_screen_one, d): d for d in dirs_to_screen}
            screen_start = time.monotonic()
            for i, future in enumerate(as_completed(futures), 1):
                paper_dir, result, usage, error = future.result()
                name = paper_dir.name
                elapsed_so_far = time.monotonic() - screen_start
                avg_sec = elapsed_so_far / i
                remaining_sec = avg_sec * (len(dirs_to_screen) - i)
                eta = _format_duration(remaining_sec) if i > 1 else "?"
                prefix = f"[{batch_name} {i}/{len(dirs_to_screen)} ETA {eta}]"
                nonlocal total_cost, skipped, errors
                total_cost += usage.get("cost_usd", 0)
                if error:
                    if "already exists" in error:
                        skipped += 1
                        existing = (paper_dir / tag / f"{name}_screen.json") if tag else (paper_dir / f"{name}_screen.json")
                        if existing.exists():
                            try:
                                data = json.loads(existing.read_text())
                                if data.get("is_replication"):
                                    positives.append(name)
                            except Exception:
                                pass
                        print(f"{prefix} SKIP  {name}", file=sys.stderr)
                    elif is_usage_limit_error(error):
                        print(f"{prefix} LIMIT {name}", file=sys.stderr)
                        usage_limit_failures.append(paper_dir)
                    else:
                        errors += 1
                        print(f"{prefix} FAIL  {name}: {error}", file=sys.stderr)
                else:
                    is_rep = result.get("is_replication", False)
                    conf = result.get("confidence", "?")
                    types = ", ".join(result.get("replication_types", []))
                    if is_rep:
                        positives.append(name)
                        print(f"{prefix} YES   {name}  [{conf}] {types}", file=sys.stderr)
                    else:
                        print(f"{prefix} no    {name}  [{conf}]", file=sys.stderr)
        return usage_limit_failures

    usage_limit_failures = _run_screen_batch(paper_dirs, "Screen")

    retry_attempt = 1
    while usage_limit_failures and retry_attempt <= 100 and not _shutdown_requested:
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"Session usage limit hit: {len(usage_limit_failures)} papers paused.", file=sys.stderr)
        print(f"Probing every 30 minutes until session resets...", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)
        while not _shutdown_requested:
            time.sleep(1800)
            print(f"[Probe] Checking if session limit has reset...", file=sys.stderr)
            if probe_session_available(model):
                print(f"[Probe] Session available — resuming {len(usage_limit_failures)} papers", file=sys.stderr)
                break
            print(f"[Probe] Still limited — waiting another 30 minutes", file=sys.stderr)
        usage_limit_failures = _run_screen_batch(usage_limit_failures, f"Retry-{retry_attempt}")
        retry_attempt += 1

    elapsed = time.monotonic() - start
    positive_list_path = papers_dir / f"screened_positive_{tag or 'untagged'}.txt"
    positive_list_path.write_text("\n".join(positives) + "\n")

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Screen complete: {len(positives)} positive / {len(paper_dirs) - skipped - errors} screened  |  {skipped} skipped  |  {errors} errors", file=sys.stderr)
    print(f"Total cost: ${total_cost:.4f}  |  Time: {_format_duration(elapsed)}", file=sys.stderr)
    print(f"Positive list → {positive_list_path}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)


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


def extract_batch(
    papers_dir: Path,
    model: str = "sonnet",
    workers: int = 1,
    output_file: Path | None = None,
    level: str = "base",
    skip_check: bool = False,
    tag: str | None = None,
    include_papers: set[str] | None = None,
    use_cursor: bool = False,
    pdf_only: bool = False,
    html_mode: bool = False,
    limit: int | None = None,
) -> list[dict]:
    """Process all paper directories under papers_dir.

    If include_papers is provided, only process directories whose names are in the set.
    """

    if html_mode:
        # For HTML mode, check for HTML files
        paper_dirs = sorted(
            p for p in papers_dir.iterdir()
            if p.is_dir() and any(p.glob("*.html"))
            and (include_papers is None or p.name in include_papers)
        )
    elif pdf_only:
        # For PDF-only mode, check for PDF files instead of abstract.md
        paper_dirs = sorted(
            p for p in papers_dir.iterdir()
            if p.is_dir() and any(p.glob("*.pdf"))
            and (include_papers is None or p.name in include_papers)
        )
    else:
        # Normal mode: include dirs with abstract.md OR XML-only dirs (auto-detected in extract_paper)
        paper_dirs = sorted(
            p for p in papers_dir.iterdir()
            if p.is_dir()
            and (
                (p / "abstract.md").exists()
                or (any(p.glob("*.xml")) and not any(p.glob("*.html")) and not any(p.glob("*.pdf")))
            )
            and (include_papers is None or p.name in include_papers)
        )

    if limit is not None and len(paper_dirs) > limit:
        print(f"Limiting batch from {len(paper_dirs)} to {limit} papers (--limit)", file=sys.stderr)
        paper_dirs = paper_dirs[:limit]

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
                use_cursor=use_cursor,
                pdf_only=pdf_only,
                html_mode=html_mode,
            )
            return (paper_dir, data, usage, None, log_msgs)
        except SkipPaper as e:
            return (paper_dir, "skip", None, str(e), [])
        except Exception as e:
            return (paper_dir, None, None, str(e), [])

    # Counters for batch summary (must be before process_batch_of_papers for nonlocal access)
    sanity_corrections = 0
    sanity_warnings = 0
    enrichment_rep_ok = 0
    enrichment_orig_doi = 0
    enrichment_title_found = 0

    def process_batch_of_papers(papers_to_process: list[Path], batch_name: str = "Initial") -> list[Path]:
        """Process a batch of papers and return list of papers that failed due to usage limits."""
        nonlocal sanity_corrections, sanity_warnings
        nonlocal enrichment_rep_ok, enrichment_orig_doi, enrichment_title_found
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
                    if "SANITY" in msg and "→" in msg:
                        sanity_corrections += 1
                    elif "SANITY" in msg:
                        sanity_warnings += 1
                    elif "📖  Replication:" in msg:
                        enrichment_rep_ok += 1
                    elif "+  Entry" in msg and "from DOI" in msg:
                        enrichment_orig_doi += 1
                    elif "+  Entry" in msg and "found DOI" in msg:
                        enrichment_title_found += 1

        return usage_limit_failures

    # Process initial batch
    usage_limit_failures = process_batch_of_papers(paper_dirs, "Initial")

    # Retry papers that hit usage limits
    retry_attempt = 1
    max_retries = 100  # Prevent infinite loops

    while usage_limit_failures and retry_attempt <= max_retries and not _shutdown_requested:
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"Session usage limit hit: {len(usage_limit_failures)} papers paused.", file=sys.stderr)
        print(f"Probing every 30 minutes until session resets...", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)

        # Probe until session available, checking every 30 minutes
        while not _shutdown_requested:
            time.sleep(1800)  # 30 minutes
            print(f"[Probe] Checking if session limit has reset...", file=sys.stderr)
            if probe_session_available(model):
                print(f"[Probe] Session available — resuming {len(usage_limit_failures)} papers", file=sys.stderr)
                break
            print(f"[Probe] Still limited — waiting another 30 minutes", file=sys.stderr)

        # Retry the failed papers
        retry_papers = usage_limit_failures
        usage_limit_failures = process_batch_of_papers(retry_papers, f"Retry-{retry_attempt}")

        retry_attempt += 1

    if usage_limit_failures:
        print(f"\nWarning: {len(usage_limit_failures)} papers still hitting usage limits after {max_retries} retries", file=sys.stderr)
        for paper_dir in usage_limit_failures:
            errors.append({"paper": paper_dir.name, "error": "Usage limit after max retries"})

    # Compute refinement, confidence stats from results
    refinement_count = sum(1 for r in results if r.get("usage", {}).get("refinement_round"))
    confidence_counts = {"high": 0, "medium": 0, "low": 0}
    total_entries = 0
    for r in results:
        for rep in r.get("replications", []):
            conf = rep.get("confidence", "").lower()
            if conf in confidence_counts:
                confidence_counts[conf] += 1
            total_entries += 1

    # Print usage summary
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
    if sanity_corrections or sanity_warnings:
        print(f"Validation: {sanity_corrections} auto-corrections, {sanity_warnings} warnings", file=sys.stderr)
    if enrichment_rep_ok or enrichment_orig_doi or enrichment_title_found:
        print(
            f"Enrichment: {enrichment_rep_ok} replication DOIs, "
            f"{enrichment_orig_doi} original DOIs enriched, "
            f"{enrichment_title_found} DOIs found from title",
            file=sys.stderr,
        )
    print(f"{'='*60}", file=sys.stderr)

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
        "original_n", "original_es", "original_es_type", "original_es_95_CI",
        "original_p_value", "original_p_value_type", "original_p_value_tails",
        "replication_n", "replication_es", "replication_es_type", "replication_es_95_CI",
        "replication_p_value", "replication_p_value_type", "replication_p_value_tails",
        "ai_version", "validated",
    ]

    rows = []
    for paper_dir in sorted(papers_dir.iterdir()):
        if not paper_dir.is_dir():
            continue

        # Find result JSON — look in tag subdir first, then paper dir
        search_dir = paper_dir / tag if tag and (paper_dir / tag).is_dir() else paper_dir
        result_file = None
        for suffix in ("_result_xml.json", "_result_html.json", "_result_pdf_only.json", "_result_full.json", "_result_mid.json", "_result.json"):
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

        replication_doi = paper_dir.name.replace("--", "/")

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

    return out_path


def main():
    # Register signal handler for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)

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
        choices=["base", "mid", "full"],
        default="full",
        help="Extraction level: base (core fields), mid (+ optional stats), full (all statistical details)",
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
        "--usecursor",
        action="store_true",
        help="Use `cursor agent` CLI instead of `claude` CLI",
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
        "--screen",
        action="store_true",
        help="Screening mode: run cheap Haiku pre-screen to identify papers likely containing replications. Writes _screen.json files and a screened_positive_<tag>.txt include-list.",
    )
    parser.add_argument(
        "--screen-model",
        type=str,
        default="haiku",
        help="Model to use for screening (default: haiku)",
    )

    args = parser.parse_args()

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

    if args.screen:
        screen_batch(
            args.path,
            model=args.screen_model,
            workers=args.workers,
            tag=args.tag,
            pdf_only=args.onlypdf,
            limit=args.limit,
            include_papers=include_papers,
        )
    elif args.batch:
        extract_batch(
            args.path,
            model=args.model,
            workers=args.workers,
            output_file=args.output,
            level=args.level,
            skip_check=args.dontcheck,
            tag=args.tag,
            include_papers=include_papers,
            use_cursor=args.usecursor,
            pdf_only=args.onlypdf,
            html_mode=args.html,
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
                use_cursor=args.usecursor,
                pdf_only=args.onlypdf,
                html_mode=args.html,
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
