#!/usr/bin/env python3
"""
Phase 3: Download full text for all confirmed replications that are:
  (a) not already ingested
  (b) not already downloaded anywhere

Uses the public `fetchpdf` library (../fetchpdf_public; OSF, SSRN, Figshare,
PsychArchives, PMC, Unpaywall, Crossref, EuropePMC, Semantic Scholar, OpenAlex,
CORE, Elsevier TDM, ...) plus the grey last resorts from `fetchpdf_grey`
(Sci-Hub; `--legalonly` turns them off), with parallel batch processing.

Layout: one folder per record, `inbox/{stem}/{stem}.*` (fetchpdf's
--make-subfolder), named exactly as the record's future papers/{stem}/ folder --
fetchpdf's doi_to_safe_filename and corpus.models.doi_to_folder are one
encoding. Stage 7 moves the PDF across by stem and `corpus adopt-structured`
moves the rest. Run-level files (failed_dois.csv, missing_pdfs.html) stay at the
inbox root. Records downloaded flat before this layout are moved into place by
`python -m mo_pipeline.corpus inbox-subfolders --execute`.

Formats: structured full text AND the PDF, not one or the other. fetchpdf's
--get-xml-or-html walk fills two goals per record -- a structured copy
({stem}.xml, else publisher {stem}.fulltext.html) and {stem}.pdf -- so a paper
that offers both ends up with both. XML is the preferred input for LLM
extraction (tables keep their row/column structure, and the glyph-corruption
class that broken PDF ToUnicode CMaps cause does not exist in markup); the PDF
is still always attempted, since figures, supplements and the rendered page
only come from it. `--no-download-xml` restores the old PDF-only chain.

Renditions: what stage 8 actually reads is the Markdown rendition
{stem}_from_xml.md / {stem}_from_html.md. After each batch this script renders
the records it touched through the corpus prose gate (`corpus.render.render_dirs`:
fetchpdf's converter, behind render.MIN_PROSE_LINE) -- never through fetchpdf's
own --to-markdown, which writes unconditionally while stage 8 trusts any
rendition it finds. `--no-to-markdown` skips the pass; `python -m
mo_pipeline.corpus render-markdown --execute` does the same over everything
already on the drive.

Because goals are filled PER GOAL from what is already on disk, the same walk
doubles as a backfill: `--backfill-structured` fetches only the missing XML/HTML
half and `--backfill-pdf` only the missing PDF half for records already on the
drive (papers/ and inbox/), touching nothing that exists. See `run_backfill`.

Every run ends with fetchpdf's per-source cost table (track_source): calls, hits
and seconds per source, grey sources included, plus source_tracking.csv and
source_counts.json at the inbox root.

Downloads are prioritized by replication type:
  direct > close > conceptual > systematic > multi-site > other

Usage (run from the repo root):
  python -m mo_pipeline.discover.download_all_confirmed              # all types
  python -m mo_pipeline.discover.download_all_confirmed --type direct
  python -m mo_pipeline.discover.download_all_confirmed --type direct,close
  python -m mo_pipeline.discover.download_all_confirmed --limit 100
  python -m mo_pipeline.discover.download_all_confirmed --workers 8
  python -m mo_pipeline.discover.download_all_confirmed --download-si
  python -m mo_pipeline.discover.download_all_confirmed --backfill-structured --backfill-pdf
"""

import argparse
import csv
import os
import sys
from collections import Counter
from pathlib import Path

csv.field_size_limit(sys.maxsize)

# Through the shim (CLAUDE.md invariant 7): it imports fetchpdf_grey first, which
# loads the Elsevier key and installs the grey last-resort hook on fetch_pdf.
from mo_pipeline.shared.fetch import batch_fetch_pdfs, disable_last_resorts

from mo_pipeline.config import (
    CONFIRMED_REPLICATIONS_CSV as CONFIRMED_CSV,
    INGESTED_ROOT as INGESTED_DIR,
    INBOX_DIR as OUTPUT_DIR,
    PAPERS_DIR,
    PDF_SEARCH_DIRS as _BASE_PDF_SEARCH_DIRS,
)
from mo_pipeline.corpus.models import doi_to_folder, folder_to_doi, is_doi_folder

# ── Configuration ────────────────────────────────────────────────────────────

