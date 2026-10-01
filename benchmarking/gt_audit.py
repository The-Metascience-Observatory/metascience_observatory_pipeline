#!/usr/bin/env python3
"""Ground-truth hygiene: find rows whose recorded original study and DOI disagree.

An external ground-truth set is imported verbatim, and some of its rows name one
paper in `original_title` while `original_url` points at another. That is not a
rare typo: auditing the 2026-09-02 pilot by hand found three such rows in 27
papers, each of which scored a correct extraction as a miss. The pipeline is
then measured against a target the ground truth itself does not agree on.

This resolves every row's DOI at Crossref and compares the title that comes back
with the title the row records. When they disagree it also asks Crossref which
DOI the *recorded title* belongs to, so the row can be repaired rather than
merely flagged. Nothing is applied automatically: the audit writes a reviewable
CSV, and `--write-corrections` appends only the unambiguous cases (the DOI
resolves to a demonstrably different paper AND the recorded title resolves
cleanly to another DOI) to the set's corrections sidecar, each with its
evidence. Everything else stays in the audit for a human.

    python benchmarking/gt_audit.py --gt silver:main_gt_human
    python benchmarking/gt_audit.py --gt silver:flora --write-corrections

Crossref answers are cached under benchmarking/cache/crossref/, so a re-run
costs nothing and the network is hit once per DOI.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH_DIR.parent))

from mo_pipeline import config  # noqa: E402
from mo_pipeline.corpus.models import doi_to_folder  # noqa: E402
from mo_pipeline.corpus.models import normalize_doi  # noqa: E402
from mo_pipeline.shared.fetch_metadata_from_title import _title_similarity  # noqa: E402

SILVER_DIR = config.BENCH_SILVER_DIR
CACHE_DIR = config.BENCH_CACHE_DIR / "crossref"
MAILTO = "dan@metascienceobservatory.org"
#: Below this the recorded title and the DOI's real title are different papers.
#: _title_similarity already returns 0.0 for a non-match and accepts a record
#: holding only a paper's main title, so this is a floor, not a fuzzy cutoff.
MATCH_FLOOR = 0.55
#: Fields that, together with the replication, the original and the description,
#: identify a row. Two rows agreeing on all of them are the same finding twice.
DUP_FIELDS = ("result", "original_n", "original_es", "original_p_value",
              "replication_n", "replication_es", "replication_p_value")
#: Below this much converted text, "the paper does not cite X" means the
#: conversion failed, not that the ground truth is wrong.
MIN_PAPER_CHARS = 20_000
TIMEOUT = 20


def _cache(key: str) -> Path:
    return CACHE_DIR / f"{hashlib.sha256(key.encode()).hexdigest()}.json"


def _get(url: str) -> dict | None:
    """Crossref GET with an on-disk cache. None on any failure (cached as null)."""
    cf = _cache(url)
    if cf.exists():
        try:
            return json.loads(cf.read_text())
        except json.JSONDecodeError:
            pass
    req = urllib.request.Request(url, headers={
        "User-Agent": f"mo_pipeline gt_audit (mailto:{MAILTO})", "Accept": "application/json"})
    out = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                out = json.loads(r.read())
            break
        except Exception as e:
            code = getattr(e, "code", None)
            if code == 404:
                break                       # a DOI Crossref does not know: not retryable
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cf.write_text(json.dumps(out))
    time.sleep(0.1)
    return out


def crossref_title(item: dict) -> str:
    """Crossref files a subtitle separately; a title without it loses comparisons."""
    main = (item.get("title") or [""])[0] or ""
    sub = (item.get("subtitle") or [""])[0] or ""
    if sub and sub.lower() not in main.lower():
        return f"{main.rstrip(': ')}: {sub}"
    return main


def title_of_doi(doi: str) -> tuple[str, str]:
    """(title, year) Crossref holds for a DOI, or ("", "") if it does not know it."""
    data = _get(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}?mailto={MAILTO}")
    item = (data or {}).get("message") or {}
    if not item:
        return "", ""
    parts = ((item.get("issued") or {}).get("date-parts") or [[None]])[0]
    return crossref_title(item), str(parts[0] or "")


def doi_of_title(title: str) -> tuple[str, str, float]:
    """(doi, title, similarity) for the best Crossref match to a title."""
    q = urllib.parse.quote(title[:300])
    data = _get(f"https://api.crossref.org/works?query.bibliographic={q}&rows=5&mailto={MAILTO}")
    best = ("", "", 0.0)
    for item in ((data or {}).get("message") or {}).get("items", []) or []:
        cand = crossref_title(item)
        sim = _title_similarity(title, cand, threshold=MATCH_FLOOR)
        if sim > best[2]:
            best = (normalize_doi(item.get("DOI") or "") or "", cand, sim)
    return best


def _fold(s: str) -> str:
    """Lowercase and strip diacritics, so Acemoglu matches Acemoğlu."""
    return "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower())
                   if not unicodedata.combining(c))


def last_names(author_string: str) -> list[str]:
    """Surnames from either convention: "Smith, John" and "John A. Smith"."""
    out = []
    for a in (author_string or "").split(";"):
        a = a.strip()
        if not a:
            continue
        last = a.split(",")[0].strip() if "," in a else a.split()[-1]
        last = last.strip(".").strip()
        if len(last) > 1:
            out.append(last)
    return out


def cited_in_paper(paper_dir: Path, doi: str, authors: str, year: str) -> bool | None:
    """Does the replication paper actually cite the study the row names?

    The Crossref cross-check cannot see a row whose title and DOI agree with each
    other but name a study the paper never replicated -- the commonest error in
    FLoRa. The paper's own bibliography can, but only with a deliberately blunt
    test: does ANY author's surname appear anywhere in the paper's text?

    Anything sharper misfires. Requiring the surname near the year flagged four
    rows in the human set that were all cited, because numeric citation styles
    put the surname in a bibliography and the year nowhere near it; requiring
    the DOI flags every working-paper and preprint identifier. Diacritics are
    stripped for the same reason ("Acemoglu" for "Acemoğlu"). So this fires only
    when no author of the recorded original is mentioned at all, which is hard
    to explain except by the row naming the wrong study. Returns None when the
    folder holds nothing readable, so "cannot tell" is never "not cited".
    """
    chunks = []
    for name in ("references.json", "references.md", "body.md", "abstract.md"):
        f = paper_dir / name
        if f.exists():
            try:
                chunks.append(f.read_text(errors="replace"))
            except OSError:
                pass
    for x in list(paper_dir.glob("*.xml"))[:1]:
        try:
            chunks.append(x.read_text(errors="replace")[:2_000_000])
        except OSError:
            pass
    blob = _fold(" ".join(chunks))
    # A thin conversion cannot support a negative claim. One paper here has
    # 12 KB of text where the article runs to tens of thousands, and none of its
    # authors' names survived: that is a broken conversion being reported as a
    # ground-truth error. A real paper plus its bibliography clears this easily.
    if len(blob) < MIN_PAPER_CHARS:
        return None
    if doi and doi.lower() in blob:
        return True
    names = last_names(authors)
    if not names:
        return None
    return any(_fold(s) in blob for s in names)


def _doi(url: str) -> str:
    m = re.search(r"10\.\d{4,9}/\S+", (url or "").strip())
    return (normalize_doi(m.group(0)) or m.group(0).rstrip("/").lower()) if m else ""


def audit(rows: list[dict], workers: int = 4, papers_dir: Path | None = None) -> list[dict]:
    """One finding per problematic row; rows that check out are not reported."""
    dois = sorted({_doi(r.get("original_url", "")) for r in rows} - {""})
    print(f"resolving {len(dois)} distinct original DOIs at Crossref "
          f"({sum(1 for d in dois if _cache(f'https://api.crossref.org/works/{urllib.parse.quote(d)}?mailto={MAILTO}').exists())} cached)",
          file=sys.stderr)
    resolved: dict[str, tuple[str, str]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for doi, res in zip(dois, pool.map(title_of_doi, dois)):
            resolved[doi] = res

    seen: dict[tuple, str] = {}
    findings = []
    for r in rows:
        rid = r.get("row_id") or ""
        doi, title = _doi(r.get("original_url", "")), (r.get("original_title") or "").strip()
        rep = _doi(r.get("replication_url", ""))

        # A duplicate has to match on everything that distinguishes a row, or the
        # check reports distinct effects as duplicates. Two ways it did: a
        # truncated description hides the words that separate "(need for
        # autonomy)" from "(need for relatedness)", and effect-level sets reuse
        # one construct-level description across rows that differ only in their
        # statistics. So: full description AND the recorded numbers.
        key = (rep, doi,
               re.sub(r"\W+", " ", (r.get("description") or "").lower()).strip(),
               tuple((r.get(f) or "").strip() for f in DUP_FIELDS))
        if key[0] and seen.get(key):
            findings.append(dict(row_id=rid, issue="duplicate_row", recorded_title=title[:120],
                                 recorded_doi=doi, doi_resolves_to="", proposed_doi="",
                                 proposed_doi_title="", similarity="", action="review",
                                 evidence=f"same replication, original and description as {seen[key]}"))
            continue
        if key[0]:
            seen[key] = rid

        if not doi and not title:
            findings.append(dict(row_id=rid, issue="no_original", recorded_title="", recorded_doi="",
                                 doi_resolves_to="", proposed_doi="", proposed_doi_title="",
                                 similarity="", action="review", evidence="row names no original study"))
            continue
        if not doi or not title:
            continue                        # only one side present: nothing to cross-check

        real_title, real_year = resolved.get(doi, ("", ""))
        if not real_title:
            findings.append(dict(row_id=rid, issue="doi_unknown_to_crossref", recorded_title=title[:120],
                                 recorded_doi=doi, doi_resolves_to="", proposed_doi="",
                                 proposed_doi_title="", similarity="", action="review",
                                 evidence="Crossref does not know this DOI (may be OSF/DataCite; not necessarily wrong)"))
            continue
        if _title_similarity(title, real_title, threshold=MATCH_FLOOR) > 0:
            # Title and DOI agree with each other. They can still name a study
            # the paper never replicated, which only its bibliography reveals.
            if papers_dir is not None and rep:
                folder = papers_dir / doi_to_folder(rep)
                if folder.is_dir() and cited_in_paper(folder, doi, r.get("original_authors", ""),
                                                      str(r.get("original_year") or "").split(".")[0]) is False:
                    findings.append(dict(
                        row_id=rid, issue="gt_original_not_cited_in_paper", recorded_title=title[:120],
                        recorded_doi=doi, doi_resolves_to=f"{real_title[:90]} ({real_year})",
                        proposed_doi="", proposed_doi_title="", similarity="", action="review",
                        evidence="no author of the recorded original is mentioned anywhere in the "
                                 "replication paper, so the row may name a study it does not replicate"))
            continue

        cand_doi, cand_title, sim = doi_of_title(title)
        if cand_doi and cand_doi != doi and sim > 0:
            findings.append(dict(
                row_id=rid, issue="doi_title_mismatch", recorded_title=title[:120], recorded_doi=doi,
                doi_resolves_to=f"{real_title[:90]} ({real_year})", proposed_doi=cand_doi,
                proposed_doi_title=cand_title[:120], similarity=f"{sim:.2f}", action="correct",
                evidence=(f"recorded DOI resolves to \"{real_title[:70]}\" ({real_year}), a different paper; "
                          f"the recorded title resolves to {cand_doi}")))
        else:
            findings.append(dict(
                row_id=rid, issue="doi_title_mismatch_unresolved", recorded_title=title[:120],
                recorded_doi=doi, doi_resolves_to=f"{real_title[:90]} ({real_year})", proposed_doi="",
                proposed_doi_title="", similarity="", action="review",
                evidence="recorded DOI resolves to a different paper and the recorded title could not be resolved"))
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gt", required=True, help="silver:<name> or a path to a ground-truth CSV")
    ap.add_argument("--write-corrections", action="store_true",
                    help="append the unambiguous fixes to the set's corrections sidecar")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--papers-dir", default=None,
                    help="also check each row's original against the replication paper's own "
                         "bibliography, for rows whose paper is on disk")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.gt.startswith("silver:"):
        name = args.gt.split(":", 1)[1]
        path = SILVER_DIR / ({"flora": "flora.csv", "fred_v242": "fred_v2_4_2.csv"}.get(name, f"{name}.csv"))
    else:
        name, path = Path(args.gt).stem, Path(args.gt)
    if not path.exists():
        sys.exit(f"ERROR: {path} not found")

    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    findings = audit(rows, args.workers, Path(args.papers_dir) if args.papers_dir else None)

    out = Path(args.out) if args.out else BENCH_DIR / f"gt_audit_{name}.csv"
    fields = ["row_id", "issue", "recorded_title", "recorded_doi", "doi_resolves_to",
              "proposed_doi", "proposed_doi_title", "similarity", "action", "evidence"]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(findings)

    counts = defaultdict(int)
    for x in findings:
        counts[x["issue"]] += 1
    print(f"\n{len(rows)} rows audited, {len(findings)} flagged -> {out}")
    for k, v in sorted(counts.items()):
        print(f"  {k:32} {v}")

    correctable = [x for x in findings if x["action"] == "correct"]
    if not args.write_corrections:
        if correctable:
            print(f"\n{len(correctable)} rows can be corrected automatically; re-run with "
                  f"--write-corrections to append them to silver/{name}_corrections.csv")
        return 0

    sidecar = SILVER_DIR / f"{name}_corrections.csv"
    existing, header = [], ["row_id", "action", "field", "value", "evidence", "date", "by"]
    if sidecar.exists():
        with open(sidecar, newline="", encoding="utf-8-sig") as f:
            existing = list(csv.DictReader(f))
    already = {(r["row_id"], r.get("field", "")) for r in existing}
    date = time.strftime("%Y-%m-%d")
    added = 0
    for x in correctable:
        for field, value in (("original_url", f"https://doi.org/{x['proposed_doi']}"),
                             ("original_doi_norm", x["proposed_doi"])):
            if (x["row_id"], field) in already:
                continue
            existing.append({"row_id": x["row_id"], "action": "set", "field": field, "value": value,
                             "evidence": x["evidence"], "date": date, "by": "gt_audit"})
            added += 1
    with open(sidecar, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(existing)
    print(f"\nappended {added} correction row(s) to {sidecar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
