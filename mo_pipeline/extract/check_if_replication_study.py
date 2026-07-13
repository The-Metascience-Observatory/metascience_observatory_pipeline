#!/usr/bin/env python3
"""
Check if papers contain replication studies.

Similar to extract.py but simpler - just classifies whether a paper contains
direct, very close, or conceptual replications without extracting detailed data.

Usage:
    # Single paper
    python check_if_replication_study.py papers/10.1234_some-paper/

    # Batch: all papers in a directory
    python check_if_replication_study.py papers/ --batch

    # With specific model
    python check_if_replication_study.py papers/ --batch --model sonnet

    # Use Cursor Agent CLI instead of Claude CLI
    python check_if_replication_study.py papers/ --batch --usecursor
"""

import argparse
import csv
import json
import signal
import subprocess
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Handle Ctrl+C gracefully
def signal_handler(sig, frame):
    print("\n\nInterrupted by user. Exiting...", file=sys.stderr)
    sys.exit(130)

signal.signal(signal.SIGINT, signal_handler)

SYSTEM_PROMPT = """You are a scientific expert analyzing academic papers to determine if they contain replication studies.

A replication study tests whether findings from a previous published study can be reproduced. There are four types:

1. **Technical replication** (also called "robustness checking"): A new experiment is not done, but raw data from an existing experiment is reanalyzed using the reported procedures. Or, it may involve simply running provided code on data to get results (this is called "frictionless" reproduction).

2. **Direct replication**: An experimental procedure is repeated as closely as possible, usually following the specifications for the procedure given in the original paper. This is the most common understanding of the term "replication".

3. **Close replication**: An experimental procedure is repeated closely, but with one or more intentional changes (e.g., different language, online vs in-person, different population).

4. **Conceptual replication**: A finding from a previous experiment is tested in a new experiment using a different experimental procedure.

**IMPORTANT EXCLUSIONS:**
- Within-paper replications (e.g., "Study 1 and Study 2" where both are in the same paper) - these are NOT replications
- Extensions or modifications without testing the original finding
- Meta-analyses or reviews (unless they also report new replication data)
- Robustness checks within the same dataset (without reanalyzing the original study's data)

Your task: Determine if this paper contains ANY replication studies (technical, direct, close, or conceptual).

Output a JSON object with:
{
    "contains_replications": true or false,
    "replication_types": ["technical", "direct", "close", "conceptual"] (list all that apply, empty if false),
    "confidence": "high", "medium", or "low",
    "reasoning": "Brief explanation of your classification"
}

**Focus on the abstract and methods section first.** Look for language like:
- "We replicate [Author Year]"
- "Replication of", "Reproduction of"
- "We test whether [previous finding] holds"
- "Following [Author Year], we examine..."
- References to "original study" or "previous study" in methods
- "We reanalyze data from [Author Year]" (technical replication)
- "Using the data/code from [Author Year]" (technical replication)
- "Robustness check of [previous finding]" (technical replication)

Be conservative: if unsure, err on the side of "contains_replications": false.
"""


class SkipPaper(Exception):
    """Raised when a paper should be skipped."""


def _num(value, default=0):
    """Convert usage/cost fields to numeric values safely."""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


# Approximate cost per 1M tokens (input, output) for Cursor models. Used when CLI returns no usage.
_CURSOR_COST_PER_1M = {
    "composer-1.5": (3.0, 15.0),
    "composer-1": (3.0, 15.0),
    "opus-4.6": (15.0, 75.0),
    "opus-4.6-thinking": (15.0, 75.0),
    "opus-4.5": (15.0, 75.0),
    "sonnet-4.5": (3.0, 15.0),
    "sonnet-4.5-thinking": (3.0, 15.0),
    "gemini-3-flash": (0.15, 0.60),
    "gemini-3-pro": (1.25, 5.0),
    "gpt-5.3-codex": (2.5, 10.0),
    "gpt-5.2": (2.5, 10.0),
}