# New PDFs land in inbox/ awaiting conversion (stage 7 moves them into papers/).
# Include the inbox in the search dirs so restarts skip already-downloaded files.
PDF_SEARCH_DIRS = list(_BASE_PDF_SEARCH_DIRS) + [OUTPUT_DIR]

TYPE_PRIORITY = ["direct", "close", "conceptual", "systematic", "multi-site"]

# The artifact suffixes fetchpdf writes that count as "we have this paper".
# Must stay in step with fetchpdf.retrieval.tiers.TIER_EXTENSIONS: ".xml" is T1,
# ".fulltext.html" is T2. ".landing.html" is deliberately absent -- a landing
# page is not full text. Longest-first so "{doi}.fulltext.html" strips to
# "{doi}" and not to "{doi}.fulltext".
STRUCTURED_SUFFIXES = (".fulltext.html", ".xml")
ARTIFACT_SUFFIXES = STRUCTURED_SUFFIXES + (".pdf",)


def _artifact_stem(name):
    """(stem, suffix) for a full-text artifact filename, else (None, None).

    Longest suffix first, so "{doi}.fulltext.html" yields "{doi}" rather than
    "{doi}.fulltext".
    """
    lower = name.lower()
    for suffix in ARTIFACT_SUFFIXES:
        if len(lower) > len(suffix) and lower.endswith(suffix):
            return lower[: -len(suffix)], suffix
    return None, None


def _scan_artifacts_into(root, stems, structured=None):
    """Recursively walk `root` with os.scandir and add lowercase artifact stems.

    Adds every stem that has a PDF, an XML or a publisher HTML full text to
    `stems`; when `structured` is given, the XML/HTML ones also land there. The
    two sets are what separate "we already have this paper" from "we already
    have the structured half of this paper" -- the second is what the
    --backfill-structured run needs and what a PDF-only index cannot answer.

    os.scandir is significantly faster than pathlib.Path.rglob because it
    avoids Path object construction and reuses cached stat info from the
    DirEntry objects. Uses an explicit stack to avoid recursion overhead.

    Emits a heartbeat line every ~2 seconds so large scans (e.g. 4000+
    paper folders) don't look hung.
    """
    import time as _time
    stack = [str(root)]
    files_seen = 0
    start = _time.time()
    last_heartbeat = start
    while stack:
        current = stack.pop()
        try:
            it = os.scandir(current)
        except (PermissionError, FileNotFoundError, OSError):
            continue
        with it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        files_seen += 1
                        stem, suffix = _artifact_stem(entry.name)
                        if stem is not None:
                            stems.add(stem)
                            if structured is not None and suffix != ".pdf":
                                structured.add(stem)
                except (PermissionError, FileNotFoundError, OSError):
                    continue
        now = _time.time()
        if now - last_heartbeat > 2.0:
            print(
                f"    ... {files_seen:,} files scanned, "
                f"{len(stems):,} records found "
                f"({now - start:.0f}s, {len(stack)} dirs queued)",
                flush=True,
            )
            last_heartbeat = now


def _seed_stems_from_catalog(stems):
    """Add every DOI already in the corpus catalog as a stem (fast, no drive walk).

    Replaces the old recursive scan of ingested/ (now reorganized into papers/):
    one indexed query instead of walking thousands of folders on a slow drive.
    """
    from mo_pipeline.config import CATALOG_PATH
    if not CATALOG_PATH.exists():
        return 0
    from mo_pipeline.corpus import catalog
    conn = catalog.connect()
    try:
        n = 0
        for (doi,) in conn.execute("SELECT doi FROM papers"):
            stems.add(doi_to_folder(doi).lower())
            n += 1
        return n
    finally:
        conn.close()


