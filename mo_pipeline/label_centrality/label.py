"""Run claude CLI centrality labeling over the pilot manifest.

For each work unit (replication paper folder), builds a blinded task prompt
(original metadata + abstract + claim list — never results) and invokes the
claude CLI the same way extract.py does. The agent reads the paper folder and
writes {folder}/{tag}/centrality_result.json.

Resumable: units whose tag output already exists and validates are skipped.
Run two passes with different tags for AI-AI agreement:

    python -m mo_pipeline.label_centrality.label --tag centrality_pilot_a --workers 3
    python -m mo_pipeline.label_centrality.label --tag centrality_pilot_b --workers 3

Smoke test one unit first:
    python -m mo_pipeline.label_centrality.label --tag smoke --limit 1 --show-prompt
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from mo_pipeline import config
from mo_pipeline.label_centrality import common

PROMPT_PATH = config.PROMPTS_DIR / "prompt_centrality.md"
VERSION_PATH = config.PROMPTS_DIR / "version_centrality.txt"
TIMEOUT_SEC = 600
VALID_LABELS = {"central", "secondary", "cannot_determine"}


def build_user_prompt(unit: dict, abstracts: dict, out_path: Path) -> str:
    orig = abstracts.get(unit["original_doi"], {})
    abstract = (orig.get("abstract") or "").strip() or "(abstract unavailable)"
    claims = "\n".join(
        f'- row_id: {r["row_id"]}\n  claim: {r["claim_description"]}'
        for r in unit["rows"]
    )
    return f"""Label the centrality of each claim below, following your instructions.

ORIGINAL PAPER (the claims come from this paper):
- Title: {unit["original_title"]}
- Journal: {unit["original_journal"]} ({unit["original_year"]})
- DOI: {unit["original_doi"]}
- Abstract: {abstract}

CLAIMS TO LABEL (each was tested by the replication paper in this folder):
{claims}

The replication paper's files are in: {unit["folder"]}
Read its abstract.md and the introduction/background portion of body.md (or the
PDF if markdown is missing) to see how it describes the original study.
Remember the blinding rule: ignore all replication outcomes.

Write your output JSON to exactly this path: {out_path}
"""


def result_valid(out_path: Path, unit: dict) -> bool:
    try:
        data = json.loads(out_path.read_text())
        labels = data["labels"]
        got = {l["row_id"] for l in labels}
        want = {r["row_id"] for r in unit["rows"]}
        return got == want and all(l["label"] in VALID_LABELS for l in labels)
    except Exception:
        return False


def run_unit(unit: dict, abstracts: dict, tag: str, model: str, show_prompt: bool) -> str:
    folder = Path(unit["folder"])
    tag_dir = folder / tag
    out_path = tag_dir / "centrality_result.json"
    if result_valid(out_path, unit):
        return f"skip (done): {folder.name}"
    tag_dir.mkdir(exist_ok=True)

    user_prompt = build_user_prompt(unit, abstracts, out_path)
    if show_prompt:
        print("=" * 70, f"\nUSER PROMPT for {folder.name}:\n{user_prompt}\n", "=" * 70)

    cmd = [
        "claude",
        "--print",
        "--output-format", "json",
        "--model", model,
        "--max-turns", "40",
        "--system-prompt", PROMPT_PATH.read_text(),
        "--allowedTools", "Read", "Grep", "Glob", "Write",
        "--add-dir", str(folder),
        "--dangerously-skip-permissions",
        user_prompt,
    ]
    start = time.monotonic()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = proc.communicate(timeout=TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return f"TIMEOUT: {folder.name}"
    wall = time.monotonic() - start

    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        envelope = {"raw_stdout": stdout[:2000], "stderr": stderr[:2000]}
    (tag_dir / "debug_log.json").write_text(json.dumps({
        "prompt_version": VERSION_PATH.read_text().strip(),
        "model": model,
        "wall_sec": round(wall, 1),
        "returncode": proc.returncode,
        "envelope": envelope,
    }, indent=1))

    if proc.returncode != 0:
        return f"CLI ERROR ({proc.returncode}): {folder.name}: {(stderr or stdout)[:200]}"
    if not result_valid(out_path, unit):
        return f"INVALID OUTPUT: {folder.name}"
    return f"ok ({wall:.0f}s): {folder.name}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=None, help="only first N units")
    ap.add_argument("--show-prompt", action="store_true")
    args = ap.parse_args()

    manifest = common.load_manifest()  # re-asserts the blinding whitelist
    abstracts = json.loads(common.ABSTRACTS_PATH.read_text())
    units = manifest["units"][: args.limit] if args.limit else manifest["units"]
    print(f"{len(units)} units, tag={args.tag}, model={args.model}, workers={args.workers}")

    failures = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_unit, u, abstracts, args.tag, args.model, args.show_prompt): u
                for u in units}
        for i, fut in enumerate(as_completed(futs), 1):
            msg = fut.result()
            if not msg.startswith(("ok", "skip")):
                failures += 1
            print(f"[{i}/{len(units)}] {msg}", flush=True)

    print(f"done: {failures} failures (re-run same command to retry them)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
