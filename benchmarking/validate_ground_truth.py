#!/usr/bin/env python3
"""
Ground truth validation tool for result classification mismatches.

Reads the errors CSV from a benchmark evaluation run, filters for
result_mismatch cases, and displays the paper's Discussion/Conclusion
sections alongside the ground truth and extracted classifications
for manual review.

Usage:
    python benchmarking/validate_ground_truth.py --tag sonnet_test_02_2026

    # Resume from a specific case number
    python benchmarking/validate_ground_truth.py --tag sonnet_test_02_2026 --start 5

    # Just display without interactive mode
    python benchmarking/validate_ground_truth.py --tag sonnet_test_02_2026 --display-only
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path


def extract_discussion(body_text: str, max_chars: int = 3000) -> str:
    """Extract the Discussion/Conclusion section from body.md text."""
    # Try to find Discussion or General Discussion or Conclusion sections
    patterns = [
        r"(?:^|\n)#+\s*(General\s+)?Discussion.*",
        r"(?:^|\n)#+\s*Conclusion.*",
        r"(?:^|\n)#+\s*Summary.*",
        r"(?:^|\n)\*\*\s*(General\s+)?Discussion.*",
    ]

    best_start = None
    for pattern in patterns:
        match = re.search(pattern, body_text, re.IGNORECASE)
        if match:
            if best_start is None or match.start() < best_start:
                best_start = match.start()

    if best_start is not None:
        excerpt = body_text[best_start:best_start + max_chars]
        if len(body_text) > best_start + max_chars:
            excerpt += "\n... [truncated]"
        return excerpt

    # Fallback: take last 2000 chars (likely near discussion/conclusion)
    if len(body_text) > max_chars:
        return "... [no Discussion header found, showing end of paper]\n" + body_text[-max_chars:]
    return body_text


def load_errors(errors_csv: Path) -> list[dict]:
    """Load result_mismatch errors from the benchmark errors CSV."""
    errors = []
    with open(errors_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["type"] == "result_mismatch":
                errors.append(row)
    return errors


def get_descriptions(case: dict, papers_dir: Path, tag: str) -> tuple[str, str]:
    """Get ground truth and extracted descriptions for this case.

    First tries the errors CSV columns (gt_description, ext_description).
    Falls back to reading the ground truth CSV and extraction JSON directly.
    """
    gt_desc = case.get("gt_description", "").strip()
    ext_desc = case.get("ext_description", "").strip()

    if gt_desc and ext_desc:
        return gt_desc, ext_desc

    paper = case["paper"]

    # Try to get GT description from ground truth CSV
    if not gt_desc:
        gt_csv = papers_dir.parent / "ground_truth_enhanced.csv"
        if gt_csv.exists():
            with open(gt_csv, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    repl_url = row.get("replication_url", "")
                    folder = repl_url.replace("http://doi.org/", "").replace("https://doi.org/", "").replace("/", "--")
                    if folder == paper:
                        candidate = row.get("description", "").strip()
                        if candidate and row.get("result", "") == case.get("gt_result", ""):
                            gt_desc = candidate
                            break
                        elif candidate and not gt_desc:
                            gt_desc = candidate

    # Try to get extracted description from result JSON
    if not ext_desc:
        paper_dir = papers_dir / paper
        if tag:
            result_dir = paper_dir / tag
        else:
            result_dir = paper_dir
        for json_path in sorted(result_dir.glob("*_result*.json")):
            try:
                data = json.loads(json_path.read_text())
                for rep in data.get("replications", []):
                    if rep.get("result", "") == case.get("ext_result", ""):
                        ext_desc = rep.get("description", "")
                        if ext_desc:
                            break
                if ext_desc:
                    break
            except (json.JSONDecodeError, OSError):
                continue

    return gt_desc or "(no description)", ext_desc or "(no description)"


def display_case(case: dict, body_text: str, case_num: int, total: int,
                 gt_desc: str = "", ext_desc: str = ""):
    """Display a single validation case."""
    print(f"\n{'='*70}")
    print(f"Case {case_num}/{total}: {case['paper']}")
    print(f"{'='*70}")
    print(f"  Ground Truth result:  {case['gt_result']}")
    if gt_desc:
        print(f"  Ground Truth desc:    {gt_desc}")
    print(f"  Extracted result:     {case['ext_result']}")
    if ext_desc:
        print(f"  Extracted desc:       {ext_desc}")
    if gt_desc and ext_desc and gt_desc != ext_desc:
        print(f"  ** Descriptions differ — may be different experiments **")
    print(f"{'─'*70}")
    print("DISCUSSION/CONCLUSION EXCERPT:")
    print(f"{'─'*70}")
    discussion = extract_discussion(body_text)
    print(discussion)
    print(f"{'─'*70}")


def interactive_review(case: dict) -> dict | None:
    """Prompt user for classification decision. Returns correction dict or None."""
    print("\nWhich classification is correct?")
    print("  [g] Ground truth is correct (extraction error)")
    print("  [e] Extraction is correct (ground truth error)")
    print("  [b] Both wrong (enter correct value)")
    print("  [s] Subjective / unclear")
    print("  [k] Skip this case")
    print("  [q] Quit")

    while True:
        choice = input("\nYour choice: ").strip().lower()

        if choice == "q":
            return "quit"
        elif choice == "k":
            return None
        elif choice == "g":
            return {
                "paper": case["paper"],
                "gt_result": case["gt_result"],
                "ext_result": case["ext_result"],
                "correct_result": case["gt_result"],
                "verdict": "ground_truth_correct",
                "justification": input("Brief justification (optional): ").strip(),
            }
        elif choice == "e":
            return {
                "paper": case["paper"],
                "gt_result": case["gt_result"],
                "ext_result": case["ext_result"],
                "correct_result": case["ext_result"],
                "verdict": "extraction_correct",
                "justification": input("Brief justification (optional): ").strip(),
            }
        elif choice == "b":
            correct = input("Enter correct result (success/failure/inconclusive/reversal): ").strip().lower()
            if correct in ("success", "failure", "inconclusive", "reversal"):
                return {
                    "paper": case["paper"],
                    "gt_result": case["gt_result"],
                    "ext_result": case["ext_result"],
                    "correct_result": correct,
                    "verdict": "both_wrong",
                    "justification": input("Brief justification (optional): ").strip(),
                }
            print("Invalid result. Try again.")
        elif choice == "s":
            return {
                "paper": case["paper"],
                "gt_result": case["gt_result"],
                "ext_result": case["ext_result"],
                "correct_result": "",
                "verdict": "subjective",
                "justification": input("Brief note (optional): ").strip(),
            }
        else:
            print("Invalid choice. Enter g, e, b, s, k, or q.")


def save_corrections(corrections: list[dict], output_path: Path):
    """Save corrections to CSV."""
    if not corrections:
        print("No corrections to save.")
        return

    fieldnames = ["paper", "gt_result", "ext_result", "correct_result", "verdict", "justification"]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(corrections)
    print(f"\nSaved {len(corrections)} corrections to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Validate ground truth for result classification")
    parser.add_argument("--tag", required=True, help="Benchmark tag (e.g., sonnet_test_02_2026)")
    parser.add_argument("--start", type=int, default=1, help="Start from case number (1-indexed)")
    parser.add_argument("--display-only", action="store_true", help="Display cases without interactive review")
    args = parser.parse_args()

    benchmarking_dir = Path(__file__).parent
    papers_dir = benchmarking_dir / "ground_truth_data_filtered_PDFs"
    errors_csv = benchmarking_dir / "evaluation_results" / f"benchmark_{args.tag}_errors.csv"
    output_csv = benchmarking_dir / f"ground_truth_corrections_{args.tag}.csv"

    if not errors_csv.exists():
        print(f"Error: {errors_csv} not found. Run evaluate.py first.", file=sys.stderr)
        sys.exit(1)

    errors = load_errors(errors_csv)
    print(f"Found {len(errors)} result_mismatch cases to review")

    # Deduplicate by paper (some papers appear multiple times for different replications)
    seen = set()
    unique_errors = []
    for e in errors:
        key = (e["paper"], e["gt_result"], e["ext_result"])
        if key not in seen:
            seen.add(key)
            unique_errors.append(e)
    errors = unique_errors
    print(f"After deduplication: {len(errors)} unique cases")

    corrections = []
    start_idx = max(0, args.start - 1)

    for i, case in enumerate(errors[start_idx:], start=start_idx + 1):
        paper_dir = papers_dir / case["paper"]
        body_path = paper_dir / "body.md"

        if not body_path.exists():
            print(f"\nSkipping {case['paper']}: body.md not found")
            continue

        body_text = body_path.read_text(encoding="utf-8")
        gt_desc, ext_desc = get_descriptions(case, papers_dir, args.tag)
        display_case(case, body_text, i, len(errors), gt_desc, ext_desc)

        if args.display_only:
            continue

        result = interactive_review(case)
        if result == "quit":
            break
        if result is not None:
            corrections.append(result)

    if not args.display_only and corrections:
        save_corrections(corrections, output_csv)

        # Print summary
        verdicts = {}
        for c in corrections:
            v = c["verdict"]
            verdicts[v] = verdicts.get(v, 0) + 1
        print("\nValidation Summary:")
        for v, count in sorted(verdicts.items()):
            print(f"  {v}: {count}")


if __name__ == "__main__":
    main()
