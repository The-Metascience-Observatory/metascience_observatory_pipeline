import csv
import os
import shutil

CSV_PATH = "/home/dan/Dropbox/AAA_METASCIENCE_OBSERVATORY/pull_replication_studies/data/direct_replications.csv"
TARGET_DIR = "/home/dan/downloaded_pdfs/very_likely_direct_replications/"

SEARCH_DIRS = [
    "/home/dan/Dropbox/AAA_METASCIENCE_OBSERVATORY/pull_replication_studies/downloaded_pdfs/",
    "/home/dan/downloaded_pdfs/",
    "/home/dan/Dropbox/AAA_METASCIENCE_OBSERVATORY/PDFs/",
]

EXCLUDE_PREFIXES = ("10.17605/osf", "10.6084/m9.figshare")

def doi_to_filename(doi):
    return doi.replace("/", "--") + ".pdf"

def filename_to_doi(filename):
    """Convert a PDF filename to a DOI (lowercased)."""
    name = filename.lower()
    if name.endswith(".pdf"):
        name = name[:-4]
    # Remove " (1)" style suffixes (duplicates from downloads)
    while name.endswith(")"):
        paren_start = name.rfind(" (")
        if paren_start != -1:
            inner = name[paren_start+2:-1]
            if inner.isdigit():
                name = name[:paren_start]
            else:
                break
        else:
            break
    return name.replace("--", "/")

# Step 1: Read all non-empty DOIs from CSV
dois = set()
with open(CSV_PATH, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        doi = row.get("doi", "").strip().lower()
        if doi:
            dois.add(doi)

print(f"Total non-empty DOIs in CSV: {len(dois)}")

# Step 2: Check which already exist in target
already_have = set()
for fname in os.listdir(TARGET_DIR):
    if fname.lower().endswith(".pdf"):
        d = filename_to_doi(fname)
        if d in dois:
            already_have.add(d)

print(f"Already in target directory: {len(already_have)}")
needed = dois - already_have
print(f"Still needed: {len(needed)}")

# Step 3: Build index of all PDFs in search directories (doi -> full path)
pdf_index = {}  # doi -> filepath
for search_dir in SEARCH_DIRS:
    if not os.path.isdir(search_dir):
        print(f"  WARNING: search dir not found: {search_dir}")
        continue
    for root, dirs, files in os.walk(search_dir):
        # Skip the target directory itself to avoid self-matching
        if os.path.abspath(root).startswith(os.path.abspath(TARGET_DIR)):
            continue
        for fname in files:
            if fname.lower().endswith(".pdf"):
                d = filename_to_doi(fname)
                if d in needed and d not in pdf_index:
                    pdf_index[d] = os.path.join(root, fname)

print(f"Found in search directories: {len(pdf_index)}")

# Step 4: Copy found PDFs to target
copied = 0
for doi, src_path in sorted(pdf_index.items()):
    canonical = doi_to_filename(doi)
    dest_path = os.path.join(TARGET_DIR, canonical)
    if not os.path.exists(dest_path):
        shutil.copy2(src_path, dest_path)
        copied += 1
        print(f"  Copied: {doi}")

# Step 5: Print copy count
print(f"\nCopied {copied} new PDFs to target directory.")

# Step 6: Final count
final_count = len([f for f in os.listdir(TARGET_DIR) if f.lower().endswith(".pdf")])
print(f"Final PDF count in very_likely_direct_replications: {final_count}")

# Step 7: Still missing (excluding registration/dataset DOIs)
still_missing = needed - set(pdf_index.keys())
still_missing_filtered = sorted(
    d for d in still_missing
    if not any(d.startswith(prefix) for prefix in EXCLUDE_PREFIXES)
)

print(f"\nStill missing (excluding registrations/datasets): {len(still_missing_filtered)}")
for d in still_missing_filtered:
    print(f"  {d}")