def build_existing_stems():
    """Stems that exist somewhere, and the subset that has XML/HTML.

    Returns (stems, structured). The catalog seed can only fill the first: the
    corpus catalog indexes status, not which formats a folder holds, so a
    catalog-seeded run knows a paper is on the drive but not whether it has a
    structured copy. Only the directory walks below fill `structured`, which is
    why --backfill-structured walks papers/ itself instead of querying sqlite.
    """
    import time as _time
    stems = set()
    structured = set()

    t0 = _time.time()
    print("  seeding from corpus catalog...", flush=True)
    n = _seed_stems_from_catalog(stems)
    if n:
        print(f"    ✓ {n} DOIs from catalog ({_time.time()-t0:.1f}s)", flush=True)
    elif INGESTED_DIR.exists():
        # Pre-reorg fallback: walk the legacy ingested/ tree.
        print(f"  no catalog; scanning {INGESTED_DIR}...", flush=True)
        _scan_artifacts_into(INGESTED_DIR, stems, structured)
    print(f"    ✓ {len(stems)} total after corpus ({_time.time()-t0:.1f}s)", flush=True)

    for d in PDF_SEARCH_DIRS:
        if not d.exists():
            print(f"  skipping {d} (does not exist)", flush=True)
            continue
        t0 = _time.time()
        before = len(stems)
        print(f"  scanning {d}...", flush=True)
        _scan_artifacts_into(d, stems, structured)
        print(f"    ✓ +{len(stems)-before} new (total {len(stems)}, {_time.time()-t0:.1f}s)", flush=True)

    return stems, structured


def _fetch_kwargs(args):
    """The fetchpdf flags shared by the normal and backfill runs."""
    kwargs = {
        # Two goals per record -- structured (XML, else publisher HTML) AND the
        # PDF -- instead of stopping at the best single format. Off restores the
        # source-ordered PDF chain exactly as it was.
        "get_xml_or_html": args.download_xml,
        # One folder per record, named as papers/{stem}/ will be. This is also
        # what lets a later run fill a record's missing half from disk.
        "make_subfolder": True,
        # Renditions come from _render_records, behind the prose gate; fetchpdf's
        # own conversion writes unconditionally.
        "to_markdown": False,
        # Every run ends with fetchpdf's per-source cost table (calls, hits,
        # seconds, dearest per hit first) and leaves source_tracking.csv +
        # source_counts.json at the inbox root. Where the hours go is the only
        # basis for deciding which source to cut next.
        "track_source": True,
    }
    if args.download_si or args.refresh_si:
        kwargs["pull_supplementary"] = True
        kwargs["max_supplementary_bytes"] = int(args.max_si_mb * 1024 * 1024)
        if args.refresh_si:
            kwargs["refresh_supplementary"] = True
    return kwargs


def _describe_formats(args):
    if args.download_xml:
        print("   Formats: structured (XML > publisher HTML) + PDF")
    else:
        print("   Formats: PDF only (--no-download-xml)")
    if args.download_si or args.refresh_si:
        print(f"   Supplementary files: yes (cap {args.max_si_mb:g} MB/file"
              f"{', refreshing existing manifests' if args.refresh_si else ''})")
    if args.to_markdown:
        print("   Rendering XML/HTML to {stem}_from_xml.md / {stem}_from_html.md "
              "after the batch, behind the prose gate")


def _report(results):
    """Print the shared tail of a batch: successes, formats, failures."""
    if not results:
        print("No records attempted.")
        return
    successes = [r for r in results if r[1]]
    got_structured = sum(
        1 for r in successes
        if r[2] and str(r[2]).lower().endswith(STRUCTURED_SUFFIXES)
    )
    print(f"\n{'='*60}")
    print(f"Results: {len(successes)} succeeded, {len(results)-len(successes)} failed")
    print(f"Success rate: {len(successes)/len(results)*100:.1f}%")
    # fetchpdf returns the best artifact per record and the structured goal is
    # ordered first, so a structured path here means the XML/HTML half is in
    # hand. A PDF path does not prove the structured half is missing -- when the
    # structured goal was already filled from disk it is reported as the hit.
    print(f"Structured (XML/HTML) artifact returned for {got_structured} of "
          f"{len(successes)} successes")


