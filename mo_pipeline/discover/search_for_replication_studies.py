"""
Step 1: Search the literature for candidate replication studies across disciplines.

Queries the following sources (opt in/out via --sources):
  - pubmed         : PubMed/MEDLINE (BioPython Entrez) — biomedical
  - openalex       : OpenAlex, filtered to 6 biomedical concept IDs
  - openalex_broad : OpenAlex, no concept filter — catches psych/econ/social sci
  - europepmc      : Europe PMC full-text (SRC:MED + SRC:PPR)
  - crossref       : Crossref /works — broad non-PubMed journal coverage
  - osf            : OSF preprints (PsyArXiv/SocArXiv/MetaArXiv)
  - semantic_scholar : Semantic Scholar — ML/CS reproducibility coverage

Outputs: data/candidates_raw.csv (append-only; dedup on DOI, PMID, normalized title)
Progress: progress/search_progress.json (per-query checkpoints; resumable)
"""

import argparse
import csv
import json
import re
import sys
import time

import requests
from Bio import Entrez, Medline

# Force line-buffered stdout so progress output streams to the terminal/log
# in real time instead of being batched by Python's default block buffering.
# Without this, `tail -f run.log` shows nothing for minutes at a time.
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

from mo_pipeline.config import (
    DATA_DIR, PROGRESS_DIR,
    ENTREZ_EMAIL, NCBI_DELAY, OPENALEX_DELAY, EUROPEPMC_DELAY,
    CROSSREF_DELAY, OSF_DELAY, S2_DELAY, S2_API_KEY,
    BIOMED_CONCEPT_IDS, CANDIDATES_RAW_CSV,
)

Entrez.email = ENTREZ_EMAIL

# Will be set from CLI args
MAX_PER_QUERY = 1000

# ── CSV columns ──────────────────────────────────────────────────────────────
FIELDNAMES = [
    "pmid", "doi", "title", "abstract", "authors",
    "journal", "year", "source_api", "source_query",
]

PROGRESS_FILE = PROGRESS_DIR / "search_progress.json"

# ═══════════════════════════════════════════════════════════════════════════════
# PubMed queries
# ═══════════════════════════════════════════════════════════════════════════════

PUBMED_QUERIES = [
    # Direct replication language
    '"replication study"[Title/Abstract]',
    '"replication of"[Title/Abstract]',
    '"failed to replicate"[Title/Abstract] OR "failure to replicate"[Title/Abstract]',
    '"direct replication"[Title/Abstract] OR "exact replication"[Title/Abstract]',
    '"attempted to replicate"[Title/Abstract] OR "replication attempt"[Title/Abstract]',
    '"we replicated"[Title/Abstract]',

    # Reproducibility language
    '"reproducibility of"[Title/Abstract] AND ("experiment"[Title/Abstract] OR "findings"[Title/Abstract])',
    '"failed to reproduce"[Title/Abstract] OR "could not reproduce"[Title/Abstract]',

    # Registered replication reports
    '"registered replication report"[Title/Abstract]',

    # Multi-site
    '"multi-site replication"[Title/Abstract] OR "multisite replication"[Title/Abstract]',

    # ── New queries (Phase 5a) ──
    '"close replication"[Title/Abstract]',
    '"replicability of"[Title/Abstract]',
    '"replicate the findings"[Title/Abstract]',
    '"reproduce the findings"[Title/Abstract]',
    '"Many Labs"[Title/Abstract]',
    '"replication project"[Title/Abstract]',
    '"conceptual replication"[Title/Abstract]',
    '"replication failure"[Title/Abstract]',
    '"original findings"[Title/Abstract] AND "replicat"[Title/Abstract]',

    # ── Phase 5c: additional phrasings ──
    '"replicated the result"[Title/Abstract] OR "replicated the results"[Title/Abstract]',
    '"replicate the effect"[Title/Abstract] OR "replicated the effect"[Title/Abstract]',
    '"preregistered replication"[Title/Abstract]',
    '"non-replication"[Title/Abstract] OR "non-replications"[Title/Abstract]',
    '"prior study"[Title/Abstract] AND "replicat"[Title/Abstract]',

    # ── Phase 5b: genetics / association-study replication language ──
    '"replication cohort"[Title/Abstract]',
    '"replication sample"[Title/Abstract]',
    '"replication dataset"[Title/Abstract]',
    '"discovery and replication"[Title/Abstract]',
    '"replicated the association"[Title/Abstract] OR "association was replicated"[Title/Abstract]',
    '"independent replication"[Title/Abstract] AND ("SNP"[Title/Abstract] OR "association"[Title/Abstract] OR "cohort"[Title/Abstract] OR "locus"[Title/Abstract])',
    '"two-stage"[Title/Abstract] AND ("GWAS"[Title/Abstract] OR "genome-wide association"[Title/Abstract])',
    '"replicated in" AND "independent cohort"[Title/Abstract]',
    '"meta-analysis"[Title/Abstract] AND "replication cohort"[Title/Abstract]',

    # ── Phase 6: negative/failure phrasings + registered reports ──
    '"did not replicate"[Title/Abstract] OR "does not replicate"[Title/Abstract]',
    '"unable to replicate"[Title/Abstract] OR "were unable to replicate"[Title/Abstract]',
    '"reproduce our findings"[Title/Abstract] OR "reproduce their findings"[Title/Abstract]',
    '"reanalysis of"[Title/Abstract] OR "re-analysis of"[Title/Abstract]',
    '"registered report"[Title/Abstract] AND "replicat"[Title/Abstract]',
    '"adversarial collaboration"[Title/Abstract]',
    # ── Phase 6: ML / CS reproducibility ──
    '"reproducibility study"[Title/Abstract] OR "reproducibility challenge"[Title/Abstract]',
]


