#!/usr/bin/env python3
"""
Sanity check if the AI extraction correctly identified the original study being replicated.

Quality control tool that validates whether the extracted original_* fields
(title, authors, journal) actually match the real original study that the
replication paper cites. Uses Claude to compare the replication paper's
metadata with the extracted original study metadata.

If the CSV contains replication_title and replication_journal columns (added by
extract.py's collate_results), no additional arguments are needed. Otherwise,
use --papers-dir to load metadata.json files.

Usage:
    # Self-contained CSV (has replication_title/replication_journal)
    python sanity_check_if_original_study_is_right.py collated_results.csv
    python sanity_check_if_original_study_is_right.py collated_results.csv --model sonnet

    # Legacy CSV (needs metadata.json lookup)
    python sanity_check_if_original_study_is_right.py collated_results.csv --papers-dir /path/to/papers/
"""

import argparse
import csv
import json
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal

# Import metadata fetching from extract.py's dependencies
try:
    from fetch_metadata_from_doi import fetch_metadata_from_doi
except ImportError:
    print("Warning: fetch_metadata_from_doi not available, will skip DOI lookups", file=sys.stderr)
    fetch_metadata_from_doi = None

# Handle Ctrl+C gracefully
def signal_handler(sig, frame):
    print("\n\nInterrupted by user. Exiting...", file=sys.stderr)
    sys.exit(130)

signal.signal(signal.SIGINT, signal_handler)

def load_replication_metadata(papers_dir: Path, replication_doi: str) -> dict | None:
    """Load metadata.json for a replication paper given its DOI.

    Converts DOI like '10.1002/acp.3769' to folder name '10.1002--acp.3769'
    and reads metadata.json from that folder.
    """
    folder_name = replication_doi.replace("/", "--")
    metadata_path = papers_dir / folder_name / "metadata.json"

    if not metadata_path.exists():
        return None

    try:
        return json.loads(metadata_path.read_text())
    except (json.JSONDecodeError, IOError):
        return None


def fetch_replication_metadata_from_doi(doi: str) -> dict | None:
    """Fetch replication paper metadata from its DOI using CrossRef/OpenAlex APIs.

    Returns dict with 'title' and 'journal' keys, or None if fetch fails.
    """
    if not fetch_metadata_from_doi:
        return None

    try:
        # Remove https://doi.org/ prefix if present
        clean_doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")

        metadata = fetch_metadata_from_doi(clean_doi)
        if metadata and metadata.get("title"):
            return {
                "title": metadata.get("title", ""),
                "journal": metadata.get("journal", ""),
            }
    except Exception as e:
        # Silently fail - we'll handle missing metadata downstream
        pass

    return None


