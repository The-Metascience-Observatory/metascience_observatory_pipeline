#!/usr/bin/env python3
"""Fill a blinded gold coding sheet with one AI coder.

Gold is coded twice and adjudicated (benchmarking/README.md, "Building gold").
Where a second *human* coder is not available, the protocol allows an AI coder of
a different model family -- never the model under test -- whose proposals become
gold only after human adjudication. This script is that coder.

    python benchmarking/ai_coder.py --gold-version 1 --coder ling26 \
        --model inclusionai/ling-2.6-flash
    python benchmarking/ai_coder.py --gold-version 1 --coder luna56 \
        --model openai/gpt-5.6-luna

Run `harness.py coding-sheet --gold-version N --coder <id> --no-stats` first: this
fills that sheet in place, keeping every anchored row and appending one row with a
blank `row_id` for each additional replication the coder finds. Those extra rows
are the point -- an exhaustive enumeration is what makes entry precision
measurable, which no external ground truth can currently support.

Blinding: the paper text comes from `extract_core.assemble_input`, which reads only
renditions, body.md, abstract.md and references.json. It cannot reach a `<tag>/`
subfolder, and `_assert_blinded` re-checks that on every paper.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH_DIR.parent))
sys.path.insert(1, str(BENCH_DIR))

from mo_pipeline.discover.screening_backend import get_backend, parse_json_reply  # noqa: E402
from mo_pipeline.extract.extract_core import assemble_input, paper_artifacts  # noqa: E402

import matching  # noqa: E402
from harness import CODING_DIR, CODEBOOK, paper_dir_for, read_csv, write_csv  # noqa: E402

MAX_INPUT_CHARS = 120_000
# The screening backend defaults to 400 output tokens -- right for a yes/no verdict,
# far too small for a coded entry list. Worse, a reasoning model spends the budget
# thinking before it emits anything, so a budget that is merely tight returns an
# empty string or a JSON object cut mid-key, both of which read downstream as "no
# parseable JSON" rather than "truncated". Measured on ling-3.0-flash coding one
# paper: 8000 tokens -> finish_reason "length", empty text; 16000 -> 8925 tokens,
# finish_reason "stop", parses.
MAX_OUTPUT_TOKENS = 16000

INSTRUCTIONS = """
You are coding replication studies for a research ground-truth set, following the
codebook above. You are shown ONE paper: its DOI, abstract, full text and
reference list.

Enumerate EVERY replication entry in the paper using the codebook's unit-of-coding
rules. Do not stop at the first one, and do not merge distinct effects into one
entry. If the paper is not a replication study at all, say so instead.

Reply with a JSON object only, no prose:

{
  "is_replication_paper": "yes" | "no",
  "why_negative": "<one sentence, only when 'no'>",
  "entries": [
    {
      "result": "success" | "failure" | "inconclusive" | "reversal",
      "replication_type": "direct" | "close experiment" | "close extension" | "conceptual",
      "original_url": "https://doi.org/<DOI of the ORIGINAL study being replicated>",
      "original_title": "", "original_authors": "", "original_year": "",
      "original_journal": "",
      "description": "<what specific effect this entry replicates>",
      "citation_sentence": "<the sentence from THIS paper that cites the original>",
      "gt_ambiguity": "clear" | "ambiguous",
      "notes": "<anything an adjudicator needs>"
    }
  ]
}

Rules that matter most:
- `result` describes the REPLICATION's outcome, judged as the codebook directs:
  the authors' own explicit statement first, the evidence second.
- Never invent an `original_url`. Leave it empty if the paper does not identify
  the original clearly enough to name a DOI.
