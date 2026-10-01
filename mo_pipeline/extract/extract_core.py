#!/usr/bin/env python3
"""Single-shot core-fields extractor: stage 7 without statistics.

One no-tools model call per paper. Python assembles the input (abstract + full
text + reference list, from the same tier ladder extract.py uses), the model
replies with the JSON envelope, and the same validators, DOI enrichment and
collate as extract.py run on the result. The 14 statistical fields are never
requested and are stripped if the model emits them anyway.

Compared with extract.py's agentic mode: one request instead of ~10 agent
turns, no refinement round, no PDF table reads, and a provider switch
(claude_cli or openrouter) through discover/screening_backend.py.

Output, per paper, under papers/{doi}/{tag}/:
    {folder}_result_core.json   the record (ai_version "<prompt version>-core")
    debug_log.json              provider/model, assembly stats, usage, raw reply
    provenance.json             the same sidecar extract.py writes

Usage:
    python -m mo_pipeline.extract.extract_core <papers_dir> --tag core_v1 [--workers 4]
        [--include-list F] [--limit N] [--dontcheck] [--provider claude_cli|openrouter]
        [--model M] [--max-input-chars N] [--collate-only] [--show-prompt]
    python -m mo_pipeline.extract.extract_core <paper_dir> --tag core_smoke --dontcheck
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import signal
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from mo_pipeline import config
from mo_pipeline.corpus.models import folder_to_doi_url
from mo_pipeline.discover.screening_backend import (BACKENDS, BackendUnavailable, get_backend,
                                                    parse_json_reply)
from mo_pipeline.extract.extract import (
    RESULT_SUFFIXES, STAT_FIELDS, SkipPaper, _extract_doi_from_url, _format_duration, _husk_reason,
    _write_provenance, collate_results, enrich_metadata, is_usage_limit_error,
    load_existing_replication_urls, load_system_prompt, load_version_number,
    normalize_doi_url, paper_artifacts, probe_session_available,
    validate_citation_sentences, validate_extraction, validate_original_dois,
)

LEVEL = "core"
RESULT_SUFFIX = "_result_core.json"
CORE_FIELDS = (
    "original_url", "original_authors", "original_title", "original_journal",
    "original_volume", "original_issue", "original_pages", "original_year",
    "description", "result", "replication_type", "discipline", "subdiscipline",
    "confidence", "explanation", "citation_sentence",
)
REFS_CAP = 300            # reference entries
REFS_MAX_CHARS = 60_000
MIN_BODY_BUDGET = 20_000

_shutdown = False


def _signal_handler(signum, frame):
    global _shutdown
    if _shutdown:
        print("\nForce quit.", file=sys.stderr)
        sys.exit(1)
    _shutdown = True
    print("\nShutdown requested: finishing in-flight papers, starting no new ones.",
          file=sys.stderr)


def _setting(name: str):
    """config.<name>, overridable by an env var of the same name (typed like the default)."""
    default = getattr(config, name)
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return type(default)(raw) if not isinstance(default, str) else raw


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


# ── reference list ──────────────────────────────────────────────────────────

def _format_structured_ref(ref: dict) -> str:
    authors = ref.get("authors") or []
    if isinstance(authors, list):
        authors = "; ".join(str(a) for a in authors if a)
    parts = []
    if authors:
        parts.append(str(authors).strip())
    if ref.get("year"):
        parts.append(f"({ref['year']})")
    title = str(ref.get("title") or "").strip()
    if title:
        parts.append(title.rstrip(".") + ".")
    venue = str(ref.get("journal") or "").strip()
    if venue:
        loc = venue
        if ref.get("volume"):
            loc += f" {ref['volume']}"
        if ref.get("issue"):
            loc += f"({ref['issue']})"
        if ref.get("pages"):
            loc += f": {ref['pages']}"
        parts.append(loc + ".")
    if ref.get("doi"):
        parts.append(f"doi:{ref['doi']}")
    return " ".join(parts).strip()


def render_references(refs: list, cap: int = REFS_CAP) -> str:
    """One numbered line per entry, for both references.json shapes.

    GROBID: {id, authors[], title, journal, volume, issue, pages, year, doi}
    (some entries carry the whole citation in `title` with everything else
    null; that renders as-is). Elsevier (the retired xml_to_markdown backfill): {"raw"}.
    The GROBID `id` is kept as the number because numeric citation styles
    refer to it.
    """
    lines: list[str] = []
    for ref in refs:
        if len(lines) >= cap:
            break
        if isinstance(ref, str):
            text, num = ref.strip(), None
        elif isinstance(ref, dict):
            num = ref.get("id")
            text = str(ref["raw"]).strip() if ref.get("raw") else _format_structured_ref(ref)
        else:
            continue
        if not text:
            continue
        lines.append(f"[{num if num not in (None, '') else len(lines) + 1}] {text}")
    return "\n".join(lines)


_BIB_OPEN = re.compile(r"<(ref-list|ce:bibliography|bibliography)\b", re.IGNORECASE)
_BIB_ENTRY_END = re.compile(r"</(ref|ce:bib-reference|sb:reference)>", re.IGNORECASE)


def _xml_reflist_text(xml_path: Path) -> str:
    """Tag-stripped bibliography of a raw JATS / Elsevier XML, one entry per line."""
    raw = _read(xml_path)
    m = _BIB_OPEN.search(raw)
    if not m:
        return ""
    end = raw.find(f"</{m.group(1)}>", m.end())
    chunk = raw[m.start(): end if end != -1 else len(raw)]
    chunk = _BIB_ENTRY_END.sub("\n", chunk)
    text = html.unescape(re.sub(r"<[^>]+>", " ", chunk))
    lines = (re.sub(r"\s+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if len(line) > 3)


def _pdf_text(pdf: Path, max_chars: int, tail_only: bool = False) -> str:
    """PyMuPDF text in reading order. tail_only = the last quarter of the pages."""
    import fitz  # pymupdf
    doc = fitz.open(str(pdf))
    n = doc.page_count
    start = max(0, (n * 3) // 4) if tail_only else 0
    out: list[str] = []
    total = 0
    for i in range(start, n):
        t = doc[i].get_text("text", sort=True)
        out.append(t)
        total += len(t)
        if total >= max_chars:
            break
    return "".join(out)[:max_chars]


def reference_block(paper_dir: Path, art: dict) -> tuple[str, str]:
    """(text, source) down the ladder: references.json > references.md >
    raw XML bibliography > PDF tail pages > none."""
    rj = paper_dir / "references.json"
    if rj.exists():
        try:
            refs = json.loads(_read(rj))
        except json.JSONDecodeError:
            refs = []
        if isinstance(refs, list) and refs:
            text = render_references(refs)
            if text:
                return text[:REFS_MAX_CHARS], "references.json"
    rm = paper_dir / "references.md"
    if rm.exists():
        text = _read(rm).strip()
        if text:
            return text[:REFS_MAX_CHARS], "references.md"
    raw = art.get("structured_raw")
    if raw is not None and raw.suffix.lower() == ".xml":
        text = _xml_reflist_text(raw)
        if text:
            return text[:REFS_MAX_CHARS], raw.name
    pdf = art.get("pdf")
    if pdf is not None:
        try:
            text = _pdf_text(pdf, REFS_MAX_CHARS, tail_only=True).strip()
        except Exception:
            text = ""
        if text:
            return text, f"{pdf.name} (tail pages)"
    return "", "none"


# ── input assembly ──────────────────────────────────────────────────────────

def _truncate_middle(text: str, budget: int) -> tuple[str, bool]:
    """Keep head 55% + tail 45% so the Discussion survives; mark the gap."""
    if len(text) <= budget:
        return text, False
    head = int(budget * 0.55)
    tail = budget - head
    omitted = len(text) - head - tail
    marker = (f"\n\n[... {omitted:,} characters omitted from the middle of the "
              f"full text ...]\n\n")
    return text[:head] + marker + text[-tail:], True


def assemble_input(paper_dir: Path, art: dict, max_chars: int) -> tuple[str, dict]:
    """The user message: [PAPER DOI], [ABSTRACT] (grobid tier only),
    [FULL TEXT: tier], [REFERENCE LIST: source]. Returns (prompt, info)."""
    tier = art["primary_tier"] or "pdf"
    sections = ["Extract replication data from the paper below.",
                f"[PAPER DOI]\n{folder_to_doi_url(paper_dir.name)}"]
    if art["primary"] is not None:
        body = _read(art["primary"])
        if tier == "grobid":
            abstract = _read(paper_dir / "abstract.md").strip()
            if abstract:
                sections.append(f"[ABSTRACT]\n{abstract}")
        refs_text, refs_source = reference_block(paper_dir, art)
        primary_name = art["primary"].name
    else:
        # PDF-only folder: the whole PDF is the text, its bibliography included.
        body = _pdf_text(art["pdf"], max_chars * 3)
        refs_text, refs_source = "", "end of the PDF text"
        primary_name = art["pdf"].name
    refs_text = refs_text[:REFS_MAX_CHARS]
    reserved = sum(len(s) for s in sections) + len(refs_text) + 400
    body, truncated = _truncate_middle(body.strip(), max(MIN_BODY_BUDGET, max_chars - reserved))
    sections.append(f"[FULL TEXT: {tier}]\n{body}")
    if refs_text:
        sections.append(f"[REFERENCE LIST: {refs_source}]\n{refs_text}")
    elif tier == "pdf":
        sections.append("[REFERENCE LIST: end of the PDF text]\n"
                        "The PDF text above ends with the paper's own reference list.")
    else:
        sections.append("[REFERENCE LIST: none]\nNo separate reference list is available "
                        "for this paper; use the inline citations.")
    sections.append("Reply with the JSON object only.")
    prompt = "\n\n".join(sections)
    info = {"tier": tier, "primary_file": primary_name, "refs_source": refs_source,
            "chars": len(prompt), "truncated": truncated}
    return prompt, info


# ── post-processing ─────────────────────────────────────────────────────────

def postprocess(data, paper_dir: Path, ai_version: str) -> dict:
    """Coerce the envelope, strip statistical keys, fill missing core keys,
    inject replication_url / ai_version (as extract.py does)."""
    if not isinstance(data, dict):
        raise ValueError("reply is not a JSON object")
    reps = data.get("replications")
    if reps is None:
        reps = []
    elif isinstance(reps, dict):
        reps = [reps]
    if not isinstance(reps, list):
        raise ValueError("`replications` is not a list")
    reps = [r for r in reps if isinstance(r, dict)]
    contains = data.get("contains_replications")
    if not isinstance(contains, bool):
        contains = bool(reps)
    replication_url = folder_to_doi_url(paper_dir.name)
    for rep in reps:
        for k in STAT_FIELDS:
            rep.pop(k, None)
        for k in CORE_FIELDS:
            v = rep.get(k)
            if v is None:
                rep[k] = ""
            elif not isinstance(v, str):
                rep[k] = json.dumps(v) if isinstance(v, (list, dict)) else str(v)
        rep["replication_url"] = replication_url
        rep["ai_version"] = ai_version
    return {"contains_replications": contains, "replications": reps}


def _grobid_refs(paper_dir: Path) -> list[dict] | None:
    """references.json when GROBID-shaped (year/authors keys), else None:
    validate_citation_sentences can only corroborate that shape."""
    rj = paper_dir / "references.json"
    if not rj.exists():
        return None
    try:
        refs = json.loads(_read(rj))
    except json.JSONDecodeError:
        return None
    if isinstance(refs, list) and refs and all(isinstance(r, dict) and "year" in r for r in refs):
        return refs
    return None


# ── one paper ───────────────────────────────────────────────────────────────

def extract_paper_core(paper_dir: Path, backend, system_prompt: str, existing_urls=None,
                       tag: str | None = None, max_chars: int | None = None,
                       ) -> tuple[dict, dict, list[str]]:
    """One no-tools call for one paper. Returns (data, usage, log_messages).
    Raises SkipPaper (already done / already in the DB) or RuntimeError."""
    paper_dir = paper_dir.resolve()
    log: list[str] = []
    output_dir = paper_dir / tag if tag else paper_dir
    max_chars = max_chars or _setting("EXTRACT_CORE_MAX_INPUT_CHARS")

    for suffix in RESULT_SUFFIXES:
        existing = output_dir / f"{paper_dir.name}{suffix}"
        if existing.exists():
            raise SkipPaper(f"Output already exists: {existing.name}")
    doi_url = normalize_doi_url(folder_to_doi_url(paper_dir.name))
    if existing_urls and doi_url in existing_urls:
        raise SkipPaper(f"Already in dataset: {doi_url}")

    art = paper_artifacts(paper_dir)
    if not art["has_fulltext"]:
        raise FileNotFoundError(
            f"No readable full text in {paper_dir} (expected a *_from_xml.md / "
            f"*_from_html.md rendition, a body.md, or a PDF)")
    husk = _husk_reason(paper_dir, art)
    if husk:
        raise FileNotFoundError(f"No article text in {paper_dir}: {husk}")
    user_prompt, info = assemble_input(paper_dir, art, max_chars)
    log.append(f"  tier: {info['tier']}; refs: {info['refs_source']}; "
               f"{info['chars']:,} chars{' (truncated)' if info['truncated'] else ''}")

    output_dir.mkdir(exist_ok=True)
    start = time.monotonic()
    attempts = []
    data = None
    for attempt in range(2):
        r = backend.complete(system_prompt, user_prompt, cwd=paper_dir)
        attempts.append(r)
        if r.fatal:
            # Misconfigured backend: every remaining paper would fail the same
            # way, so stop the batch rather than book 200 identical failures.
            raise BackendUnavailable(r.error)
        if r.error:
            if r.returncode not in (None, 0):
                # extract.py's message shape: an empty body after ":\n" is how
                # is_usage_limit_error recognises a silent session-limit exit.
                combined = (r.stderr + r.stdout).strip()
                raise RuntimeError(f"{backend.name} CLI failed for {paper_dir}:\n{combined}")
            raise RuntimeError(f"{backend.name} failed for {paper_dir}: {r.error}")
        data = parse_json_reply(r.text)
        if data is not None:
            break
        log.append(f"  ⚠️  reply was not parseable JSON (attempt {attempt + 1} of 2)")
    wall_ms = int((time.monotonic() - start) * 1000)

    usage = {"model": backend.model, "input_tokens": 0, "output_tokens": 0,
             "cache_creation_tokens": 0, "cache_read_tokens": 0, "cost_usd": 0.0,
             "duration_ms": 0, "num_turns": 0}
    for r in attempts:
        for k, v in r.usage.items():
            if k == "model":
                usage["model"] = v
            elif isinstance(v, (int, float)):
                usage[k] = usage.get(k, 0) + v
    usage.update({"wall_time_ms": wall_ms, "attempts": len(attempts), "tier": info["tier"],
                  "refs_source": info["refs_source"], "truncated": info["truncated"]})
    ai_version = f"{load_version_number()}-{LEVEL}"
    debug = {
        "provider": backend.name, "model": usage["model"], "prompt_level": LEVEL,
        "prompt_version": ai_version, "assembly": info, "usage": usage,
        "assistant_text": attempts[-1].text[:50_000],
        "envelope": {k: v for k, v in (attempts[-1].raw or {}).items() if k != "result"},
    }
    (output_dir / "debug_log.json").write_text(json.dumps(debug, indent=2, default=str))
    if data is None:
        why = ("the reply was truncated at the output cap (raise "
               "EXTRACT_CORE_MAX_OUTPUT_TOKENS)" if attempts[-1].truncated
               else "the reply was not valid JSON")
        raise RuntimeError(f"{backend.name}: {why} for {paper_dir}:\n{attempts[-1].text[:500]}")

    data = postprocess(data, paper_dir, ai_version)
    data, msgs = validate_extraction(data)
    log.extend(msgs)

    doi_cache: dict = {}
    if not _shutdown:
        mismatches, doi_cache = validate_original_dois(data)
        for mm in mismatches:
            log.append(
                f"  ❌  DOI mismatch entry {mm['entry_idx']} (similarity={mm['similarity']}) "
                f"— agent: \"{mm['agent_title'][:60]}\" vs API: \"{mm['api_title'][:60]}\"")
        for cm in validate_citation_sentences(data, _grobid_refs(paper_dir)):
            parts = []
            if not cm["author_found"]:
                parts.append("author not found in citation")
            if not cm["year_found"]:
                parts.append("year not found in citation")
            log.append(
                f"  ❌  Citation mismatch entry {cm['entry_idx']} ({', '.join(parts)}) "
                f"— citation: \"{cm['citation'][:80]}\" vs extracted: "
                f"\"{cm['extracted_authors'][:40]}\" ({cm['extracted_year']})")
        try:
            data, enrich_msgs = enrich_metadata(
                data,
                replication_doi=_extract_doi_from_url(folder_to_doi_url(paper_dir.name)) or "",
                original_doi_cache=doi_cache)
            log.extend(enrich_msgs)
        except Exception as e:
            log.append(f"  !  Metadata enrichment failed: {e}")

    _write_provenance(output_dir, model_id=usage["model"], prompt_level=LEVEL, artifacts=art,
                      html_mode=False, pdf_only=False, force_tier=None,
                      dontcheck=existing_urls is None)
    (output_dir / f"{paper_dir.name}{RESULT_SUFFIX}").write_text(json.dumps(data, indent=2))
    return data, usage, log


# ── batch ───────────────────────────────────────────────────────────────────

def looks_like_batch(path: Path) -> bool:
    """Is this a directory OF paper folders, rather than one paper?

    Decided by what the subdirectories are, never by whether a PDF happens to
    sit in this directory. The corpus root holds a few loose stage-6 leftovers,
    so "contains a PDF" reported the whole corpus as a single paper: one doomed
    extraction, and --include-list silently ignored. Returns on the first paper
    folder it sees, so it costs one readdir on a slow drive.
    """
    try:
        for child in path.iterdir():
            if child.is_dir() and paper_artifacts(child)["has_fulltext"]:
                return True
    except OSError:
        pass
    return False


def discover_papers(papers_dir: Path, include: set[str] | None, limit: int | None) -> list[Path]:
    """Same rule as extract_batch: any folder with readable full text."""
    dirs = sorted(p for p in papers_dir.iterdir()
                  if p.is_dir() and (include is None or p.name in include)
                  and paper_artifacts(p)["has_fulltext"])
    if limit is not None and len(dirs) > limit:
        print(f"Limiting batch from {len(dirs)} to {limit} papers (--limit)", file=sys.stderr)
        dirs = dirs[:limit]
    return dirs


def extract_core_batch(papers_dir: Path, backend, *, workers: int = 4, tag: str | None = None,
                       include_papers: set[str] | None = None, limit: int | None = None,
                       skip_check: bool = False, max_chars: int | None = None) -> dict:
    paper_dirs = discover_papers(papers_dir, include_papers, limit)
    if not paper_dirs:
        print(f"No paper directories found in {papers_dir}", file=sys.stderr)
        return {"results": [], "skipped": [], "errors": [], "total_usage": {}}
    existing_urls = None if skip_check else load_existing_replication_urls()
    system_prompt = load_system_prompt(LEVEL)
    print(f"Found {len(paper_dirs)} papers; provider={backend.name} model={backend.model} "
          f"workers={workers} tag={tag or '(none)'}", file=sys.stderr)

    results: list[dict] = []
    skipped: list[str] = []
    errors: list[dict] = []
    totals: Counter = Counter()
    tiers: Counter = Counter()
    refs: Counter = Counter()
    n_truncated = 0

    def process_one(pd: Path):
        if _shutdown:
            return pd, "skip", None, "Shutdown requested", []
        try:
            data, usage, log = extract_paper_core(pd, backend, system_prompt, existing_urls,
                                                  tag, max_chars)
            return pd, data, usage, None, log
        except SkipPaper as e:
            return pd, "skip", None, str(e), []
        except BackendUnavailable:
            raise                      # abort the batch; retrying cannot help
        except Exception as e:
            return pd, None, None, str(e), []

    def run_batch(dirs: list[Path], name: str) -> list[Path]:
        nonlocal n_truncated
        limit_failures: list[Path] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(process_one, d): d for d in dirs}
            for i, fut in enumerate(as_completed(futures), 1):
                pd, data, usage, err, log = fut.result()
                prefix = f"[{name} {i}/{len(dirs)}]"
                if data == "skip":
                    print(f"{prefix} SKIP  {pd.name}: {err}", file=sys.stderr)
                    skipped.append(pd.name)
                elif err:
                    if is_usage_limit_error(err):
                        print(f"{prefix} LIMIT {pd.name}: {err}", file=sys.stderr)
                        limit_failures.append(pd)
                    else:
                        print(f"{prefix} FAIL  {pd.name}: {err}", file=sys.stderr)
                        errors.append({"paper": pd.name, "error": err})
                else:
                    for k in ("input_tokens", "output_tokens", "cost_usd", "wall_time_ms"):
                        totals[k] += usage.get(k, 0)
                    tiers[usage.get("tier", "?")] += 1
                    refs[usage.get("refs_source", "?")] += 1
                    n_truncated += bool(usage.get("truncated"))
                    n = len(data.get("replications", []))
                    label = f"{n} replication(s)" if data.get("contains_replications") else "no replications"
                    print(f"{prefix} OK    {pd.name}: {label}  "
                          f"({usage['input_tokens'] + usage['output_tokens']:,} tokens, "
                          f"${usage['cost_usd']:.4f}, {usage['wall_time_ms'] / 1000:.1f}s)",
                          file=sys.stderr)
                    results.append({"paper": pd.name, "usage": usage, **data})
                for msg in log:
                    print(f"{prefix} {pd.name}{msg}", file=sys.stderr)
        return limit_failures

    limit_failures = run_batch(paper_dirs, "Initial")
    attempt = 1
    while limit_failures and attempt <= 100 and not _shutdown:
        if backend.name == "claude_cli":
            print(f"\nSession usage limit hit: {len(limit_failures)} papers paused; "
                  f"probing every 30 minutes until it lifts.", file=sys.stderr)
            while not _shutdown:
                time.sleep(1800)
                if probe_session_available(backend.model):
                    break
                print("  still limited...", file=sys.stderr)
        else:
            print(f"\nProvider rate limit hit: {len(limit_failures)} papers paused; "
                  f"retrying in 5 minutes.", file=sys.stderr)
            time.sleep(300)
        if _shutdown:
            break
        limit_failures = run_batch(limit_failures, f"Retry {attempt}")
        attempt += 1
    for pd in limit_failures:
        errors.append({"paper": pd.name, "error": "usage limit (not retried)"})

    n_ok = len(results)
    print(f"\n{'=' * 60}\nDone: {n_ok} extracted, {len(skipped)} skipped, {len(errors)} failed",
          file=sys.stderr)
    if n_ok:
        print(f"  tokens: {totals['input_tokens']:,} in / {totals['output_tokens']:,} out; "
              f"cost ${totals['cost_usd']:.2f}; mean wall "
              f"{totals['wall_time_ms'] / n_ok / 1000:.1f}s/paper", file=sys.stderr)
        print(f"  tiers: {dict(tiers)}", file=sys.stderr)
        print(f"  reference list source: {dict(refs)}  (none: {refs.get('none', 0)}); "
              f"truncated inputs: {n_truncated}", file=sys.stderr)
    for e in errors[:20]:
        print(f"  FAIL {e['paper']}: {e['error'][:160]}", file=sys.stderr)
    collate_results(papers_dir, tag=tag)
    return {"results": results, "skipped": skipped, "errors": errors, "total_usage": dict(totals)}


# ── CLI ─────────────────────────────────────────────────────────────────────

def make_backend(provider: str | None, model: str | None):
    provider = provider or _setting("EXTRACT_CORE_PROVIDER")
    if provider not in BACKENDS:
        sys.exit(f"unknown --provider {provider!r}; expected one of {sorted(BACKENDS)}")
    kwargs: dict = {"timeout": _setting("EXTRACT_CORE_TIMEOUT_SEC")}
    if provider == "claude_cli":
        model = model or _setting("EXTRACT_CORE_MODEL")
    else:
        if not model:
            sys.exit("--provider openrouter needs an explicit --model slug "
                     "(e.g. anthropic/claude-sonnet-4.6)")
        kwargs["max_tokens"] = _setting("EXTRACT_CORE_MAX_OUTPUT_TOKENS")
    return get_backend(provider=provider, model=model, **kwargs)


def main() -> int:
    signal.signal(signal.SIGINT, _signal_handler)
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path, help="papers directory (batch) or one paper folder")
    ap.add_argument("--tag", default=None, help="run tag: outputs go to <paper>/<tag>/ (per-run resume)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--batch", action="store_true",
                      help="path is a directory of paper folders (auto-detected; pass this to be sure)")
    mode.add_argument("--single", action="store_true", help="path is one paper folder")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--include-list", type=Path, default=None,
                    help="file of paper folder names, one per line")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dontcheck", action="store_true",
                    help="do not skip papers already in the production DB")
    ap.add_argument("--provider", choices=sorted(BACKENDS), default=None,
                    help=f"default config.EXTRACT_CORE_PROVIDER ({config.EXTRACT_CORE_PROVIDER})")
    ap.add_argument("--model", default=None,
                    help="claude_cli alias (default config.EXTRACT_CORE_MODEL) or an "
                         "OpenRouter slug (required with --provider openrouter)")
    ap.add_argument("--max-input-chars", type=int, default=None,
                    help=f"default config.EXTRACT_CORE_MAX_INPUT_CHARS ({config.EXTRACT_CORE_MAX_INPUT_CHARS})")
    ap.add_argument("--collate-only", action="store_true",
                    help="only collate existing result JSONs for the tag into a CSV")
    ap.add_argument("--show-prompt", action="store_true",
                    help="print the assembled user prompt for the first paper and exit (no model call)")
    args = ap.parse_args()
    start = time.monotonic()

    if args.collate_only:
        collate_results(args.path, tag=args.tag)
        return 0
    include = None
    if args.include_list:
        include = {l.strip() for l in args.include_list.read_text().splitlines() if l.strip()}
    if args.batch or args.single:
        single = args.single
    else:
        single = not looks_like_batch(args.path)
    if single and not paper_artifacts(args.path)["has_fulltext"]:
        sys.exit(f"{args.path} holds neither readable full text nor any paper folder. "
                 f"Pass --batch if it is a directory of paper folders.")

    if args.show_prompt:
        target = args.path if single else next(iter(discover_papers(args.path, include, 1)), None)
        if target is None:
            sys.exit("no extractable paper found")
        prompt, info = assemble_input(target, paper_artifacts(target),
                                      args.max_input_chars or _setting("EXTRACT_CORE_MAX_INPUT_CHARS"))
        print(json.dumps(info), file=sys.stderr)
        print(prompt)
        return 0

    backend = make_backend(args.provider, args.model)
    if single:
        existing = None if args.dontcheck else load_existing_replication_urls()
        try:
            data, usage, log = extract_paper_core(args.path, backend, load_system_prompt(LEVEL),
                                                  existing, args.tag, args.max_input_chars)
            for m in log:
                print(m, file=sys.stderr)
            print(f"OK: {len(data.get('replications', []))} replication(s); "
                  f"{usage['input_tokens'] + usage['output_tokens']:,} tokens, "
                  f"${usage['cost_usd']:.4f}, {usage['wall_time_ms'] / 1000:.1f}s", file=sys.stderr)
        except SkipPaper as e:
            print(f"SKIP: {e}", file=sys.stderr)
        collate_results(args.path.resolve().parent, tag=args.tag)
    else:
        extract_core_batch(args.path, backend, workers=args.workers, tag=args.tag,
                           include_papers=include, limit=args.limit,
                           skip_check=args.dontcheck, max_chars=args.max_input_chars)
    print(f"Runtime: {_format_duration(time.monotonic() - start)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
