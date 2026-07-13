#!/usr/bin/env python3
"""
Move PDF folders from potential_replication_studies directory to in_database
if their DOI matches any replication_url in the database CSV.

Usage:
    python move_pdfs_in_database.py         # Interactive mode (asks for confirmation)
    python move_pdfs_in_database.py --yes   # Auto-confirm and move
    python move_pdfs_in_database.py --dry-run  # Just show matches, don't move
"""

import argparse
import csv
import shutil
import sys
from pathlib import Path

# Paths
source_dir = Path("/home/dan/metascience_observatory_pdfs/potential_replication_studies_identified_by_fred_team_not_in_ground_truth_need_to_be_ingested")
dest_dir = Path("/home/dan/metascience_observatory_pdfs/in_database")
csv_path = Path("/home/dan/Dropbox/AAA_METASCIENCE_OBSERVATORY/metascience_observatory_website/data/replications_database_2026_02_10_070006.csv")

def doi_to_folder_name(doi: str) -> str:
    """Convert DOI to folder name format (replace / with --)."""
    # Remove https://doi.org/ prefix if present
    doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
    return doi.replace("/", "--")

def main():
    parser = argparse.ArgumentParser(
        description="Move PDF folders that are in the database to in_database directory"
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt and proceed with move",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be moved without actually moving",
    )
    args = parser.parse_args()

    # Create destination directory if it doesn't exist
    if not args.dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)

    # Read replication URLs from CSV
    print(f"Reading database from {csv_path}...", file=sys.stderr)
    replication_dois = set()

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            replication_url = row.get("replication_url", "").strip()
            if replication_url:
                folder_name = doi_to_folder_name(replication_url)
                replication_dois.add(folder_name)

    print(f"Found {len(replication_dois)} unique replication DOIs in database", file=sys.stderr)

    # Check which folders in source_dir match
    print(f"\nScanning {source_dir}...", file=sys.stderr)

    if not source_dir.exists():
        print(f"ERROR: Source directory does not exist: {source_dir}", file=sys.stderr)
        sys.exit(1)

    folders = [f for f in source_dir.iterdir() if f.is_dir()]
    print(f"Found {len(folders)} folders in source directory", file=sys.stderr)

    # Find matches
    matches = []
    for folder in folders:
        if folder.name in replication_dois:
            matches.append(folder)

    print(f"\nFound {len(matches)} folders that match database replication URLs", file=sys.stderr)

    if not matches:
        print("No matches found. Nothing to move.", file=sys.stderr)
        return

    # Show what will be moved
    print("\nThe following folders will be moved:", file=sys.stderr)
    for i, folder in enumerate(matches[:10], 1):
        print(f"  {i}. {folder.name}", file=sys.stderr)

    if len(matches) > 10:
        print(f"  ... and {len(matches) - 10} more", file=sys.stderr)

    print(f"\nFrom: {source_dir}", file=sys.stderr)
    print(f"To:   {dest_dir}", file=sys.stderr)

    # Handle dry-run mode
    if args.dry_run:
        print("\n[DRY RUN] No files were moved.", file=sys.stderr)
        return

    # Ask for confirmation unless --yes was provided
    if not args.yes:
        try:
            response = input("\nProceed with move? [y/N]: ")
            if response.lower() != "y":
                print("Aborted.", file=sys.stderr)
                return
        except EOFError:
            print("\nERROR: Cannot read input in non-interactive mode.", file=sys.stderr)
            print("Use --yes to skip confirmation or --dry-run to preview.", file=sys.stderr)
            sys.exit(1)

    # Move folders
    print("\nMoving folders...", file=sys.stderr)
    moved = 0
    skipped = 0

    for folder in matches:
        dest_path = dest_dir / folder.name

        # Check if destination already exists
        if dest_path.exists():
            print(f"SKIP (already exists): {folder.name}", file=sys.stderr)
            skipped += 1
            continue

        try:
            shutil.move(str(folder), str(dest_path))
            print(f"MOVED: {folder.name}", file=sys.stderr)
            moved += 1
        except Exception as e:
            print(f"ERROR moving {folder.name}: {e}", file=sys.stderr)

    print(f"\nDone! Moved {moved} folders, skipped {skipped}", file=sys.stderr)


if __name__ == "__main__":
    main()
