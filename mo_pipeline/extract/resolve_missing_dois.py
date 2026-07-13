#!/usr/bin/env python3
"""
Post-process collated CSV to resolve missing DOIs using bibliographic metadata.

Usage:
    python resolve_missing_dois.py benchmarking/ground_truth_data_filtered_PDFs/collated_results_v6_feb2026.csv
"""

import argparse
import csv
import sys
import time
from pathlib import Path

# Import existing DOI resolution functions
from fetch_missing_doi import fetch_metadata_from_title
from fetch_metadata_from_doi import fetch_metadata_from_doi


def resolve_missing_dois(csv_path: Path, output_path: Path = None, dry_run: bool = False) -> dict:
    """Resolve missing DOIs in a collated results CSV.

    Args:
        csv_path: Path to input CSV
        output_path: Path to output CSV (default: overwrite input)
        dry_run: If True, don't write changes

    Returns:
        dict with statistics about resolution
    """
    if output_path is None:
        output_path = csv_path

    # Read CSV
    rows = []
    with open(csv_path, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    stats = {
        'total_replications': 0,
        'missing_urls': 0,
        'resolved': 0,
        'failed': 0,
        'skipped_no_title': 0,
    }

    for i, row in enumerate(rows):
        # Skip non-replication rows
        if row['contains_replications'] != 'True':
            continue

        stats['total_replications'] += 1

        # Skip if URL already exists
        if row.get('original_url') and row['original_url'].strip():
            continue

        stats['missing_urls'] += 1

        # Need title for DOI resolution
        title = row.get('original_title', '').strip()
        if not title:
            stats['skipped_no_title'] += 1
            print(f"  [{i+1}] SKIP: No title (replication: {row.get('replication_doi', 'unknown')})", file=sys.stderr)
            continue

        print(f"  [{i+1}] Resolving: {title[:60]}...", file=sys.stderr)

        try:
            # Fetch DOI from title using multiple APIs
            doi_data = fetch_metadata_from_title(title)

            if not doi_data or not doi_data.get('doi'):
                stats['failed'] += 1
                print(f"       ✗ No DOI found", file=sys.stderr)
                continue

            doi = doi_data['doi']

            # Validate DOI and get full metadata
            validated = fetch_metadata_from_doi(doi)

            if not validated:
                stats['failed'] += 1
                print(f"       ✗ DOI {doi} failed validation", file=sys.stderr)
                continue

            # Success! Update the row
            if not dry_run:
                row['original_url'] = f"https://doi.org/{doi}"

                # Optionally update other fields if they're missing and we have better data
                if not row.get('original_journal') and validated.get('journal'):
                    row['original_journal'] = validated['journal']

                if not row.get('original_year') and validated.get('year'):
                    row['original_year'] = str(validated['year'])

            stats['resolved'] += 1
            print(f"       ✓ Resolved to: {doi}", file=sys.stderr)

            # Rate limit: be nice to APIs
            time.sleep(0.5)

        except Exception as e:
            stats['failed'] += 1
            print(f"       ✗ Error: {e}", file=sys.stderr)
            continue

    # Write updated CSV
    if not dry_run:
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nUpdated CSV written to: {output_path}", file=sys.stderr)

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Resolve missing DOIs in collated results CSV"
    )
    parser.add_argument(
        'csv_path',
        type=Path,
        help='Path to collated results CSV'
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=None,
        help='Output CSV path (default: overwrite input)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Dry run: show what would be resolved without writing changes'
    )

    args = parser.parse_args()

    if not args.csv_path.exists():
        print(f"Error: CSV not found: {args.csv_path}", file=sys.stderr)
        sys.exit(1)

    print("=" * 80, file=sys.stderr)
    print("DOI RESOLUTION POST-PROCESSING", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    print(f"Input: {args.csv_path}", file=sys.stderr)
    if args.dry_run:
        print("Mode: DRY RUN (no changes will be written)", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    print()

    stats = resolve_missing_dois(args.csv_path, args.output, args.dry_run)

    # Print summary
    print()
    print("=" * 80, file=sys.stderr)
    print("SUMMARY", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    print(f"Total replications: {stats['total_replications']}", file=sys.stderr)
    print(f"Missing URLs: {stats['missing_urls']}", file=sys.stderr)
    print(f"  Resolved: {stats['resolved']} ({100*stats['resolved']/max(stats['missing_urls'],1):.1f}%)", file=sys.stderr)
    print(f"  Failed: {stats['failed']}", file=sys.stderr)
    print(f"  Skipped (no title): {stats['skipped_no_title']}", file=sys.stderr)
    print("=" * 80, file=sys.stderr)


if __name__ == '__main__':
    main()
