#!/usr/bin/env python3
"""
Wrapper for sanity_check_if_original_study_is_right.py that filters out validated=yes rows.

This script:
1. Filters input CSV to exclude rows where validated="yes"
2. Runs sanity_check_if_original_study_is_right.py on filtered rows
3. Merges results back into original CSV (preserving all rows)

Usage:
    python run_sanity_check_on_unvalidated.py \\
        /path/to/input.csv \\
        --model sonnet \\
        --output /path/to/output.csv
"""

import argparse
import csv
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path


# Handle Ctrl+C gracefully
def signal_handler(sig, frame):
    print("\n\nInterrupted by user. Cleaning up...", file=sys.stderr)
    sys.exit(130)


signal.signal(signal.SIGINT, signal_handler)


def filter_csv_by_validation(input_path: Path, temp_path: Path) -> tuple[int, int]:
    """
    Filter CSV to exclude rows where validated="yes".

    Returns (total_rows, filtered_rows) counts.
    """
    print(f"Reading and filtering {input_path}...", file=sys.stderr)

    with open(input_path, newline="", encoding="utf-8") as f_in:
        reader = csv.DictReader(f_in)
        fieldnames = reader.fieldnames
        rows = list(reader)

    # Filter out validated="yes" rows
    filtered_rows = [
        row for row in rows
        if row.get("validated", "").strip().lower() != "yes"
    ]

    # Write filtered CSV
    with open(temp_path, "w", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(filtered_rows)

    total = len(rows)
    filtered = total - len(filtered_rows)

    print(
        f"Filtered out {filtered} validated=yes rows, "
        f"will check {len(filtered_rows)} rows",
        file=sys.stderr
    )

    return total, filtered


def run_sanity_check(
    temp_csv: Path,
    temp_output: Path,
    args: argparse.Namespace
) -> int:
    """
    Run sanity_check_if_original_study_is_right.py on filtered CSV.

    Returns the return code from the subprocess.
    """
    script_path = Path(__file__).parent / "sanity_check_if_original_study_is_right.py"

    if not script_path.exists():
        print(f"ERROR: Sanity check script not found at {script_path}", file=sys.stderr)
        return 1

    # Build command
    cmd = [
        sys.executable,  # Use same Python interpreter
        str(script_path),
        str(temp_csv),
        "--output", str(temp_output),
        "--model", args.model,
    ]

    # Pass through optional arguments
    if args.limit:
        cmd.extend(["--limit", str(args.limit)])

    if args.verbose:
        cmd.append("--verbose")

    if args.papers_dir:
        cmd.extend(["--papers-dir", str(args.papers_dir)])

    print(f"\nRunning sanity check...", file=sys.stderr)
    print(f"Command: {' '.join(cmd)}\n", file=sys.stderr)

    # Run with streaming output
    try:
        result = subprocess.run(cmd, check=False)
        return result.returncode
    except KeyboardInterrupt:
        print("\nInterrupted during sanity check", file=sys.stderr)
        raise


def merge_results_back(
    original_csv: Path,
    checked_csv: Path,
    output_path: Path
) -> None:
    """
    Merge sanity check results back into original CSV.

    - Rows that were checked: populate new columns
    - Rows that were skipped (validated=yes): leave new columns empty
    """
    print(f"\nMerging results back into original CSV...", file=sys.stderr)

    # Read original CSV (all rows)
    with open(original_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        original_fieldnames = reader.fieldnames
        original_rows = list(reader)

    # Read checked CSV (with new columns)
    checked_lookup = {}
    new_columns = ["is_plausible_replication", "plausibility_reasoning"]

    if checked_csv.exists():
        with open(checked_csv, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                doi = row["replication_doi"]
                checked_lookup[doi] = {
                    "is_plausible_replication": row.get("is_plausible_replication", ""),
                    "plausibility_reasoning": row.get("plausibility_reasoning", ""),
                }

    # Merge: add new columns to all original rows
    output_fieldnames = list(original_fieldnames) + new_columns

    for row in original_rows:
        doi = row["replication_doi"]
        if doi in checked_lookup:
            row.update(checked_lookup[doi])
        else:
            # Not checked (validated=yes or error) - leave empty
            row["is_plausible_replication"] = ""
            row["plausibility_reasoning"] = ""

    # Write merged CSV
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_fieldnames)
        writer.writeheader()
        writer.writerows(original_rows)

    checked_count = len(checked_lookup)
    skipped_count = len(original_rows) - checked_count

    print(
        f"Merged results: {checked_count} checked, {skipped_count} skipped",
        file=sys.stderr
    )
    print(f"\nFinal results written to {output_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Run sanity check on non-validated rows only (excludes validated=yes)"
    )
    parser.add_argument(
        "csv_path",
        type=Path,
        help="Path to input CSV file",
    )
    parser.add_argument(
        "--model",
        default="haiku",
        help="Claude model to use (default: haiku)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV path (default: adds _plausibility_checked suffix to input)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of rows to check (for testing)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed information for each check",
    )
    parser.add_argument(
        "--papers-dir",
        type=Path,
        default=None,
        help="Directory containing paper folders with metadata.json files",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep temporary files (for debugging)",
    )

    args = parser.parse_args()

    # Validate input
    if not args.csv_path.exists():
        print(f"ERROR: Input CSV not found: {args.csv_path}", file=sys.stderr)
        sys.exit(1)

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        stem = args.csv_path.stem
        output_path = args.csv_path.parent / f"{stem}_plausibility_checked.csv"

    # Create temp files
    temp_dir = tempfile.mkdtemp(prefix="sanity_check_")
    temp_filtered_csv = Path(temp_dir) / "filtered.csv"
    temp_output_csv = Path(temp_dir) / "checked.csv"

    try:
        # Step 1: Filter CSV
        total_rows, filtered_count = filter_csv_by_validation(
            args.csv_path,
            temp_filtered_csv
        )

        # Step 2: Run sanity check
        returncode = run_sanity_check(temp_filtered_csv, temp_output_csv, args)

        if returncode != 0:
            print(
                f"\nWARNING: Sanity check returned non-zero exit code: {returncode}",
                file=sys.stderr
            )
            print("Will still attempt to merge any partial results...", file=sys.stderr)

        # Step 3: Merge results back
        merge_results_back(args.csv_path, temp_output_csv, output_path)

        print(f"\n{'='*60}", file=sys.stderr)
        print("Done!", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)

    finally:
        # Cleanup temp files
        if not args.keep_temp:
            import shutil
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                print(f"Warning: Failed to clean up temp dir {temp_dir}: {e}", file=sys.stderr)
        else:
            print(f"\nTemp files kept in: {temp_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