def check_if_plausible_replication(
    original_title: str,
    original_journal: str,
    replication_title: str,
    replication_journal: str,
    description: str = "",
    model: str = "haiku"
) -> tuple[bool | None, str, dict]:
    """
    Call Claude to check if replication paper plausibly replicates original.

    Returns (is_plausible, reasoning, usage_dict)
    """

    # Build description context if available
    description_context = ""
    if description and description.strip():
        description_context = f"\n\nContext (what finding is being replicated):\n{description}"

    prompt = f"""You are a scientific expert checking if a replication study plausibly replicates an original study based on metadata.

Original Study:
- Title: {original_title or "(missing)"}
- Journal: {original_journal or "(missing)"}

Replication Study:
- Title: {replication_title or "(missing)"}
- Journal: {replication_journal or "(missing)"}
{description_context}

Question: Does the replication study title/journal suggest it plausibly replicates the original study?

Consider:
- Do the titles suggest related research topics?
- Are they in related journals/fields?
- Does the replication title reference the original in any way?
- If context is provided, does it clarify the relationship between the studies?
- IMPORTANT: If the replication title contains words like "replication", "replication study", or "replicate", this is strong evidence of plausibility

Respond with ONLY a JSON object (no other text):
{{
    "is_plausible_replication": true or false,
    "reasoning": "brief 1-2 sentence explanation"
}}
"""

    cmd = [
        "claude",
        "--print",
        "--output-format", "json",
        "--model", model,
        "--dangerously-skip-permissions",
        prompt,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return None, "Timeout", {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0}
    except KeyboardInterrupt:
        raise  # Re-raise to let signal handler catch it

    if result.returncode != 0:
        return None, f"Error: {result.stderr[:200]}", {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0}

    try:
        cli_output = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, "Failed to parse CLI output", {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0}

    # Extract usage
    usage = {
        "input_tokens": cli_output.get("usage", {}).get("input_tokens", 0),
        "output_tokens": cli_output.get("usage", {}).get("output_tokens", 0),
        "cost_usd": cli_output.get("total_cost_usd", 0),
    }

    # Parse the model's response
    response_text = cli_output.get("result", "").strip()

    # Try to extract JSON from response
    try:
        # Find JSON object in response
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            response_json = json.loads(response_text[start:end])
            is_plausible = response_json.get("is_plausible_replication")
            reasoning = response_json.get("reasoning", "No reasoning provided")
            return is_plausible, reasoning, usage
        else:
            return None, f"No JSON in response: {response_text[:100]}", usage
    except json.JSONDecodeError:
        return None, f"Invalid JSON in response: {response_text[:100]}", usage


def main():
    parser = argparse.ArgumentParser(
        description="Sanity check if extracted original study metadata matches the actual original study cited in replication papers"
    )
    parser.add_argument(
        "csv_path",
        type=Path,
        help="Path to collated results CSV",
    )
    parser.add_argument(
        "--papers-dir",
        type=Path,
        default=None,
        help="Directory containing paper folders with metadata.json files (optional, only needed if CSV lacks replication_title/replication_journal columns)",
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
        help="Output CSV path (default: adds _checked suffix to input)",
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
        help="Show detailed information (titles and reasoning) for each check",
    )

    args = parser.parse_args()

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        stem = args.csv_path.stem
        output_path = args.csv_path.parent / f"{stem}_checked.csv"

    # Read input CSV
    print(f"Reading {args.csv_path}...", file=sys.stderr)
    with open(args.csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    # Check if CSV has replication metadata columns
    has_replication_metadata = "replication_title" in fieldnames and "replication_journal" in fieldnames

    if not has_replication_metadata and not args.papers_dir:
        print(
            "ERROR: CSV lacks replication_title/replication_journal columns and --papers-dir not provided",
            file=sys.stderr
        )
        sys.exit(1)

    # Add new columns
    new_fieldnames = list(fieldnames) + ["is_plausible_replication", "plausibility_reasoning"]

    # Process rows
    total_usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
    }

    checked = 0
    skipped = 0
    errors = 0
    implausible_results = []  # Track IMPLAUSIBLE results for end summary

    start_time = time.monotonic()

    try:
        for i, row in enumerate(rows, 1):
            # Skip if no replications
            if row.get("contains_replications") != "True":
                row["is_plausible_replication"] = ""
                row["plausibility_reasoning"] = ""
                skipped += 1
                continue

            # Check limit
            if args.limit and checked >= args.limit:
                row["is_plausible_replication"] = ""
                row["plausibility_reasoning"] = ""
                continue

            # Get replication DOI
            replication_doi = row["replication_doi"]

            # Get replication paper metadata
            if has_replication_metadata:
                # Read from CSV
                replication_title = row.get("replication_title", "").strip()
                replication_journal = row.get("replication_journal", "").strip()
            else:
                # Load from metadata.json
                metadata = load_replication_metadata(args.papers_dir, replication_doi)

                if not metadata:
                    print(f"[{i}/{len(rows)}] SKIP  {replication_doi}: no metadata.json", file=sys.stderr)
                    row["is_plausible_replication"] = ""
                    row["plausibility_reasoning"] = "No metadata found"
                    skipped += 1
                    continue

                replication_title = metadata.get("title", "").strip()
                replication_journal = metadata.get("journal", "").strip()

            # If replication metadata is empty, try fetching from DOI
            if not replication_title and not replication_journal:
                print(f"[{i}/{len(rows)}] Fetching metadata for {replication_doi}...", file=sys.stderr)
                fetched = fetch_replication_metadata_from_doi(row.get("replication_url", "") or f"https://doi.org/{replication_doi}")

                if fetched:
                    replication_title = fetched.get("title", "").strip()
                    replication_journal = fetched.get("journal", "").strip()
                    # Update row in case we want to re-collate later
                    if has_replication_metadata:
                        row["replication_title"] = replication_title
                        row["replication_journal"] = replication_journal

            # Skip if still no replication metadata after fetch attempt
            if not replication_title and not replication_journal:
                print(f"[{i}/{len(rows)}] SKIP  {replication_doi}: no replication metadata available", file=sys.stderr)
                row["is_plausible_replication"] = ""
                row["plausibility_reasoning"] = "No replication metadata available"
                skipped += 1
                continue

            # Check plausibility
            original_title = row.get("original_title", "")
            original_journal = row.get("original_journal", "")
            description = row.get("description", "")

            is_plausible, reasoning, usage = check_if_plausible_replication(
                original_title,
                original_journal,
                replication_title,
                replication_journal,
                description=description,
                model=args.model
            )

            # Update totals
            for key in total_usage:
                total_usage[key] += usage.get(key, 0)

            # Store results
            row["is_plausible_replication"] = str(is_plausible) if is_plausible is not None else ""
            row["plausibility_reasoning"] = reasoning

            if is_plausible is None:
                print(f"[{i}/{len(rows)}] ERROR {replication_doi}: {reasoning}", file=sys.stderr)
                errors += 1
            else:
                result_label = "PLAUSIBLE" if is_plausible else "IMPLAUSIBLE"
                print(
                    f"[{i}/{len(rows)}] {result_label}  {replication_doi}  "
                    f"(${usage['cost_usd']:.4f})",
                    file=sys.stderr
                )

                # Track IMPLAUSIBLE results for end summary
                if not is_plausible:
                    implausible_results.append({
                        "replication_doi": replication_doi,
                        "original_title": original_title,
                        "replication_title": replication_title,
                        "reasoning": reasoning,
                    })

                # Show detailed output: always for IMPLAUSIBLE, or for all if --verbose
                if not is_plausible or args.verbose:
                    print(f"  Original:    {original_title}", file=sys.stderr)
                    print(f"               [{original_journal}]", file=sys.stderr)
                    print(f"  Replication: {replication_title}", file=sys.stderr)
                    print(f"               [{replication_journal}]", file=sys.stderr)
                    print(f"  Reasoning:   {reasoning}", file=sys.stderr)
                    print("", file=sys.stderr)

                checked += 1

    except KeyboardInterrupt:
        print("\n\nInterrupted! Writing partial results...", file=sys.stderr)

    # Write output CSV (even if interrupted)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=new_fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Print summary
    elapsed = time.monotonic() - start_time
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Checked: {checked}  |  Skipped: {skipped}  |  Errors: {errors}", file=sys.stderr)
    print(
        f"Total tokens: {total_usage['input_tokens'] + total_usage['output_tokens']:,} "
        f"(in: {total_usage['input_tokens']:,}, out: {total_usage['output_tokens']:,})",
        file=sys.stderr,
    )
    print(f"Total cost: ${total_usage['cost_usd']:.4f}", file=sys.stderr)
    print(f"Runtime: {elapsed:.1f}s", file=sys.stderr)
    print(f"\nResults written to {output_path}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    # Print IMPLAUSIBLE results summary for human review
    if implausible_results:
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"IMPLAUSIBLE RESULTS FOR REVIEW ({len(implausible_results)} found)", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)

        for idx, result in enumerate(implausible_results, 1):
            print(f"\n[{idx}] {result['replication_doi']}", file=sys.stderr)
            print(f"  Original:    {result['original_title']}", file=sys.stderr)
            print(f"  Replication: {result['replication_title']}", file=sys.stderr)
            print(f"  Reasoning:   {result['reasoning']}", file=sys.stderr)

        print(f"\n{'='*60}", file=sys.stderr)
        print(f"Review these {len(implausible_results)} IMPLAUSIBLE results above", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)


if __name__ == "__main__":
    main()