# ═══════════════════════════════════════════════════════════════════════════════
# OpenAlex queries
# ═══════════════════════════════════════════════════════════════════════════════

# Two explicit blocks (replacing the old `[:15]` slice that fed OSF/S2). The
# genetics/GWAS block is biomedical-only and generates noise on the social-sci /
# CS-biased repositories (OSF, Semantic Scholar), so those sources get only the
# CORE block. OpenAlex + Crossref search both blocks.
REPLICATION_CORE = [
    "replication study",
    "failed to replicate",
    "reproducibility of",
    "replication attempt",
    "registered replication report",
    "direct replication",
    # ── Phase 5a ──
    "close replication",
    "conceptual replication",
    "replication failure",
    "replicability",
    "Many Labs",
    # ── Phase 5c: additional phrasings ──
    "replicated the result",
    "replicate the effect",
    "preregistered replication",
    "non-replication",

    # ── Phase 6: negative/failure phrasings (high recall, classifier filters) ──
    "did not replicate",
    "does not replicate",
    "unable to replicate",
    "reproduce our findings",
    "reanalysis",
    "Registered Report",           # broader than "registered replication report"
    "adversarial collaboration",

    # ── Phase 6: ML / CS reproducibility (retrieves ~100% — mostly OA/arXiv) ──
    "reproducibility study",
    "we reproduce",
    "reproducibility challenge",

    # ── Phase 6: ecology / poli-sci / sociology (retrieve 62–99%) ──
    "replication data",
    "reproducibility in ecology",

    # ── Phase 6: economics — NOTE only ~30% of econ replications are
    #    retrievable without RePEc/SSRN/NBER, so keep this minimal ──
    "computational reproducibility",
    "replication in economics",
]

GENETICS_QUERIES = [
    # ── Phase 5b: genetics / association-study replication language ──
    "replication cohort",
    "replication sample",
    "discovery and replication",
    "replicated the association",
    "association was replicated",
    "independent replication cohort",
    "two-stage genome-wide association",
]

# ═══════════════════════════════════════════════════════════════════════════════
# Europe PMC queries (supports full-text search)
# ═══════════════════════════════════════════════════════════════════════════════