def _folders_missing(root, suffixes, allow=None):
    """DOI folders under `root` that hold no file ending in one of `suffixes`.

    One scandir per record folder, not a recursive walk of the whole tree: every
    paper folder also holds body.md, references.json and friends that this
    question does not care about.
    """
    import time as _time
    missing, seen, start, last = [], 0, _time.time(), _time.time()
    try:
        entries = list(os.scandir(root))
    except OSError as e:
        print(f"  cannot scan {root}: {e}")
        return missing
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False) or not is_doi_folder(entry.name):
            continue
        stem = entry.name.lower()
        if allow is not None and stem not in allow:
            continue
        seen += 1
        has_it = False
        try:
            with os.scandir(entry.path) as it:
                for f in it:
                    if f.name.lower().endswith(suffixes):
                        has_it = True
                        break
        except OSError:
            continue
        if not has_it:
            missing.append(entry.name)
        now = _time.time()
        if now - last > 2.0:
            print(f"    ... {seen:,} folders checked, {len(missing):,} without "
                  f"{'/'.join(suffixes)} ({now - start:.0f}s)", flush=True)
            last = now
    return missing


def _render_records(results, args):
    """Write the prose-gated Markdown rendition for every record a batch touched.

    Goes through corpus.render rather than fetchpdf's to_markdown= so that a
    rendition failing render.MIN_PROSE_LINE never lands on disk: stage 8 reads
    any {stem}_from_xml.md it finds as the primary full text, unchecked. The
    record folders come from the result paths, not from re-encoding the DOIs,
    so this cannot disagree with where fetchpdf actually wrote.
    """
    if not args.to_markdown:
        return None
    dirs = sorted({Path(r[2]).parent for r in results if r[1] and r[2]})
    if not dirs:
        return None
    from mo_pipeline.corpus import render
    summary = render.render_dirs(dirs, execute=True)
    print(f"Markdown renditions: {summary['converted']} written, "
          f"{summary['rejected_no_prose']} rejected by the prose gate, "
          f"{summary['already_present']} already present, "
          f"{summary['failed']} failed")
    for example in summary["rejected_examples"]:
        print(f"  rejected: {example}")
    return summary