- `citation_sentence` must be copied verbatim from the paper.
- Do not report statistics; they are not being coded.
""".strip()

CORE_FIELDS = ["result", "replication_type", "original_url", "original_title",
               "original_authors", "original_year", "original_journal",
               "description", "citation_sentence"]


REFS_HEADER = re.compile(r"^\[REFERENCE LIST: .*?\]$", re.M)


def _looks_like_prose(block: str) -> bool:
    """Does a reference list read as bibliography, or as OCR noise?

    GROBID on a badly encoded PDF produces entries like `D 3&0201. 01&%00: 3.`
    Measured on the FLoRa hold-out, 10 of 73 papers carry a list like that. Handing
    it to a coder under a `[REFERENCE LIST]` header is worse than handing it
    nothing: the header asserts these are the paper's references, which invites a
    fabricated `original_url`. Vowel share and the count of word-shaped tokens
    separate the two cleanly.
    """
    lines = [l for l in block.splitlines() if len(l.strip()) > 15]
    if not lines:
        return False
    good = 0
    for l in lines:
        letters = [c for c in l if c.isalpha()]
        if not letters:
            continue
        vowels = sum(1 for c in letters if c.lower() in "aeiou") / len(letters)
        words = sum(1 for w in l.split() if re.fullmatch(r"[A-Za-z][A-Za-z'.-]{2,}", w))
        if vowels >= 0.22 and words >= 3:
            good += 1
    return good / len(lines) >= 0.5


def drop_unreadable_references(prompt: str) -> tuple[str, bool]:
    """Replace a junk reference block with an honest statement of its absence."""
    m = REFS_HEADER.search(prompt)
    if not m:
        return prompt, False
    head, block = prompt[:m.start()], prompt[m.end():]
    tail = ""
    nxt = re.search(r"^\[[A-Z][^\]]*\]$", block, re.M)
    if nxt:
        block, tail = block[:nxt.start()], block[nxt.start():]
    else:
        # The reference list is the last section, so what follows it is the
        # trailing instruction ("Reply with the JSON object only."). Losing that
        # would cost the reply format, so keep a short final paragraph.
        chunks = block.rsplit("\n\n", 1)
        if len(chunks) == 2 and len(chunks[1].strip()) < 200:
            block, tail = chunks[0], chunks[1]
    if _looks_like_prose(block):
        return prompt, False
    return (head + "[REFERENCE LIST: none]\nThis paper's machine-extracted reference list was "
            "unreadable and has been withheld. Identify originals from the inline citations in "
            "the text above, and leave `original_url` empty rather than guessing a DOI.\n\n"
            + tail), True


class BackendUnusable(Exception):
    """The backend can serve nothing: a dead model id, a rejected key, no credit."""


def _hit_the_token_ceiling(r) -> bool:
    """Did the model stop because it ran out of budget rather than finished?"""
    try:
        return (r.raw or {}).get("choices", [{}])[0].get("finish_reason") == "length"
    except (AttributeError, IndexError, TypeError):
        return False


def _assert_blinded(paper_dir: Path, art: dict) -> None:
    """No path handed to the model may live in a `<tag>/` extraction subfolder."""
    for p in [art.get("primary"), art.get("pdf"), art.get("structured_raw"), *art.get("supporting", [])]:
        if p is not None and Path(p).resolve().parent != paper_dir.resolve():
            raise AssertionError(f"blinding violation: {p} is not a direct child of {paper_dir}")


def code_paper(paper_dir: Path, backend, system_prompt: str) -> dict:
    art = paper_artifacts(paper_dir)
    if not art["has_fulltext"]:
        raise FileNotFoundError(f"no readable full text in {paper_dir}")
    _assert_blinded(paper_dir, art)
    user_prompt, info = assemble_input(paper_dir, art, MAX_INPUT_CHARS)
    user_prompt, dropped = drop_unreadable_references(user_prompt)
    info["refs_withheld"] = dropped
    if dropped:
        info["refs_source"] = "withheld (unreadable)"
    last, truncated = "", False
    for attempt in range(2):
        r = backend.complete(system_prompt, user_prompt, cwd=paper_dir)
        if getattr(r, "fatal", False):
            # Describes the backend, not this paper: retrying it 166 times would
            # just print the same error 166 times (screening_backend ab7fe09).
            raise BackendUnusable(f"{backend.name}/{backend.model}: {r.error}")
        if r.error:
            if attempt:
                raise RuntimeError(f"{backend.name} failed for {paper_dir.name}: {r.error}")
            continue
        data = parse_json_reply(r.text)
        if isinstance(data, dict):
            info["output_tokens"] = (r.usage or {}).get("output_tokens")
            info["cost_usd"] = (r.usage or {}).get("cost_usd")
            data["_info"] = info
            return data
        last = (r.text or "").strip()
        truncated = truncated or _hit_the_token_ceiling(r)
    if truncated:
        raise RuntimeError(f"{backend.name} ran out of output tokens on {paper_dir.name} "
                           f"(budget {getattr(backend, 'max_tokens', '?')}, finish_reason 'length'): "
                           f"raise --max-output-tokens")
    if not last:
        raise RuntimeError(f"{backend.name} returned an empty reply for {paper_dir.name} "
                           f"(output token budget {getattr(backend, 'max_tokens', '?')} — raise "
                           f"--max-output-tokens if the model spent it all reasoning)")
    raise RuntimeError(f"{backend.name} gave no parseable JSON for {paper_dir.name}: {last[:200]}")


def _entries(data: dict) -> list[dict]:
    """The model's entries, however it wrapped them."""
    for key in ("entries", "replications", "rows"):
        v = data.get(key)
        if isinstance(v, dict):
            return [v]
        if isinstance(v, list):
            return [e for e in v if isinstance(e, dict)]
    return []