EUROPEPMC_QUERIES = [
    '(BODY:"replication of" AND BODY:"original study") AND SRC:MED',
    '(BODY:"we replicated" OR BODY:"we attempted to replicate") AND SRC:MED',
    '(BODY:"failed to replicate" OR BODY:"failure to replicate") AND SRC:MED',
    '(TITLE:"replication study" OR TITLE:"replication of") AND SRC:MED',
    '(TITLE:"registered replication") AND SRC:MED',
    # ── New queries (Phase 5a) ──
    '(BODY:"close replication" OR BODY:"exact replication") AND SRC:MED',
    '(BODY:"replicate the findings" OR BODY:"reproduce the findings") AND SRC:MED',
    '(BODY:"Many Labs" OR BODY:"Registered Replication Report") AND SRC:MED',
    '(BODY:"replication attempt" AND BODY:"original study") AND SRC:MED',
    '(TITLE:"conceptual replication" OR TITLE:"close replication") AND SRC:MED',
    '(TITLE:"replication failure" OR TITLE:"replicability") AND SRC:MED',

    # ── Phase 5b: genetics / association-study replication language ──
    # EuropePMC BODY: search is especially powerful here because "replication cohort"
    # often appears in Methods/Results rather than Abstract.
    '(BODY:"replication cohort" OR BODY:"replication sample" OR BODY:"replication dataset") AND SRC:MED',
    '(BODY:"discovery and replication" OR (BODY:"discovery cohort" AND BODY:"replication cohort")) AND SRC:MED',
    '(BODY:"replicated the association" OR BODY:"association was replicated") AND SRC:MED',
    '(BODY:"independent replication" AND (BODY:"SNP" OR BODY:"association" OR BODY:"locus")) AND SRC:MED',
    '(TITLE:"replication cohort" OR TITLE:"replication sample") AND SRC:MED',
    '(BODY:"two-stage" AND BODY:"genome-wide association") AND SRC:MED',

    # ── Phase 5c: additional phrasings ──
    '(BODY:"replicated the result" OR BODY:"replicated the results") AND SRC:MED',
    '(BODY:"replicate the effect" OR BODY:"replicated the effect") AND SRC:MED',
    '(TITLE:"preregistered replication" OR BODY:"preregistered replication") AND SRC:MED',
    '(BODY:"non-replication" OR BODY:"non-replications") AND SRC:MED',
    '(BODY:"prior study" AND BODY:"replicat") AND SRC:MED',

    # ── Phase 6: negative phrasings + registered reports + reanalysis ──
    '(BODY:"did not replicate" OR BODY:"does not replicate" OR BODY:"were unable to replicate") AND SRC:MED',
    '(BODY:"reproduce our findings" OR BODY:"reproduce their findings") AND SRC:MED',
    '(BODY:"reanalysis of" OR BODY:"re-analysis of") AND BODY:"replicat" AND SRC:MED',
    '(TITLE:"registered report" AND BODY:"replicat") AND SRC:MED',
    '(BODY:"adversarial collaboration") AND SRC:MED',
    # ── Phase 6: ML / CS reproducibility ──
    '(TITLE:"reproducibility study" OR BODY:"reproducibility challenge") AND SRC:MED',

    # ── Preprints (bioRxiv / medRxiv / etc. indexed by Europe PMC as SRC:PPR) ──
    '(TITLE:"replication" OR ABSTRACT:"failed to replicate") AND SRC:PPR',
    '(TITLE:"replication study" OR TITLE:"replication of") AND SRC:PPR',
    '(TITLE:"reproducibility" OR ABSTRACT:"did not replicate") AND SRC:PPR',
]


# ── Runtime keyword overlay (editable from the dashboard) ────────────────────
# The four lists above are the code DEFAULTS. `data/keywords.json` (if present)
# overrides any of them per-list; the dashboard edits that file. Re-derive the
# combined lists here so overrides take effect.
from mo_pipeline.discover import keywords as _keywords  # noqa: E402
_eff = _keywords.apply_overrides({
    "replication_core": REPLICATION_CORE,
    "genetics_queries": GENETICS_QUERIES,
    "pubmed_queries": PUBMED_QUERIES,
    "europepmc_queries": EUROPEPMC_QUERIES,
})
REPLICATION_CORE = _eff["replication_core"]
GENETICS_QUERIES = _eff["genetics_queries"]
PUBMED_QUERIES = _eff["pubmed_queries"]
EUROPEPMC_QUERIES = _eff["europepmc_queries"]

# Per-source fan-out (mirrored by keywords.API_FANOUT): OpenAlex and Crossref
# search everything; OSF and Semantic Scholar (social-sci / CS biased) get the
# CORE block only, since the genetics/GWAS terms generate noise there.
OPENALEX_TITLE_SEARCHES = REPLICATION_CORE + GENETICS_QUERIES
SOCIAL_SCI_QUERIES = REPLICATION_CORE


# ═══════════════════════════════════════════════════════════════════════════════
# Progress helpers
# ═══════════════════════════════════════════════════════════════════════════════