def _estimate_cursor_usage(
    result_text: str,
    prompt_text: str,
    model_id: str,
) -> tuple[int, int, float]:
    """Estimate input/output tokens and cost when Cursor CLI returns no usage.
    Uses ~4 chars/token for text and approximate model pricing.
    """
    out_tok = max(0, int(len(result_text) / 3.5))
    in_tok = max(0, int(len(prompt_text) / 4) + 800)  # +800 for typical abstract+body read
    rates = (2.0, 10.0)
    for k, v in _CURSOR_COST_PER_1M.items():
        if k in model_id or model_id in k:
            rates = v
            break
    cost = (in_tok * rates[0] + out_tok * rates[1]) / 1_000_000
    return in_tok, out_tok, cost


def _extract_usage(cli_output: dict, fallback_model: str) -> tuple[str, dict]:
    """Extract model and usage fields across Claude/Cursor JSON variants."""
    model_usage = cli_output.get("modelUsage")
    if isinstance(model_usage, dict) and model_usage:
        model_id = next(iter(model_usage))
    else:
        model_id = (
            cli_output.get("model")
            or cli_output.get("model_id")
            or fallback_model
        )

    usage_block = cli_output.get("usage", {})
    if not isinstance(usage_block, dict):
        usage_block = {}
    per_model_usage = {}
    if isinstance(model_usage, dict) and model_usage:
        first_key = next(iter(model_usage))
        maybe = model_usage.get(first_key, {})
        if isinstance(maybe, dict):
            per_model_usage = maybe

    # Support known naming variants across CLIs.
    input_tokens = int(
        _num(
            usage_block.get(
                "input_tokens",
                usage_block.get("inputTokens", per_model_usage.get("inputTokens", 0)),
            ),
            default=0,
        )
    )
    output_tokens = int(
        _num(
            usage_block.get(
                "output_tokens",
                usage_block.get("outputTokens", per_model_usage.get("outputTokens", 0)),
            ),
            default=0,
        )
    )
    cache_creation_tokens = int(
        _num(
            usage_block.get(
                "cache_creation_input_tokens",
                usage_block.get(
                    "cacheCreationInputTokens",
                    per_model_usage.get("cacheCreationInputTokens", 0),
                ),
            ),
            default=0,
        )
    )
    cache_read_tokens = int(
        _num(
            usage_block.get(
                "cache_read_input_tokens",
                usage_block.get(
                    "cacheReadInputTokens",
                    per_model_usage.get("cacheReadInputTokens", 0),
                ),
            ),
            default=0,
        )
    )

    cost_usd = _num(
        cli_output.get(
            "total_cost_usd",
            cli_output.get(
                "totalCostUsd",
                cli_output.get(
                    "cost_usd",
                    per_model_usage.get("costUSD", 0),
                ),
            ),
        ),
        default=0.0,
    )

    duration_ms = int(
        _num(
            cli_output.get("duration_ms", cli_output.get("durationMs", 0)),
            default=0,
        )
    )
    num_turns = int(_num(cli_output.get("num_turns", cli_output.get("numTurns", 0)), default=0))

    usage = {
        "model": model_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cost_usd": cost_usd,
        "duration_ms": duration_ms,
        "num_turns": num_turns,
    }
    return model_id, usage