def _score(anchor: dict, entry: dict) -> float:
    """How well a coded entry answers an anchored sheet row.

    The sheet anchors a row to an external record with `original_hint` (a title
    and year). Reuses the matcher's own string comparison so alignment here and
    matching at evaluation time cannot drift apart.
    """
    hint = (anchor.get("original_hint") or "").strip()
    if not hint:
        return 0.0
    title = " ".join(str(entry.get(k) or "") for k in ("original_title", "original_year"))
    return matching.string_ratio(hint, title)


def fill_sheet(rows: list[dict], results: dict[str, dict], coder: str) -> list[dict]:
    """Anchored rows keep their row_id; every unclaimed entry becomes a new row."""
    out: list[dict] = []
    by_paper: dict[str, list[dict]] = {}
    for r in rows:
        by_paper.setdefault(r["paper_folder"], []).append(r)

    for folder, sheet_rows in by_paper.items():
        data = results.get(folder)
        if data is None:                      # not coded (error / not on disk): leave blank
            out.extend(sheet_rows)
            continue
        is_rep = str(data.get("is_replication_paper", "")).strip().lower()
        entries = _entries(data)
        negatives = [r for r in sheet_rows if r["row_id"].endswith("#neg")]
        for r in negatives:
            r["is_replication_paper"] = "yes" if (is_rep == "yes" or entries) else "no"
            r["why_negative"] = "" if r["is_replication_paper"] == "yes" else str(data.get("why_negative", ""))[:300]
            r["notes"] = (r.get("notes") or "") + f" [coded by {coder}]"
        positives = [r for r in sheet_rows if not r["row_id"].endswith("#neg")]
        if not positives:
            out.extend(sheet_rows)
            continue

        # Greedy best-first alignment: the strongest (row, entry) pair wins, so a
        # paper whose anchors resemble each other cannot have them all claimed by
        # the same entry.
        pairs = sorted(((_score(r, e), i, j) for i, r in enumerate(positives) for j, e in enumerate(entries)),
                       reverse=True)
        taken_r: set[int] = set()
        taken_e: set[int] = set()
        for sc, i, j in pairs:
            if sc < 0.5 or i in taken_r or j in taken_e:
                continue
            taken_r.add(i); taken_e.add(j)
            _write(positives[i], entries[j], coder)
        # anchors with no entry: leave the coded fields blank but say why
        for i, r in enumerate(positives):
            if i not in taken_r:
                if len(entries) == 1 and not taken_e:
                    _write(r, entries[0], coder)
                    taken_e.add(0)
                else:
                    r["notes"] = (r.get("notes") or "") + f" [{coder}: no entry matched this anchor]"
        # entries nobody anchored: the exhaustive-enumeration payload
        for j, e in enumerate(entries):
            if j in taken_e:
                continue
            extra = {k: "" for k in positives[0]}
            extra.update({"row_id": "", "replication_doi": positives[0]["replication_doi"],
                          "paper_folder": folder, "external_row_id": "", "original_hint": "",
                          "is_replication_paper": "yes"})
            _write(extra, e, coder)
            extra["notes"] = (extra.get("notes") or "") + f" [{coder}: extra entry, not in the external record]"
            positives.append(extra)
        out.extend(negatives + positives)
    return out


