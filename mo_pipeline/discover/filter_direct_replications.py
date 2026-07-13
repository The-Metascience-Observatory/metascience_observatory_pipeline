"""
Filter confirmed replications to find high-confidence DIRECT replications
(same experiment, same methods) and exclude close/conceptual replications
that tested a finding in a different cohort or context.

Searches all known PDF directories for existing files, downloads missing ones,
and copies everything into a single output folder.

Input:  data/confirmed_replications.csv
Output: /home/dan/downloaded_pdfs/very_likely_direct_replications/
        data/direct_replications.csv
"""

import csv
import os
import re
import shutil
import sys
from pathlib import Path

csv.field_size_limit(sys.maxsize)

# Uses the specialized package at /home/dan/Dropbox/AAA_METASCIENCE_OBSERVATORY/fetch_pdf_from_doi
from fetch_pdf_from_doi import fetch_pdf_from_doi
from mo_pipeline.config import (
    DATA_DIR, PDF_DIR, CONFIRMED_REPLICATIONS_CSV, DOWNLOAD_STATUS_CSV,
    DIRECT_REPLICATIONS_CSV as DIRECT_CSV,
    DIRECT_REPLICATIONS_PDF_DIR as OUTPUT_DIR,
    LEGACY_REPLICATIONS_DB as REPLICATIONS_DB,
    PDF_SEARCH_DIRS,
)

# ---------------------------------------------------------------------------
# Patterns that suggest a CLOSE replication (different population / context)
# ---------------------------------------------------------------------------
EXTENSION_PATTERNS = [
    r"different\s+(cohort|population|sample|dataset|ethnic|race|country|site|facilit)",
    r"independent\s+(cohort|population|sample|dataset)",
    r"new\s+(cohort|population|sample|dataset|patient)",
    r"external\s+validat",
    r"another\s+(cohort|population|sample|dataset|facilit|country)",
    r"(African|Asian|European|Japanese|Chinese|Korean|Hispanic|Latino)\s+(American|population|cohort|sample)",
    r"(validated|generalized?|extended?)\s+(in|to|across)\s+(a\s+)?(different|independent|new|another)",
    r"genome.wide\s+association",
    r"genetic\s+(variant|loci|polymorphism|association)",
    r"(SNP|polymorphism|allele|genotype|haplotype)",
]

# Patterns that indicate a true direct / exact replication
DIRECT_PATTERNS = [
    r"same\s+(method|design|protocol|paradigm|procedure|experiment|task|stimuli|measure)",
    r"registered\s+(report|replication)",
    r"pre.?registered",
    r"exact\s+replication",
    r"close(ly)?\s+(match|follow|replic)",
    r"reproducibility\s+project",
    r"many\s*labs",
]


def doi_to_filename(doi):
    if not doi:
        return None
    return doi.replace("/", "--").replace("\\", "--") + ".pdf"


def is_strong_direct(row):
    """Return True if the paper looks like a genuine direct replication."""
    if row.get("replication_type", "").lower() != "direct":
        return False
    if row.get("confidence", "").lower() != "high":
        return False

    text = (row.get("reasoning", "") + " " + row.get("title", "")).lower()

    has_extension = any(re.search(p, text) for p in EXTENSION_PATTERNS)
    has_direct = any(re.search(p, text) for p in DIRECT_PATTERNS)

    return has_direct and not has_extension


def build_pdf_index():
    """Build a map of normalized DOI -> file path from all known PDF dirs."""
    doi_to_pdf = {}
    for search_dir in PDF_SEARCH_DIRS:
        if not search_dir.exists():
            continue
        for root, dirs, files in os.walk(search_dir):
            for fname in files:
                if fname.lower().endswith(".pdf"):
                    base = fname.rsplit(".pdf", 1)[0]
                    # Remove ' (1)' suffixes from duplicate downloads
                    base = re.sub(r"\s*\(\d+\)$", "", base)
                    doi_candidate = base.replace("--", "/").lower()
                    doi_to_pdf[doi_candidate] = os.path.join(root, fname)
    return doi_to_pdf


def write_failed_html(failed_rows):
    """Generate an HTML page with clickable links for papers we couldn't download."""
    import html as html_mod
    html_path = OUTPUT_DIR / "missing_pdfs.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Missing Direct Replication PDFs — Manual Retrieval</title>