def check_paper(
    paper_dir: Path,
    model: str = "haiku",
    tag: str | None = None,
    use_cursor: bool = False,
    timeout_seconds: int = 300,
    timeout_retries: int = 1,
) -> tuple[dict, dict]:
    """Check if a single paper contains replications.

    Returns (result_dict, usage_dict).
    Raises SkipPaper if output already exists.
    """
    paper_dir = paper_dir.resolve()

    # Determine output directory based on tag
    if tag:
        output_dir = paper_dir / tag
        output_dir.mkdir(exist_ok=True)
    else:
        output_dir = paper_dir

    # Check if output already exists
    result_path = output_dir / "replication_check.json"
    if result_path.exists():
        raise SkipPaper(f"Output already exists: replication_check.json")

    # Validate expected files exist
    abstract = paper_dir / "abstract.md"
    if not abstract.exists():
        raise FileNotFoundError(f"Missing abstract.md in {paper_dir}")

    # Check for corrupted/failed PDF extractions
    abstract_text = abstract.read_text()
    if "Partner servers are unavailable" in abstract_text or len(abstract_text.strip()) < 50:
        raise SkipPaper(f"Corrupted or invalid abstract (likely failed PDF extraction)")

    user_prompt = (
        f"Analyze the paper in: {paper_dir}\n"
        f"Start by reading {paper_dir}/abstract.md\n"
        f"Determine if this paper contains replication studies.\n"
        f"Save your result to {output_dir}/result.json"
    )

    if use_cursor:
        # Cursor CLI has no dedicated system prompt flag; prepend instructions.
        cursor_prompt = (
            f"System instructions:\n{SYSTEM_PROMPT}\n\n"
            f"Task instructions:\n{user_prompt}"
        )
        cmd = [
            "cursor", "agent",
            "--print",
            "--output-format", "json",
            "--model", model,
            "--workspace", str(paper_dir),
            "--force",
            cursor_prompt,
        ]
        cli_name = "cursor"
    else:
        cmd = [
            "claude",
            "--print",
            "--output-format", "json",
            "--model", model,
            "--system-prompt", SYSTEM_PROMPT,
            "--allowedTools", "Read", "Grep", "Glob",
            "--add-dir", str(paper_dir),
            "--dangerously-skip-permissions",
            user_prompt,
        ]
        cli_name = "claude"

    start = time.monotonic()

    result = None
    for attempt in range(timeout_retries + 1):
        # Increase timeout each retry (300s, 600s, 900s, ...)
        attempt_timeout = timeout_seconds * (attempt + 1)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=attempt_timeout,
            )
            break
        except subprocess.TimeoutExpired:
            if attempt < timeout_retries:
                print(
                    f"Timeout ({attempt_timeout}s) for {paper_dir.name}; retrying "
                    f"{attempt + 1}/{timeout_retries}...",
                    file=sys.stderr,
                )
                continue
            raise RuntimeError(
                f"Timed out after {attempt_timeout}s for {paper_dir}"
            )
        except KeyboardInterrupt:
            raise  # Re-raise to let signal handler catch it

    wall_time_ms = int((time.monotonic() - start) * 1000)

    if result.returncode != 0:
        raise RuntimeError(
            f"{cli_name} CLI failed for {paper_dir}:\n{result.stderr}"
        )

    # Parse the selected CLI JSON envelope
    try:
        cli_output = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Failed to parse {cli_name} CLI output for {paper_dir}:\n"
            f"{result.stdout[:500]}"
        )

    model_id, usage = _extract_usage(cli_output, fallback_model=model)
    usage["wall_time_ms"] = wall_time_ms

    # Cursor CLI does not return usage/token/cost in its JSON; estimate when zeros
    if use_cursor and usage["input_tokens"] == 0 and usage["output_tokens"] == 0:
        result_text = cli_output.get("result", "") or ""
        prompt_text = (
            f"System instructions:\n{SYSTEM_PROMPT}\n\n"
            f"Task instructions:\n{user_prompt}"
        )
        est_in, est_out, est_cost = _estimate_cursor_usage(
            result_text, prompt_text, usage["model"]
        )
        usage["input_tokens"] = est_in
        usage["output_tokens"] = est_out
        usage["cost_usd"] = est_cost
        usage["_estimated"] = True  # Mark as estimated for debug_log

    # Save full output as debug log
    debug_log = {
        "model": model_id,
        "modelUsage": cli_output.get("modelUsage", {}),
        "assistant_text": cli_output.get("result", ""),
        "usage": usage,
        "session_id": cli_output.get("session_id", ""),
    }
    debug_path = output_dir / "debug_log.json"
    debug_path.write_text(json.dumps(debug_log, indent=2))

    # Read the result.json the agent should have written
    temp_result_path = output_dir / "result.json"
    if not temp_result_path.exists():
        raise RuntimeError(
            f"Agent did not write result.json for {paper_dir}.\n"
            f"Agent output: {cli_output.get('result', '')[:500]}"
        )

    try:
        data = json.loads(temp_result_path.read_text())
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Agent wrote invalid JSON to {temp_result_path}:\n"
            f"{temp_result_path.read_text()[:500]}"
        )

    # Inject model name into output
    data["model"] = usage["model"]

    # Rename result.json to replication_check.json
    final_path = output_dir / "replication_check.json"
    final_path.write_text(json.dumps(data, indent=2))
    temp_result_path.unlink()

    return data, usage