def run_backfill(args):
    """Fetch the missing half -- XML/HTML, PDF, or both -- for records on the drive.

    papers/ and inbox/ share one shape (one folder per record holding
    {stem}.pdf, {stem}.xml, ...), which is exactly what --make-subfolder
    reproduces, so fetchpdf fills the goal already on disk and walks only the
    other (fetchpdf.retrieval.engine._fill_goals_from_disk). Nothing that exists
    is downloaded again.

    Records are pre-filtered here rather than handed wholesale to fetchpdf
    because its phase-1 identifier resolution runs *before* the already-on-disk
    check -- passing all ~13k corpus DOIs would mean ~13k resolutions to
    discover that most of them have nothing left to fetch.
    """
    if not args.download_xml:
        sys.exit("Backfills fill the structured and PDF goals; --no-download-xml cancels them.")
    wanted = []
    if args.backfill_structured:
        wanted.append(("XML/HTML", STRUCTURED_SUFFIXES))
    if args.backfill_pdf:
        wanted.append(("PDF", (".pdf",)))

    allow = None
    if args.doi_csv:
        from mo_pipeline.discover.doi_runs import extract_dois_from_text
        parsed = extract_dois_from_text(Path(args.doi_csv).read_text(errors="replace"))
        allow = {doi_to_folder(d).lower() for d in parsed["dois"]}
        print(f"Restricting the backfill to {len(allow)} DOIs from {args.doi_csv}")

    remaining = args.limit
    all_results = []
    for root in (PAPERS_DIR, OUTPUT_DIR):
        if not root.is_dir():
            continue
        stems = set()
        for label, suffixes in wanted:
            print(f"Scanning {root} for records with no {label}...")
            missing = _folders_missing(root, suffixes, allow)
            print(f"  {len(missing)} records need a {label}")
            stems.update(missing)
        stems = sorted(stems)
        if remaining is not None:
            stems = stems[:max(0, remaining)]
        dois = []
        for stem in stems:
            try:
                dois.append(folder_to_doi(stem))
            except Exception as e:
                print(f"  skipping unparseable folder {stem}: {e}")
        if not dois:
            continue
        print(f"\n🚀 Backfilling {len(dois)} records in {root} "
              f"with {args.workers} workers...")
        _describe_formats(args)
        results = batch_fetch_pdfs(
            dois=dois,
            output_dir=str(root),
            verbose=True,
            workers=args.workers,
            delay=args.delay,
            # A record with no XML anywhere is not a missing PDF, and the report
            # would land in the corpus root next to papers/.
            create_missing_report=False,
            **_fetch_kwargs(args),
        )
        all_results.extend(results)
        _render_records(results, args)
        if remaining is not None:
            remaining -= len(dois)
            if remaining <= 0:
                break

    _report(all_results)
    if all_results:
        print("Re-index the corpus when this finishes: "
              "python -m mo_pipeline.corpus scan")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--type",
        help="Filter by replication type. Single value or comma-separated list "
             "(e.g. 'direct', 'direct,close', 'direct,close,conceptual')",
    )
    parser.add_argument(
        "--doi-csv",
        help="Download the DOIs listed in this CSV instead of the confirmed-replications "
             "set (DOI column auto-detected; used by dashboard DOI runs). --type is ignored.",
    )
    parser.add_argument("--limit", type=int, help="Max number of DOIs to attempt")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers (default: 4)")
    parser.add_argument("--delay", type=float, default=0.2, help="Delay per worker (default: 0.2s)")
    parser.add_argument("--legalonly", action="store_true", help="Only use legal download sources (no Sci-Hub)")
    parser.add_argument(
        "--download-xml", dest="download_xml", action="store_true", default=True,
        help="Keep a structured copy (XML, else publisher HTML) AND the PDF for "
             "every record. On by default; XML is the preferred extraction input.",
    )
    parser.add_argument(
        "--no-download-xml", dest="download_xml", action="store_false",
        help="PDF only: the old source-ordered chain, no structured half.",
    )
    parser.add_argument(
        "--backfill-structured", action="store_true",
        help="Fetch the missing XML/HTML for records ALREADY on the drive "
             "(papers/ + inbox/) instead of downloading new ones. Re-downloads "
             "no PDF. --type is ignored; --doi-csv narrows it; --limit caps it.",
    )
    parser.add_argument(
        "--backfill-pdf", action="store_true",
        help="Fetch the missing PDF for records already on the drive that have "
             "only XML/HTML (grey last resorts included unless --legalonly). "
             "Combine with --backfill-structured to fill both halves in one pass.",
    )
    parser.add_argument(
        "--download-si", action="store_true",
        help="Also download every supplementary file each record offers, as "
             "{stem}_supplementary_info_N.ext siblings plus a JSON manifest.",
    )
    parser.add_argument(
        "--refresh-si", action="store_true",
        help="Re-run the supplementary pass over records that already have a "
             "manifest, picking up files deposited since. Implies --download-si.",
    )
    parser.add_argument(
        "--max-si-mb", type=float, default=300,
        help="Per-file cap for --download-si, in MB (default: 300).",
    )
    parser.add_argument(
        "--to-markdown", dest="to_markdown", action="store_true", default=True,
        help="After each batch, render the retrieved XML/HTML to "
             "{stem}_from_xml.md / {stem}_from_html.md behind the corpus prose "
             "gate (render.MIN_PROSE_LINE). On by default.",
    )
    parser.add_argument(
        "--no-to-markdown", dest="to_markdown", action="store_false",
        help="Skip the rendition pass; `python -m mo_pipeline.corpus "
             "render-markdown --execute` does it later.",
    )
    parser.add_argument(
        "--cookies", metavar="FILE", default=None,
        help="Institutional access from this cookie file. Without it, fetchpdf "
             "uses the access `get-cookies setup` configured, if any (off until "
             "then). Tried after the fast open-access sources, for Wiley, T&F, "
             "SAGE, Springer, Royal Society, Hogrefe and INFORMS.",
    )
    parser.add_argument(
        "--no-cookies", action="store_true",
        help="Do not use institutional access this run, even if it is set up.",
    )
    parser.add_argument(
        "--cookies-only", action="store_true",
        help="Institutional access alone, no other source (grey ones included): "
             "for DOIs whose chain already failed. PDF-only.",
    )
    parser.add_argument(
        "--cookies-max", type=int, default=5000,
        help="Most PDFs fetched through institutional access in one run (default: 5000).",
    )
    args = parser.parse_args()

    from fetchpdf.retrieval import institutional
    if args.cookies and not os.path.isfile(args.cookies):
        parser.error(f"--cookies: no such file: {args.cookies}")
    if args.no_cookies:
        institutional.disable_cookies()
    elif args.cookies:
        institutional.use_cookies_file(args.cookies)
    institutional.set_max_downloads(args.cookies_max)
    access = institutional.active_cookies_file()
    if args.cookies_only:
        if not access:
            parser.error("--cookies-only: institutional access is not set up "
                         "(run `get-cookies setup`, or pass --cookies FILE)")
        institutional.set_cookies_only(True)
        disable_last_resorts()
        args.download_xml = False
    if access:
        print(f"Institutional access: on ({access}; cap {args.cookies_max} PDFs"
              f"{', ONLY this route' if args.cookies_only else ''})")
    else:
        print("Institutional access: off (`get-cookies setup` turns it on)")

    if args.legalonly:
        # fetchpdf no longer takes legalonly= on batch_fetch_pdfs; last-resort
        # sources (Sci-Hub) are process state, disabled once before any worker
        # starts -- which is why this sits here and not next to the batch call.
        print("Legal sources only (--legalonly): last-resort sources disabled")
        disable_last_resorts()

    if args.backfill_structured or args.backfill_pdf:
        run_backfill(args)
        return

    type_filter = None
    if args.type:
        type_filter = {t.strip() for t in args.type.split(",") if t.strip()}
        print(f"Filtering to replication types: {sorted(type_filter)}")

    print("Building index of existing full text...")
    existing, structured = build_existing_stems()
    print(f"  {len(existing)} records already exist (corpus + downloaded); "
          f"{len(structured)} of them have XML/HTML")
    if args.download_xml and existing - structured:
        print(f"  {len(existing - structured)} have no structured full text — "
              f"--backfill-structured fetches it without re-downloading PDFs")

    to_download = []
    if args.doi_csv:
        # Explicit DOI list (dashboard DOI runs / ad-hoc CSVs): auto-detect the
        # DOI column, skip already-present papers; no type info to filter on.
        from mo_pipeline.discover.doi_runs import extract_dois_from_text
        text = Path(args.doi_csv).read_text(errors="replace")
        parsed = extract_dois_from_text(text)
        print(f"DOI list: {len(parsed['dois'])} DOIs from column "
              f"'{parsed['column']}' of {args.doi_csv}")
        for doi in parsed["dois"]:
            if doi_to_folder(doi).lower() in existing:
                continue
            to_download.append((doi, "other", ""))
    else:
        # Read confirmed replications
        with open(CONFIRMED_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            confirmed = list(reader)

        # Filter to papers that need downloading
        for row in confirmed:
            doi = row.get("doi", "").strip().lower()
            if not doi:
                continue
            stem = doi_to_folder(doi)
            if stem in existing:
                continue
            rtype = row.get("replication_type", "other")
            if type_filter and rtype not in type_filter:
                continue
            to_download.append((doi, rtype, row.get("title", "")[:80]))

    # Sort by type priority
    def sort_key(item):
        try:
            return TYPE_PRIORITY.index(item[1])
        except ValueError:
            return len(TYPE_PRIORITY)

    to_download.sort(key=sort_key)

    if args.limit:
        to_download = to_download[:args.limit]

    print(f"\nDOIs to download: {len(to_download)}")
    types = Counter(t[1] for t in to_download)
    for rtype, count in types.most_common():
        print(f"  {rtype}: {count}")

    if not to_download:
        print("Nothing to download.")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Extract just the DOIs for batch_fetch_pdfs
    dois = [doi for doi, rtype, title in to_download]

    print(f"\n🚀 Starting batch download with {args.workers} parallel workers...")
    print(f"   Output: {OUTPUT_DIR}")
    print(f"   Delay per worker: {args.delay}s")
    _describe_formats(args)
    print()

    # Use the specialized package's batch_fetch_pdfs with parallel workers
    results = batch_fetch_pdfs(
        dois=dois,
        output_dir=str(OUTPUT_DIR),
        verbose=True,
        workers=args.workers,
        delay=args.delay,
        create_missing_report=True,
        **_fetch_kwargs(args),
    )

    _render_records(results, args)
    _report(results)
    print(f"Output: {OUTPUT_DIR} (one folder per record)")
    if any(not r[1] for r in results):
        print(f"Missing PDFs report: {OUTPUT_DIR}/missing_pdfs.html")


if __name__ == "__main__":
    main()