<style>
  body { font-family: sans-serif; max-width: 1100px; margin: 40px auto; padding: 0 20px; }
  h1 { color: #333; }
  .stats { color: #666; margin-bottom: 20px; }
  table { border-collapse: collapse; width: 100%; }
  th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #ddd; }
  th { background: #f5f5f5; position: sticky; top: 0; }
  a { color: #1a73e8; }
  a:visited { color: #681da8; }
  .links a { margin-right: 12px; white-space: nowrap; }
  tr:hover { background: #f0f7ff; }
</style>
</head><body>
""")
        f.write("<h1>Missing Direct Replication PDFs</h1>\n")
        f.write(f'<p class="stats">{len(failed_rows)} papers could not be downloaded automatically. '
                f"Save PDFs as <code>DOI--format.pdf</code> to: "
                f"<code>{OUTPUT_DIR}</code></p>\n")
        f.write("<table>\n<tr><th>#</th><th>Title</th><th>Year</th><th>Links</th></tr>\n")

        for i, row in enumerate(failed_rows, 1):
            doi = row.get("doi", "")
            pmid = row.get("pmid", "")
            title = html_mod.escape(row.get("title", "Unknown title"))
            year = html_mod.escape(row.get("year", ""))

            links = '<td class="links">'
            if doi:
                links += f'<a href="https://doi.org/{doi}" target="_blank">DOI</a>'
                links += f'<a href="https://sci-hub.se/{doi}" target="_blank">Sci-Hub</a>'
                links += f'<a href="https://scholar.google.com/scholar?q={doi}" target="_blank">Scholar</a>'
            if pmid:
                links += f'<a href="https://pubmed.ncbi.nlm.nih.gov/{pmid}/" target="_blank">PubMed</a>'
                links += f'<a href="https://www.ncbi.nlm.nih.gov/pmc/articles/pmid/{pmid}/" target="_blank">PMC</a>'
            links += "</td>"

            f.write(f"<tr><td>{i}</td><td>{title}</td><td>{year}</td>{links}</tr>\n")

        f.write("</table>\n</body></html>\n")
    return html_path


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load confirmed replications
    with open(CONFIRMED_REPLICATIONS_CSV, "r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    print(f"Loaded {len(rows)} confirmed replications")

    # Filter to strong direct replications
    direct_rows = [r for r in rows if is_strong_direct(r)]
    print(f"Strong direct replications: {len(direct_rows)}")

    # Exclude papers already in the replications database
    db_dois = set()
    if REPLICATIONS_DB.exists():
        with open(REPLICATIONS_DB, "r", newline="", encoding="utf-8") as f:
            for db_row in csv.DictReader(f):
                url = db_row.get("replication_url", "").strip()
                m = re.search(r"(?:doi\.org/)(10\..+)", url)
                if m:
                    db_dois.add(m.group(1).lower().rstrip("/"))
        before = len(direct_rows)
        direct_rows = [
            r for r in direct_rows
            if r.get("doi", "").strip().lower() not in db_dois
        ]
        excluded = before - len(direct_rows)
        print(f"Excluded {excluded} already in database ({len(db_dois)} DB DOIs)")
        print(f"Remaining: {len(direct_rows)}")

    # Write CSV manifest
    fieldnames = rows[0].keys() if rows else []
    with open(DIRECT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(direct_rows)
    print(f"Wrote {DIRECT_CSV}")

    # Build index of all existing PDFs
    print("\nIndexing existing PDFs...")
    doi_to_pdf = build_pdf_index()
    print(f"Indexed {len(doi_to_pdf)} PDFs")

    # Load previous download status to skip known failures
    prev_failed = set()
    if DOWNLOAD_STATUS_CSV.exists():
        with open(DOWNLOAD_STATUS_CSV, "r", newline="", encoding="utf-8") as f:
            for srow in csv.DictReader(f):
                if srow.get("download_success", "").lower() != "true":
                    doi_key = srow.get("doi", "").strip().lower()
                    if doi_key:
                        prev_failed.add(doi_key)
    print(f"Previously failed downloads: {len(prev_failed)}")

    # Process each direct replication
    copied = 0
    downloaded = 0
    failed = 0
    skipped_prev_fail = 0
    no_doi = 0
    failed_rows = []

    for i, row in enumerate(direct_rows):
        doi = row.get("doi", "").strip().lower()
        title = row.get("title", "")[:80]

        if not doi:
            no_doi += 1
            failed_rows.append(row)
            continue

        filename = doi_to_filename(doi)
        dst = OUTPUT_DIR / filename

        # Already in output folder?
        if dst.exists():
            copied += 1
            continue

        # Check if we already have it somewhere
        if doi in doi_to_pdf:
            src = doi_to_pdf[doi]
            shutil.copy2(src, dst)
            copied += 1
            continue

        # Skip known failures — just add to HTML list
        if doi in prev_failed:
            skipped_prev_fail += 1
            failed_rows.append(row)
            continue

        # Need to download
        print(f"  [{i+1}/{len(direct_rows)}] Downloading: {doi} ...")
        try:
            result = fetch_pdf_from_doi(doi, str(dst))
            if result and dst.exists():
                downloaded += 1
            else:
                if dst.exists():
                    dst.unlink()
                failed += 1
                failed_rows.append(row)
                print(f"    FAILED: {title}")
        except Exception as e:
            if dst.exists():
                dst.unlink()
            failed += 1
            failed_rows.append(row)
            print(f"    ERROR: {e}")

    # Generate HTML for failed downloads
    if failed_rows:
        html_path = write_failed_html(failed_rows)
        print(f"\nMissing PDFs HTML: {html_path}")

    total_have = copied + downloaded
    print(f"\n{'='*60}")
    print(f"Strong direct replications: {len(direct_rows)}")
    print(f"Already had PDF:           {copied}")
    print(f"Newly downloaded:          {downloaded}")
    print(f"Previously failed (skip):  {skipped_prev_fail}")
    print(f"Newly failed:              {failed}")
    print(f"No DOI:                    {no_doi}")
    print(f"Total PDFs in folder:      {total_have}")
    print(f"Output: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
