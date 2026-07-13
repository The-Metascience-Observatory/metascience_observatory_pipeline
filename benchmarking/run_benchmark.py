#!/usr/bin/env python3
"""
Benchmarking runner for extract.py against ground truth data.
Reads papers from ground_truth_enhanced.csv and runs extraction.
"""
import argparse
import csv
import subprocess
import sys
from pathlib import Path


def doi_url_to_folder(doi_url: str) -> str:
    """Convert DOI URL to folder name.
    Example: http://doi.org/10.1002/ejsp.2748 -> 10.1002--ejsp.2748
    """
    doi_url = doi_url.strip()
    # Remove protocol and domain
    doi_url = doi_url.replace("http://doi.org/", "")
    doi_url = doi_url.replace("https://doi.org/", "")
    doi_url = doi_url.replace("http://dx.doi.org/", "")
    doi_url = doi_url.replace("https://dx.doi.org/", "")
    # Replace / with --
    return doi_url.replace("/", "--")


def main():
    parser = argparse.ArgumentParser(description="Run extraction benchmark")
    parser.add_argument("--tag", required=True, help="Tag for this benchmark run")
    parser.add_argument("--model", default="sonnet", help="Model to use")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers")
    parser.add_argument("--level", default="full", help="Extraction level")
    parser.add_argument("--small", action="store_true", help="Use smaller ground truth subset (81 papers)")
    args = parser.parse_args()

    benchmarking_dir = Path(__file__).parent
    csv_name = "ground_truth_data_filtered_small.csv" if args.small else "ground_truth_enhanced.csv"
    csv_path = benchmarking_dir / csv_name
    papers_dir = benchmarking_dir / "ground_truth_data_filtered_PDFs"

    # Read CSV and extract paper folders
    paper_folders = set()
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            replication_url = row["replication_url"]
            folder_name = doi_url_to_folder(replication_url)
            paper_path = papers_dir / folder_name
            if paper_path.exists():
                paper_folders.add(folder_name)
            else:
                print(f"Warning: Paper directory not found: {folder_name}", file=sys.stderr)

    print(f"Found {len(paper_folders)} paper directories to process", file=sys.stderr)

    # Write include list so extract.py only processes papers in the ground truth
    include_file = benchmarking_dir / f".include_list_{args.tag}.txt"
    include_file.write_text("\n".join(sorted(paper_folders)) + "\n")

    # Call extract.py
    extract_script = benchmarking_dir.parent / "extract.py"
    cmd = [
        sys.executable,
        str(extract_script),
        str(papers_dir),
        "--batch",
        "--model", args.model,
        "--workers", str(args.workers),
        "--level", args.level,
        "--tag", args.tag,
        "--dontcheck",
        "--include-list", str(include_file),
    ]

    print(f"Running: {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd)
    include_file.unlink(missing_ok=True)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