def check_batch(
    papers_dir: Path,
    model: str = "haiku",
    workers: int = 1,
    tag: str | None = None,
    use_cursor: bool = False,
    timeout_seconds: int = 300,
    timeout_retries: int = 1,
) -> list[dict]:
    """Check all paper directories under papers_dir."""

    paper_dirs = sorted(
        p for p in papers_dir.iterdir()
        if p.is_dir() and (p / "abstract.md").exists()
    )

    if not paper_dirs:
        print(f"No paper directories found in {papers_dir}", file=sys.stderr)
        return []

    print(f"Found {len(paper_dirs)} papers to check", file=sys.stderr)

    results = []
    skipped = []
    errors = []
    total_usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "cost_usd": 0.0,
        "duration_ms": 0,
    }

    def process_one(paper_dir: Path) -> tuple[Path, dict | None, dict | None, str | None]:
        try:
            data, usage = check_paper(
                paper_dir,
                model=model,
                tag=tag,
                use_cursor=use_cursor,
                timeout_seconds=timeout_seconds,
                timeout_retries=timeout_retries,
            )
            return (paper_dir, data, usage, None)
        except SkipPaper as e:
            return (paper_dir, "skip", None, str(e))
        except Exception as e:
            return (paper_dir, None, None, str(e))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Submit all futures immediately so workers start promptly.
        futures = {
            pool.submit(process_one, d): d for d in paper_dirs
        }

        for i, future in enumerate(as_completed(futures), 1):
            paper_dir, data, usage, error = future.result()
            name = paper_dir.name
            if data == "skip":
                print(f"[{i}/{len(paper_dirs)}] SKIP  {name}: {error}", file=sys.stderr)
                skipped.append(name)
                continue
            elif error:
                print(f"[{i}/{len(paper_dirs)}] FAIL  {name}: {error}", file=sys.stderr)
                errors.append({"paper": name, "error": error})
            else:
                for key in total_usage:
                    total_usage[key] += usage.get(key, 0)

                contains = data.get("contains_replications", False)
                types = ", ".join(data.get("replication_types", []))
                label = f"YES ({types})" if contains else "NO"
                tokens = usage["input_tokens"] + usage["output_tokens"]
                cost = f"${usage['cost_usd']:.4f}"
                print(
                    f"[{i}/{len(paper_dirs)}] {label:20}  {name}  "
                    f"({tokens:,} tokens, {cost})",
                    file=sys.stderr,
                )
                results.append({"paper": name, "usage": usage, **data})

    # Print usage summary
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Papers checked: {len(results)}  |  Skipped: {len(skipped)}  |  Errors: {len(errors)}", file=sys.stderr)
    print(
        f"Total tokens: {total_usage['input_tokens'] + total_usage['output_tokens']:,} "
        f"(in: {total_usage['input_tokens']:,}, out: {total_usage['output_tokens']:,})",
        file=sys.stderr,
    )
    print(f"Total cost: ${total_usage['cost_usd']:.4f}", file=sys.stderr)
    print(f"Total time: {total_usage['duration_ms'] / 1000:.1f}s", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    # Collate results into CSV
    collate_results(papers_dir, tag=tag)

    return results


def collate_results(papers_dir: Path, tag: str | None = None) -> Path:
    """Scan all replication_check.json files and produce a CSV."""

    rows = []
    for paper_dir in sorted(papers_dir.iterdir()):
        if not paper_dir.is_dir():
            continue

        # Find replication_check.json - look in tag subdir first, then paper dir
        search_dir = paper_dir / tag if tag and (paper_dir / tag).is_dir() else paper_dir
        result_file = search_dir / "replication_check.json"

        if not result_file.exists():
            continue

        try:
            data = json.loads(result_file.read_text())
        except (json.JSONDecodeError, IOError):
            continue

        doi = paper_dir.name.replace("--", "/")
        rows.append({
            "doi": doi,
            "contains_replications": data.get("contains_replications", False),
            "replication_types": ", ".join(data.get("replication_types", [])),
            "confidence": data.get("confidence", ""),
            "reasoning": data.get("reasoning", ""),
        })

    # Write CSV
    csv_name = f"replication_check_{tag}.csv" if tag else "replication_check.csv"
    out_path = papers_dir / csv_name

    fieldnames = ["doi", "contains_replications", "replication_types", "confidence", "reasoning"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    n_yes = sum(1 for r in rows if r["contains_replications"])
    n_no = len(rows) - n_yes
    print(f"\nCollated {len(rows)} results → {out_path}", file=sys.stderr)
    print(f"  Contains replications: {n_yes}  |  No replications: {n_no}", file=sys.stderr)

    return out_path


def main():
    parser = argparse.ArgumentParser(
        description="Check if papers contain replication studies"
    )
    parser.add_argument(
        "path",
        type=Path,
        help="Path to a single paper directory, or parent directory for --batch",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Process all paper subdirectories under the given path",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel workers for batch mode (default: 1). Workers are staggered to avoid API overload.",
    )
    parser.add_argument(
        "--model",
        default="haiku",
        help="Claude model to use (default: haiku)",
    )
    parser.add_argument(
        "--tag",
        type=str,
        default=None,
        help="Tag for organizing outputs into subdirectories",
    )
    parser.add_argument(
        "--collate-only",
        action="store_true",
        help="Only collate existing result JSONs into a CSV — no checking",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=300,
        help="Base timeout per paper in seconds (default: 300). Retries use multiples of this value.",
    )
    parser.add_argument(
        "--timeout-retries",
        type=int,
        default=1,
        help="Number of retries on timeout per paper (default: 1).",
    )
    parser.add_argument(
        "--usecursor",
        "--usercursor",
        action="store_true",
        help="Use `cursor agent` CLI instead of `claude` CLI",
    )

    args = parser.parse_args()

    start = time.monotonic()

    if args.collate_only:
        collate_results(args.path, tag=args.tag)
        elapsed = time.monotonic() - start
        print(f"Runtime: {elapsed:.1f}s", file=sys.stderr)
        sys.exit(0)

    if args.batch:
        check_batch(
            args.path,
            model=args.model,
            workers=args.workers,
            tag=args.tag,
            use_cursor=args.usecursor,
            timeout_seconds=args.timeout_seconds,
            timeout_retries=args.timeout_retries,
        )
    else:
        try:
            data, usage = check_paper(
                args.path,
                model=args.model,
                tag=args.tag,
                use_cursor=args.usecursor,
                timeout_seconds=args.timeout_seconds,
                timeout_retries=args.timeout_retries,
            )
        except SkipPaper as e:
            print(f"SKIP: {e}", file=sys.stderr)
            sys.exit(0)

        contains = data.get("contains_replications", False)
        types = ", ".join(data.get("replication_types", []))
        label = f"YES ({types})" if contains else "NO"
        tokens = usage["input_tokens"] + usage["output_tokens"]
        print(
            f"{label}  |  "
            f"Tokens: {tokens:,} (in: {usage['input_tokens']:,}, out: {usage['output_tokens']:,})  |  "
            f"Cost: ${usage['cost_usd']:.4f}  |  "
            f"Turns: {usage['num_turns']}",
            file=sys.stderr,
        )
        print(f"Reasoning: {data.get('reasoning', '')}", file=sys.stderr)

    elapsed = time.monotonic() - start
    print(f"Runtime: {elapsed:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