def _write(row: dict, entry: dict, coder: str) -> None:
    for f in CORE_FIELDS:
        if f in row:
            row[f] = str(entry.get(f) or "").strip()
    if "gt_ambiguity" in row:
        row["gt_ambiguity"] = str(entry.get("gt_ambiguity") or "clear").strip()
    note = str(entry.get("notes") or "").strip()
    row["notes"] = ((row.get("notes") or "") + (f" {note}" if note else "")).strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold-version", type=int, required=True)
    ap.add_argument("--coder", required=True, help="coder id; names the sheet to fill")
    ap.add_argument("--model", required=True, help="OpenRouter slug, e.g. inclusionai/ling-2.6-flash")
    ap.add_argument("--provider", default="openrouter")
    ap.add_argument("--limit", type=int, default=None, help="code only the first N papers (pilot)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-output-tokens", type=int, default=MAX_OUTPUT_TOKENS,
                    help=f"per-call output budget (default {MAX_OUTPUT_TOKENS}); a multi-entry "
                         "paper needs thousands, not the screening default of 400")
    args = ap.parse_args()

    if args.provider == "claude_cli":
        sys.exit("refusing: the coder must not be the model under test (see README, 'Building gold'). "
                 "Use a different family via --provider openrouter.")
    sheet_path = CODING_DIR / f"sheet_gold_v{args.gold_version}_{args.coder}.csv"
    if not sheet_path.exists():
        sys.exit(f"{sheet_path} not found — run `harness.py coding-sheet --gold-version "
                 f"{args.gold_version} --coder {args.coder} --no-stats` first")
    rows = read_csv(sheet_path)
    folders = list(dict.fromkeys(r["paper_folder"] for r in rows))
    if args.limit:
        # Code a subset, but never drop the rest of the sheet: the file is written
        # back whole, so filtering `rows` here would delete every paper outside the
        # pilot. Untouched rows pass through fill_sheet unchanged.
        folders = folders[:args.limit]

    system_prompt = CODEBOOK.read_text() + "\n\n---\n\n" + INSTRUCTIONS
    backend = get_backend(provider=args.provider, model=args.model,
                          max_tokens=args.max_output_tokens)
    print(f"coding {len(folders)} papers with {backend.name}/{backend.model} as coder {args.coder!r}",
          file=sys.stderr)

    results: dict[str, dict] = {}
    errors: list[str] = []
    start = time.monotonic()

    def one(folder: str):
        d = paper_dir_for(folder)
        try:
            if d is None:
                raise FileNotFoundError(f"{folder} is not readable in the corpus or the legacy GT corpus")
            return folder, code_paper(d, backend, system_prompt), None
        except BackendUnusable:
            raise
        except Exception as exc:                                  # noqa: BLE001
            return folder, None, f"{folder}: {type(exc).__name__}: {exc}"

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for n, (folder, data, err) in enumerate(pool.map(one, folders), 1):
                if err:
                    errors.append(err)
                    print(f"[{n}/{len(folders)}] FAIL {err[:160]}", file=sys.stderr)
                else:
                    results[folder] = data
                    print(f"[{n}/{len(folders)}] {folder}: {len(_entries(data))} entry(ies)",
                          file=sys.stderr)
    except BackendUnusable as exc:
        # Save what was coded before giving up, so the run is resumable.
        write_csv(sheet_path, fill_sheet(rows, results, args.coder), list(rows[0].keys()) if rows else None)
        sys.exit(f"backend unusable, stopping after {len(results)} paper(s): {exc}\n"
                 f"Check the model id against https://openrouter.ai/api/v1/models before re-running.")

    filled = fill_sheet(rows, results, args.coder)
    assert len(filled) >= len(rows), "fill_sheet must never lose a sheet row"
    write_csv(sheet_path, filled, list(rows[0].keys()) if rows else None)
    # The output cap is the one setting that fails silently when it is too small,
    # so report the headroom actually used rather than leaving the next run to guess.
    used = [d["_info"].get("output_tokens") for d in results.values()
            if isinstance(d.get("_info"), dict) and d["_info"].get("output_tokens")]
    if used:
        cap = args.max_output_tokens
        print(f"output tokens: max {max(used):,} of a {cap:,} cap "
              f"({max(used) / cap:.0%} of budget), median {sorted(used)[len(used) // 2]:,}"
              + ("  ** raise --max-output-tokens **" if max(used) > 0.9 * cap else ""),
              file=sys.stderr)
    spent = [d["_info"].get("cost_usd") or 0 for d in results.values() if isinstance(d.get("_info"), dict)]
    if spent:
        print(f"cost: ${sum(spent):.4f} for {len(spent)} paper(s) "
              f"(${sum(spent) / len(spent):.4f}/paper)", file=sys.stderr)
    coded = sum(1 for r in filled if (r.get("result") or "").strip())
    print(f"\n{len(results)} papers coded, {len(errors)} failed; sheet now has {len(filled)} rows "
          f"({coded} with a result, {len(filled) - len(rows)} added) -> {sheet_path}", file=sys.stderr)
    print(f"runtime {time.monotonic() - start:.0f}s", file=sys.stderr)
    return 1 if errors and not results else 0


if __name__ == "__main__":
    raise SystemExit(main())