def load_progress():
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"completed_queries": []}


def save_progress(progress):
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2))


# ── In-memory dedup tracker ──────────────────────────────────────────────────
seen_dois = set()     # lowercased DOIs
seen_pmids = set()    # PMID strings
seen_titles = set()   # normalized title strings


def _normalize_title(title):
    """Lowercase, strip punctuation, collapse whitespace for dedup."""
    if not title:
        return ""
    t = re.sub(r"[^\w\s]", " ", title.lower())
    return re.sub(r"\s+", " ", t).strip()


def load_seen_from_csv():
    """Populate seen sets from existing CSV on disk."""
    if not CANDIDATES_RAW_CSV.exists():
        return 0
    count = 0
    with open(CANDIDATES_RAW_CSV, "r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            _mark_seen(row)
            count += 1
    return count


def _mark_seen(row):
    """Add a row's identifiers to the seen sets."""
    doi = (row.get("doi") or "").strip().lower()
    pmid = (row.get("pmid") or "").strip()
    title = _normalize_title(row.get("title", ""))
    if doi:
        seen_dois.add(doi)
    if pmid:
        seen_pmids.add(pmid)
    if title and len(title) > 20:  # skip very short titles to avoid false matches
        seen_titles.add(title)


def _is_duplicate(row):
    """Check if a row is already seen by DOI, PMID, or normalized title."""
    doi = (row.get("doi") or "").strip().lower()
    if doi and doi in seen_dois:
        return True
    pmid = (row.get("pmid") or "").strip()
    if pmid and pmid in seen_pmids:
        return True
    title = _normalize_title(row.get("title", ""))
    if title and len(title) > 20 and title in seen_titles:
        return True
    return False


def dedup_and_append(rows):
    """Filter out duplicates, append new rows to CSV, update seen sets."""
    new_rows = []
    for row in rows:
        if not _is_duplicate(row):
            new_rows.append(row)
            _mark_seen(row)

    skipped = len(rows) - len(new_rows)
    if not new_rows:
        print(f"    -> 0 new rows (all {len(rows)} were duplicates)")
        return 0

    write_header = not CANDIDATES_RAW_CSV.exists()
    with open(CANDIDATES_RAW_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        for row in new_rows:
            writer.writerow({k: row.get(k, "") for k in FIELDNAMES})

    print(f"    -> appended {len(new_rows)} new rows ({skipped} duplicates skipped)")
    return len(new_rows)


# ═══════════════════════════════════════════════════════════════════════════════
# Shared HTTP + query-loop helpers
# ═══════════════════════════════════════════════════════════════════════════════

# Set by _http_get_json on a terminal failure. Every search function treats None
# as "no more pages" and returns what it has, so without this flag the query loop
# could not tell a failed query from an empty one and checkpointed both as done --
# a 429 meant the query was never retried.
_REQUEST_FAILED = False

# Minimum pause between queries (and before any retry) per API. Semantic Scholar's
# delay used to apply only between pages of one query.
_QUERY_DELAY = {"semantic_scholar": S2_DELAY}
_LEGACY_CAP = 1000  # MAX_PER_QUERY when checkpoints did not record their cap


def _http_get_json(url, params=None, headers=None, timeout=60, retries=3, retry_wait_429=None,
                   min_wait=0.0):
    """GET a JSON endpoint with exponential backoff on 429/5xx/network errors.
    retry_wait_429: fixed seconds to wait on 429 (overrides exponential backoff for that code).
    min_wait: floor for every retry wait (an API's own rate-limit delay).
    Returns parsed JSON on success, None on terminal failure (and sets _REQUEST_FAILED)."""
    global _REQUEST_FAILED
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt == retries - 1:
                print(f"    giving up after {retries} attempts: {type(e).__name__}: {e}")
                _REQUEST_FAILED = True
                return None
            backoff = max(2 ** attempt, min_wait)
            print(f"    {type(e).__name__}, retrying in {backoff}s")
            time.sleep(backoff)
            continue
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                print(f"    JSON decode error from {url}")
                _REQUEST_FAILED = True
                return None
        if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
            if r.status_code == 429 and retry_wait_429 is not None:
                backoff = retry_wait_429
            else:
                backoff = 2 ** attempt
            backoff = max(backoff, min_wait)
            print(f"    HTTP {r.status_code}, retrying in {backoff}s")
            time.sleep(backoff)
            continue
        print(f"    HTTP {r.status_code} (terminal) from {url}: {r.text[:200]}")
        _REQUEST_FAILED = True
        return None
    _REQUEST_FAILED = True
    return None


def _run_query_loop(api_name, queries, fetch_fn, progress):
    """Shared driver: for each query, skip if completed else fetch -> tag -> dedup_and_append -> save.

    A query counts as completed only if no request failed, and only for the cap it
    ran under: raising --max-per-query re-runs queries checkpointed at a lower cap.
    Rows from a failed query are still kept (appending is deduplicated).
    """
    global _REQUEST_FAILED
    total, failed = 0, 0
    n = len(queries)
    caps = progress.setdefault("query_caps", {})
    delay = _QUERY_DELAY.get(api_name, 0.0)
    for i, q in enumerate(queries, start=1):
        key = f"{api_name}:{q}"
        if key in progress["completed_queries"] and caps.get(key, _LEGACY_CAP) >= MAX_PER_QUERY:
            print(f"  [skip {i}/{n}] {key[:60]}...")
            continue
        print(f"  [{i}/{n}] {api_name} query: {str(q)[:80]}")
        _REQUEST_FAILED = False
        records = fetch_fn(q)
        for rec in records:
            rec["source_api"] = api_name
            rec["source_query"] = str(q)
        total += dedup_and_append(records)
        if _REQUEST_FAILED:
            failed += 1
            print(f"    request failed; NOT checkpointing {key[:60]} (kept {len(records)} rows, will retry next run)")
        else:
            if key not in progress["completed_queries"]:
                progress["completed_queries"].append(key)
            caps[key] = MAX_PER_QUERY
        save_progress(progress)
        if delay:
            time.sleep(delay)
    print(f"  {api_name} total new rows: {total}" + (f"; {failed} queries failed, will retry" if failed else ""))
    return total


# ═══════════════════════════════════════════════════════════════════════════════
# PubMed search
# ═══════════════════════════════════════════════════════════════════════════════

def search_pubmed(query, max_results=None):
    """Return list of PMIDs matching the query."""
    limit = max_results or MAX_PER_QUERY
    handle = Entrez.esearch(db="pubmed", term=query, retmax=limit)
    record = Entrez.read(handle)
    handle.close()
    total = record.get("Count", "?")
    pmids = record.get("IdList", [])
    print(f"  PubMed: {len(pmids)} fetched ({total} total) for: {query[:80]}...")
    return pmids


def fetch_pubmed_metadata(pmid_list, batch_size=200):
    """Fetch title, abstract, authors, journal, year, DOI for a list of PMIDs."""
    records = []
    for i in range(0, len(pmid_list), batch_size):
        batch = pmid_list[i : i + batch_size]
        ids = ",".join(batch)
        handle = Entrez.efetch(db="pubmed", id=ids, rettype="medline", retmode="text")
        for rec in Medline.parse(handle):
            doi = ""
            # DOI is often in the AID field
            for aid in rec.get("AID", []):
                if aid.endswith("[doi]"):
                    doi = aid.replace(" [doi]", "").strip()
                    break
            records.append({
                "pmid": rec.get("PMID", ""),
                "doi": doi,
                "title": rec.get("TI", ""),
                "abstract": rec.get("AB", ""),
                "authors": "; ".join(rec.get("AU", [])),
                "journal": rec.get("TA", ""),
                "year": rec.get("DP", "")[:4],
            })
        handle.close()
        time.sleep(NCBI_DELAY)
        if (i // batch_size) % 10 == 0 and i > 0:
            print(f"    fetched {i + len(batch)}/{len(pmid_list)} PubMed records")
    return records


def _pubmed_fetch(query):
    pmids = search_pubmed(query)
    time.sleep(NCBI_DELAY)
    return fetch_pubmed_metadata(pmids) if pmids else []


def run_pubmed_searches(progress):
    return _run_query_loop("pubmed", PUBMED_QUERIES, _pubmed_fetch, progress)


# ═══════════════════════════════════════════════════════════════════════════════
# OpenAlex search
# ═══════════════════════════════════════════════════════════════════════════════

def search_openalex(title_query, concept_filter=True, max_results=None):
    """Search OpenAlex for works matching a title query, optionally filtered to biomed concepts."""
    limit = max_results or MAX_PER_QUERY
    base_url = "https://api.openalex.org/works"
    concept_ids = "|".join(BIOMED_CONCEPT_IDS)

    # Use title.search for tighter matching (not full-text "search")
    filters = ["type:article"]
    if concept_filter:
        filters.append(f"concepts.id:{concept_ids}")
    filters.append(f"title.search:{title_query}")

    params = {
        "filter": ",".join(filters),
        "per_page": 200,
        "cursor": "*",
        "mailto": ENTREZ_EMAIL,  # OpenAlex polite pool
    }

    results = []
    pages = 0
    while True:
        data = _http_get_json(base_url, params=params)
        if data is None:
            print(f"  OpenAlex: stopping on '{title_query}' after network/HTTP error")
            break
        works = data.get("results", [])
        if not works:
            break

        for w in works:
            doi = (w.get("doi") or "").replace("https://doi.org/", "")
            authors = "; ".join(
                a.get("author", {}).get("display_name", "")
                for a in w.get("authorships", [])
            )
            results.append({
                "pmid": "",  # OpenAlex doesn't always have PMID directly
                "doi": doi,
                "title": w.get("title", ""),
                "abstract": _reconstruct_abstract(w.get("abstract_inverted_index")),
                "authors": authors,
                "journal": ((w.get("primary_location") or {}).get("source") or {}).get("display_name", ""),
                "year": str(w.get("publication_year", "")),
            })

        pages += 1
        total_available = data.get("meta", {}).get("count", "?")
        print(f"    page {pages}: {len(results)} fetched so far (of {total_available} available, limit {limit})")
        if len(results) >= limit:
            break
        next_cursor = data.get("meta", {}).get("next_cursor")
        if not next_cursor:
            break
        params["cursor"] = next_cursor
        time.sleep(OPENALEX_DELAY)

    print(f"  OpenAlex: {len(results)} results for '{title_query}' ({pages} pages)")
    return results


def _reconstruct_abstract(inverted_index):
    """Reconstruct abstract text from OpenAlex inverted index format."""
    if not inverted_index:
        return ""
    word_positions = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort()
    return " ".join(w for _, w in word_positions)


def run_openalex_searches(progress):
    """OpenAlex filtered to biomedical concepts."""
    return _run_query_loop(
        "openalex", OPENALEX_TITLE_SEARCHES,
        lambda q: search_openalex(q, concept_filter=True), progress,
    )


def run_openalex_broad_searches(progress):
    """OpenAlex unfiltered — catches psychology, economics, social sciences, etc.
    Uses a separate progress namespace so it can run independently of the biomed-filtered pass."""
    return _run_query_loop(
        "openalex_broad", OPENALEX_TITLE_SEARCHES,
        lambda q: search_openalex(q, concept_filter=False), progress,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Europe PMC search (full-text capable)
# ═══════════════════════════════════════════════════════════════════════════════

def search_europepmc(query, max_results=None):
    """Search Europe PMC. Supports full-text (BODY:) queries."""
    max_results = max_results or MAX_PER_QUERY
    base_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    results = []
    cursor = "*"

    while True:
        params = {
            "query": query,
            "format": "json",
            "pageSize": 1000,
            "cursorMark": cursor,
            "resultType": "core",  # includes abstract
        }
        data = _http_get_json(base_url, params=params, timeout=30)
        if data is None:
            print("  Europe PMC: stopping query after network/HTTP error")
            break
        result_list = data.get("resultList", {}).get("result", [])
        if not result_list:
            break

        for item in result_list:
            doi = item.get("doi", "") or ""
            results.append({
                "pmid": item.get("pmid", "") or "",
                "doi": doi,
                "title": item.get("title", ""),
                "abstract": item.get("abstractText", ""),
                "authors": item.get("authorString", ""),
                "journal": item.get("journalTitle", ""),
                "year": str(item.get("pubYear", "")),
            })

        if len(results) >= max_results:
            break
        next_cursor = data.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        time.sleep(EUROPEPMC_DELAY)

    print(f"  Europe PMC: {len(results)} results for: {query[:60]}...")
    return results


def run_europepmc_searches(progress):
    return _run_query_loop("europepmc", EUROPEPMC_QUERIES, search_europepmc, progress)


# ═══════════════════════════════════════════════════════════════════════════════
# Crossref /works
# ═══════════════════════════════════════════════════════════════════════════════

_CROSSREF_HEADERS = {"User-Agent": f"MetascienceObservatory/1.0 (mailto:{ENTREZ_EMAIL})"}


def search_crossref(title_query, max_results=None):
    """Search Crossref /works by title. Uses cursor deep-pagination; polite-pool User-Agent."""
    max_results = max_results or MAX_PER_QUERY
    base_url = "https://api.crossref.org/works"
    results = []
    cursor = "*"
    pages = 0
    while True:
        params = {
            "query.title": title_query,
            "rows": 500,
            "cursor": cursor,
        }
        data = _http_get_json(base_url, params=params, headers=_CROSSREF_HEADERS)
        if data is None:
            break
        msg = data.get("message", {})
        items = msg.get("items", [])
        if not items:
            break
        for item in items:
            title_list = item.get("title") or [""]
            abstract_raw = item.get("abstract") or ""
            abstract = re.sub(r"<[^>]+>", "", abstract_raw).strip() if abstract_raw else ""
            authors_list = item.get("author") or []
            authors = "; ".join(
                f"{a.get('given','')} {a.get('family','')}".strip()
                for a in authors_list
            )
            container = item.get("container-title") or []
            date_parts = ((item.get("issued") or {}).get("date-parts") or [[None]])[0]
            year = str(date_parts[0]) if date_parts and date_parts[0] else ""
            results.append({
                "pmid": "",
                "doi": (item.get("DOI") or "").lower(),
                "title": title_list[0] if title_list else "",
                "abstract": abstract,
                "authors": authors,
                "journal": container[0] if container else "",
                "year": year,
            })
        pages += 1
        total_available = msg.get("total-results", "?")
        print(f"    page {pages}: {len(results)} fetched so far (of {total_available} available, limit {max_results})")
        if len(results) >= max_results:
            break
        next_cursor = msg.get("next-cursor")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        time.sleep(CROSSREF_DELAY)
    print(f"  Crossref: {len(results)} results for '{title_query}' ({pages} pages)")
    return results


def run_crossref_searches(progress):
    return _run_query_loop("crossref", OPENALEX_TITLE_SEARCHES, search_crossref, progress)


# ═══════════════════════════════════════════════════════════════════════════════
# OSF preprints (PsyArXiv / SocArXiv / MetaArXiv / etc.)
# ═══════════════════════════════════════════════════════════════════════════════

def search_osf(query, max_results=None):
    """Search OSF preprints. /v2/preprints/ doesn't offer full-text search, but
    `filter[title]=...` and `filter[description]=...` each do substring match;
    we union both to approximate free-text."""
    max_results = max_results or MAX_PER_QUERY
    base_url = "https://api.osf.io/v2/preprints/"
    results = []
    seen_ids = set()
    for filter_field in ("title", "description"):
        page = 1
        while True:
            params = {
                f"filter[{filter_field}]": query,
                "page[size]": 100,
                "page": page,
            }
            data = _http_get_json(base_url, params=params)
            if data is None:
                break
            items = data.get("data", [])
            if not items:
                break
            for item in items:
                item_id = item.get("id")
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                attrs = item.get("attributes") or {}
                rels = item.get("relationships") or {}
                provider_id = (((rels.get("provider") or {}).get("data")) or {}).get("id", "osf")
                date_published = attrs.get("date_published") or ""
                results.append({
                    "pmid": "",
                    "doi": (attrs.get("doi") or "").lower(),
                    "title": attrs.get("title", "") or "",
                    "abstract": attrs.get("description", "") or "",
                    "authors": "",  # available only via a secondary call per record; skip for now
                    "journal": provider_id,
                    "year": date_published[:4] if date_published else "",
                })
            if len(results) >= max_results:
                break
            links = data.get("links") or {}
            if not links.get("next"):
                break
            page += 1
            time.sleep(OSF_DELAY)
        if len(results) >= max_results:
            break
    print(f"  OSF: {len(results)} results for: {query[:60]}")
    return results


def run_osf_searches(progress):
    return _run_query_loop("osf", SOCIAL_SCI_QUERIES, search_osf, progress)


# ═══════════════════════════════════════════════════════════════════════════════
# Semantic Scholar /graph/v1/paper/search
# ═══════════════════════════════════════════════════════════════════════════════

_S2_FIELDS = "title,abstract,authors,year,venue,externalIds"
_S2_HEADERS = {"x-api-key": S2_API_KEY} if S2_API_KEY else None


def search_semantic_scholar(query, max_results=None):
    """Search Semantic Scholar. Paginates by offset; API hard-caps at offset+limit <= 1000."""
    max_results = max_results or MAX_PER_QUERY
    base_url = "https://api.semanticscholar.org/graph/v1/paper/search"
    results = []
    offset = 0
    limit = 100
    while offset < 1000:  # S2 hard limit
        params = {
            "query": query,
            "limit": limit,
            "offset": offset,
            "fields": _S2_FIELDS,
        }
        data = _http_get_json(base_url, params=params, headers=_S2_HEADERS,
                             retries=6, retry_wait_429=15, min_wait=S2_DELAY)
        if data is None:
            break
        items = data.get("data") or []
        if not items:
            break
        for item in items:
            ext = item.get("externalIds") or {}
            authors_list = item.get("authors") or []
            results.append({
                "pmid": str(ext.get("PubMed") or ""),
                "doi": (ext.get("DOI") or "").lower(),
                "title": item.get("title", "") or "",
                "abstract": item.get("abstract", "") or "",
                "authors": "; ".join(a.get("name", "") for a in authors_list),
                "journal": item.get("venue", "") or "",
                "year": str(item.get("year") or ""),
            })
        if len(results) >= max_results:
            break
        next_offset = data.get("next")
        if next_offset is None or next_offset == offset:
            break
        offset = next_offset
        time.sleep(S2_DELAY)
    print(f"  Semantic Scholar: {len(results)} results for: {query[:60]}")
    return results


def run_semantic_scholar_searches(progress):
    return _run_query_loop("semantic_scholar", SOCIAL_SCI_QUERIES, search_semantic_scholar, progress)


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

SOURCE_RUNNERS = {
    "pubmed":           ("PubMed Searches",                           run_pubmed_searches),
    "openalex":         ("OpenAlex Searches (biomed concept filter)", run_openalex_searches),
    "openalex_broad":   ("OpenAlex Searches (broad / all fields)",    run_openalex_broad_searches),
    "europepmc":        ("Europe PMC Searches",                       run_europepmc_searches),
    "crossref":         ("Crossref /works Searches",                  run_crossref_searches),
    "osf":              ("OSF Preprints Searches",                    run_osf_searches),
    "semantic_scholar": ("Semantic Scholar Searches",                 run_semantic_scholar_searches),
}

DEFAULT_SOURCES = [
    "pubmed", "openalex", "openalex_broad", "europepmc",
    "crossref", "osf", "semantic_scholar",
]


def main(sources=None):
    if sources is None:
        sources = DEFAULT_SOURCES

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)

    progress = load_progress()
    grand_total = 0

    # Load existing rows into dedup tracker
    existing = load_seen_from_csv()
    if existing:
        print(f"Loaded {existing} existing candidates into dedup tracker "
              f"({len(seen_dois)} DOIs, {len(seen_pmids)} PMIDs, {len(seen_titles)} titles)")
        grand_total += existing

    for src in sources:
        if src not in SOURCE_RUNNERS:
            print(f"  (unknown source '{src}' skipped; known: {list(SOURCE_RUNNERS)})")
            continue
        title, runner = SOURCE_RUNNERS[src]
        print(f"\n=== {title} ===")
        grand_total += runner(progress)

    print(f"\nDone. Total candidates in {CANDIDATES_RAW_CSV.name}: ~{grand_total} (before dedup)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Search for replication studies across multiple APIs")
    parser.add_argument(
        "--max-per-query", type=int, default=1000,
        help="Max results to fetch per query (default: 1000)"
    )
    parser.add_argument(
        "--sources", type=str, default=",".join(DEFAULT_SOURCES),
        help=f"Comma-separated sources (known: {','.join(SOURCE_RUNNERS)}; default: {','.join(DEFAULT_SOURCES)})",
    )
    args = parser.parse_args()
    MAX_PER_QUERY = args.max_per_query
    print(f"Max per query: {MAX_PER_QUERY}")
    print(f"Sources: {args.sources}")
    main(sources=args.sources.split(","))
