#!/usr/bin/env python3
"""
Re-runnable FLoRa ground-truth evaluation harness.

FLoRa (FORRT Library of Reproduction and Replication Attempts) rows are
paper-level outcomes per (original study, replication paper) pair, hand-coded
from the replication authors' own characterization (success/failure/
inconclusive; no reversal category). The pipeline emits one row per
*experiment*, so evaluation matches AI experiment rows to FLoRa rows by
original-study identity (DOI first, fuzzy title/authors/year fallback) and
aggregates them to one paper-level verdict per FLoRa row.

Subcommands (all idempotent — safe to re-run at any point mid-extraction):

  setup     xlsx -> flora_ground_truth.csv + doi-runs 'flora_gt' (all papers)
            and 'flora_pilot' (stratified sample). Skips runs that exist.
  status    coverage funnel per run (downloaded/converted/extracted) and
            writes include_list_remaining.txt for resuming extraction.
  evaluate  match + aggregate + score whatever has been extracted so far;
            writes report/metrics/confusion-matrix/disagreements under
            benchmarking/flora_eval/<run>/.

Typical loop:
    python benchmarking/flora_harness.py setup
    python -m mo_pipeline.discover.download_all_confirmed --doi-csv data/doi_runs/flora_pilot/dois.csv
    pdf4llm batch <inbox> -o <papers> --mode full-grobid --workers 4 --movepdf --resume
    python -m mo_pipeline.extract.extract <papers> --batch --level full \
        --include-list data/doi_runs/flora_pilot/include_list.txt --tag flora_pilot
    python benchmarking/flora_harness.py evaluate --run flora_pilot
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate_enhanced import (authors_match, fuzzy_match,  # noqa: E402
                               generate_confusion_matrix_png, year_match)

from mo_pipeline import config  # noqa: E402
from mo_pipeline.corpus.models import doi_to_folder  # noqa: E402
from mo_pipeline.discover.doi_runs import (create_run, delete_run,  # noqa: E402
                                           load_dois, normalize_doi, run_status)
from mo_pipeline.label_centrality.collate import cohen_kappa  # noqa: E402

BENCH_DIR = Path(__file__).resolve().parent
GT_CSV = BENCH_DIR / "flora_ground_truth.csv"
SKIPPED_CSV = BENCH_DIR / "flora_skipped.csv"
EVAL_DIR = BENCH_DIR / "flora_eval"
DEFAULT_XLSX = BENCH_DIR.parent / "flora_replications_for_extraction_testing.xlsx"

FULL_RUN = "flora_gt"
PILOT_RUN = "flora_pilot"

# Same suffix priority as extract.collate_results, but only inside tag dirs.
RESULT_SUFFIXES = ("_result_xml.json", "_result_html.json", "_result_pdf_only.json",
                   "_result_full.json", "_result_mid.json", "_result.json")

THREE_WAY = ["success", "failure", "inconclusive"]


# ── setup ────────────────────────────────────────────────────────────────────
def load_ground_truth_xlsx(xlsx_path: Path) -> tuple[list[str], list[dict]]:
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = [str(h) for h in next(rows)]
    out = []
    for r in rows:
        if not any(v is not None and str(v).strip() for v in r):
            continue
        out.append({h: ("" if v is None else str(v).strip()) for h, v in zip(header, r)})
    return header, out


def cmd_setup(args) -> None:
    header, raw = load_ground_truth_xlsx(Path(args.xlsx))
    print(f"Loaded {len(raw)} rows from {args.xlsx}")

    skipped, kept, seen_pairs = [], [], set()
    for i, row in enumerate(raw, start=2):  # xlsx line number (1 = header)
        rep_doi = normalize_doi(row["replication_url"])
        orig_doi = normalize_doi(row["original_url"])
        if rep_doi is None:
            skipped.append({**row, "skip_reason": "replication_url is not a DOI",
                            "xlsx_line": i})
            continue
        pair = (orig_doi or row["original_url"].lower(), rep_doi)
        if pair in seen_pairs:
            skipped.append({**row, "skip_reason": "duplicate (original, replication) pair",
                            "xlsx_line": i})
            continue
        seen_pairs.add(pair)
        kept.append({**row, "row_id": f"flora_{i}",
                     "replication_doi_norm": rep_doi,
                     "original_doi_norm": orig_doi or ""})

    gt_header = header + ["row_id", "replication_doi_norm", "original_doi_norm"]
    with open(GT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=gt_header)
        w.writeheader()
        w.writerows(kept)
    print(f"Ground truth: {len(kept)} rows -> {GT_CSV}")

    if skipped:
        with open(SKIPPED_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=header + ["skip_reason", "xlsx_line"])
            w.writeheader()
            w.writerows(skipped)
        print(f"Skipped rows: {len(skipped)} -> {SKIPPED_CSV}")
        for s in skipped:
            print(f"  line {s['xlsx_line']}: {s['skip_reason']} ({s['replication_url']})")

    # Paper-level outcome buckets for stratified pilot sampling: a paper with
    # multiple FLoRa rows of differing results goes in the inconclusive bucket
    # (mirrors the evaluation's mixed -> inconclusive aggregation).
    by_paper: dict[str, set] = {}
    for row in kept:
        by_paper.setdefault(row["replication_doi_norm"], set()).add(row["result"])
    all_dois = sorted(by_paper)
    print(f"Unique replication papers: {len(all_dois)}")

    def bucket(doi: str) -> str:
        results = by_paper[doi]
        return results.copy().pop() if len(results) == 1 else "inconclusive"

    rng = random.Random(args.seed)
    buckets: dict[str, list[str]] = {}
    for doi in all_dois:
        buckets.setdefault(bucket(doi), []).append(doi)
    n_pilot = min(args.pilot_size, len(all_dois))
    pilot: list[str] = []
    for cat, dois in sorted(buckets.items()):
        take = round(n_pilot * len(dois) / len(all_dois))
        pilot.extend(rng.sample(dois, min(take, len(dois))))
    # rounding drift: top up / trim to exactly n_pilot
    remaining = [d for d in all_dois if d not in set(pilot)]
    rng.shuffle(remaining)
    pilot = (pilot + remaining)[:n_pilot]
    pilot_buckets = Counter(bucket(d) for d in pilot)
    print(f"Pilot sample: {n_pilot} papers, stratified {dict(pilot_buckets)}")

    for slug, dois in [(FULL_RUN, all_dois), (PILOT_RUN, sorted(pilot))]:
        run_dir = config.DOI_RUNS_DIR / slug
        if run_dir.exists():
            if args.force:
                delete_run(slug)
                print(f"Run '{slug}': deleted existing (--force)")
            else:
                print(f"Run '{slug}': already exists, leaving as-is "
                      f"({len(load_dois(slug))} DOIs). Use --force to recreate.")
                continue
        meta = create_run(slug, csv_text="doi\n" + "\n".join(dois) + "\n")
        print(f"Run '{slug}': created with {meta['n_dois']} DOIs "
              f"-> {config.DOI_RUNS_DIR / slug}")


# ── shared: locate extraction output per paper ──────────────────────────────
def find_result_json(folder: str, tags: list[str]) -> Path | None:
    paper_dir = config.PAPERS_DIR / folder
    for tag in tags:
        tag_dir = paper_dir / tag
        if not tag_dir.is_dir():
            continue
        for suffix in RESULT_SUFFIXES:
            candidate = tag_dir / f"{folder}{suffix}"
            if candidate.exists():
                return candidate
    return None


def default_tags(run: str) -> list[str]:
    # The full run reuses pilot extractions so those papers aren't re-extracted.
    return [FULL_RUN, PILOT_RUN] if run == FULL_RUN else [run]


# ── status ───────────────────────────────────────────────────────────────────
def cmd_status(args) -> None:
    for slug in (PILOT_RUN, FULL_RUN):
        run_dir = config.DOI_RUNS_DIR / slug
        if not run_dir.exists():
            print(f"{slug}: not created yet (run setup)")
            continue
        st = run_status(slug)
        print(f"\n{slug}: {st['n_dois']} DOIs")
        print(f"  in corpus:       {st['in_corpus']}")
        print(f"  converted:       {st['converted']}")
        print(f"  inbox (pending): {st['inbox_pending']}")
        print(f"  missing:         {st['missing']}")

        tags = default_tags(slug)
        dois = load_dois(slug)
        extracted, remaining = [], []
        for doi in dois:
            folder = doi_to_folder(doi)
            if find_result_json(folder, tags) is not None:
                extracted.append(folder)
            elif (config.PAPERS_DIR / folder).is_dir():
                remaining.append(folder)
        print(f"  extracted ({'/'.join(tags)}): {len(extracted)}")
        remaining_path = run_dir / "include_list_remaining.txt"
        remaining_path.write_text("\n".join(remaining) + ("\n" if remaining else ""))
        print(f"  extractable remaining: {len(remaining)} -> {remaining_path}")


# ── evaluate ─────────────────────────────────────────────────────────────────
def map3(result: str) -> str:
    """Pipeline 4-way -> FLoRa 3-way (FLoRa has no reversal category)."""
    r = (result or "").strip().lower()
    return "failure" if r == "reversal" else r


def aggregate(mapped: list[str]) -> str:
    """Experiment rows -> one paper-level verdict (mixed mirrors FLoRa 'Mixed')."""
    return mapped[0] if len(set(mapped)) == 1 else "inconclusive"


def match_paper(gt_rows: list[dict], ext_rows: list[dict]) -> dict[str, list[dict]]:
    """Assign each AI experiment row to at most one FLoRa row of the same paper.

    Original-DOI equality trumps fuzzy matching; fuzzy needs >=2 of
    title/authors/year. Returns {row_id: [ext_rows...]} (unassigned rows are
    'extra extractions' and are simply absent).
    """
    assigned: dict[str, list[dict]] = {g["row_id"]: [] for g in gt_rows}
    for ext in ext_rows:
        ext_doi = normalize_doi(ext.get("original_url", ""))
        best_id, best_score = None, 0
        for gt in gt_rows:
            if ext_doi and gt["original_doi_norm"] and ext_doi == gt["original_doi_norm"]:
                score = 10
            else:
                score = sum([
                    fuzzy_match(ext.get("original_title", ""), gt["original_title"]),
                    authors_match(ext.get("original_authors", ""), gt["original_authors"]),
                    year_match(ext.get("original_year", ""), gt["original_year"]),
                ])
                if score < 2:
                    score = 0
            if score > best_score:
                best_id, best_score = gt["row_id"], score
        if best_id is not None:
            assigned[best_id].append(ext)
    return assigned


def cmd_evaluate(args) -> None:
    if not GT_CSV.exists():
        sys.exit(f"ERROR: {GT_CSV} not found — run setup first.")
    run = args.run
    tags = args.tags.split(",") if args.tags else default_tags(run)
    run_dois = set(load_dois(run))

    with open(GT_CSV, newline="", encoding="utf-8") as f:
        gt_all = [r for r in csv.DictReader(f)]
    gt = [r for r in gt_all if r["replication_doi_norm"] in run_dois]
    papers: dict[str, list[dict]] = {}
    for r in gt:
        papers.setdefault(r["replication_doi_norm"], []).append(r)
    print(f"Run '{run}' (tags: {','.join(tags)}): {len(gt)} GT rows, "
          f"{len(papers)} papers")

    funnel = Counter()
    matched_rows, unmatched_rows, extra_count = [], [], 0
    for doi, gt_rows in sorted(papers.items()):
        folder = doi_to_folder(doi)
        funnel["papers_total"] += 1
        if not (config.PAPERS_DIR / folder).is_dir():
            funnel["papers_not_converted"] += 1
            for g in gt_rows:
                unmatched_rows.append({**_um(g), "reason": "paper not downloaded/converted"})
            continue
        result_json = find_result_json(folder, tags)
        if result_json is None:
            funnel["papers_not_extracted"] += 1
            for g in gt_rows:
                unmatched_rows.append({**_um(g), "reason": "not yet extracted"})
            continue
        funnel["papers_extracted"] += 1
        try:
            data = json.loads(result_json.read_text())
        except (json.JSONDecodeError, IOError):
            funnel["papers_bad_json"] += 1
            for g in gt_rows:
                unmatched_rows.append({**_um(g), "reason": "unreadable result json"})
            continue
        ext_rows = data.get("replications") or []
        if not data.get("contains_replications") or not ext_rows:
            funnel["papers_no_replications_flag"] += 1
            for g in gt_rows:
                unmatched_rows.append({**_um(g), "reason": "pipeline says no replications"})
            continue

        assigned = match_paper(gt_rows, ext_rows)
        extra_count += len(ext_rows) - sum(len(v) for v in assigned.values())
        for g in gt_rows:
            hits = assigned[g["row_id"]]
            if not hits:
                unmatched_rows.append({**_um(g), "reason": "no matching experiment row",
                                       "n_ext_rows_in_paper": len(ext_rows)})
                continue
            raw = [(h.get("result") or "").strip().lower() for h in hits]
            ai = aggregate([map3(r) for r in raw])
            matched_rows.append({
                "row_id": g["row_id"],
                "replication_doi": doi,
                "original_title": g["original_title"][:80],
                "gt_result": g["result"].strip().lower(),
                "ai_result": ai,
                "ai_raw_results": ";".join(raw),
                "n_experiments_matched": len(hits),
                "agree": ai == g["result"].strip().lower(),
                "ai_confidence": ";".join((h.get("confidence") or "") for h in hits),
                "ai_explanation": " | ".join((h.get("explanation") or "") for h in hits)[:800],
                "flora_author_quote": g.get("description", "")[:800],
            })

    out_dir = Path(args.out_dir) if args.out_dir else EVAL_DIR / run
    out_dir.mkdir(parents=True, exist_ok=True)

    metrics = {
        "run": run, "tags": tags,
        "gt_rows_total": len(gt),
        "gt_rows_matched": len(matched_rows),
        "gt_rows_unmatched": len(unmatched_rows),
        "extra_ai_rows_unpenalized": extra_count,
        "funnel": dict(funnel),
    }
    if matched_rows:
        pairs = [(m["gt_result"], m["ai_result"]) for m in matched_rows]
        n = len(pairs)
        metrics["three_way"] = {
            "n": n,
            "accuracy": sum(a == b for a, b in pairs) / n,
            "cohen_kappa": cohen_kappa(pairs),
            "per_class": {},
        }
        for cat in THREE_WAY:
            tp = sum(1 for a, b in pairs if a == cat and b == cat)
            gt_n = sum(1 for a, _ in pairs if a == cat)
            pred_n = sum(1 for _, b in pairs if b == cat)
            metrics["three_way"]["per_class"][cat] = {
                "gt_count": gt_n, "pred_count": pred_n,
                "recall": tp / gt_n if gt_n else None,
                "precision": tp / pred_n if pred_n else None,
            }
        binary = [(a, b) for a, b in pairs if a in ("success", "failure")]
        metrics["binary_success_failure"] = {
            "n": len(binary),
            "accuracy": (sum(a == b for a, b in binary) / len(binary)) if binary else None,
            "ai_said_inconclusive": sum(1 for _, b in binary if b == "inconclusive"),
            "strict_flips": sum(1 for a, b in binary
                                if b in ("success", "failure") and a != b),
        }

        import pandas as pd
        df = pd.DataFrame({"gt_result": [a for a, _ in pairs],
                           "ext_result": [b for _, b in pairs]})
        generate_confusion_matrix_png(df, out_dir / "confusion_matrix.png", f"FLoRa {run}")

        with open(out_dir / "disagreements.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(matched_rows[0].keys()))
            w.writeheader()
            w.writerows(sorted(matched_rows, key=lambda m: m["agree"]))

    if unmatched_rows:
        fields = sorted({k for r in unmatched_rows for k in r})
        with open(out_dir / "unmatched.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(unmatched_rows)

    with open(out_dir / "metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    report = _report(metrics)
    (out_dir / "report.txt").write_text(report)
    print(report)
    print(f"\nOutputs -> {out_dir}/ (metrics.json, report.txt"
          + (", confusion_matrix.png, disagreements.csv" if matched_rows else "")
          + (", unmatched.csv" if unmatched_rows else "") + ")")


def _um(g: dict) -> dict:
    return {"row_id": g["row_id"], "replication_doi": g["replication_doi_norm"],
            "original_title": g["original_title"][:80], "gt_result": g["result"]}


def _report(m: dict) -> str:
    lines = ["=" * 72,
             f"FLoRa GROUND-TRUTH EVALUATION — run '{m['run']}' (tags: {','.join(m['tags'])})",
             "=" * 72,
             "",
             "COVERAGE FUNNEL",
             "-" * 72]
    f = m["funnel"]
    lines.append(f"GT rows in run:            {m['gt_rows_total']}")
    lines.append(f"Papers:                    {f.get('papers_total', 0)}")
    lines.append(f"  not downloaded/converted:{f.get('papers_not_converted', 0):>5}")
    lines.append(f"  not yet extracted:       {f.get('papers_not_extracted', 0):>5}")
    lines.append(f"  extracted:               {f.get('papers_extracted', 0):>5}")
    lines.append(f"  pipeline said no reps:   {f.get('papers_no_replications_flag', 0):>5}")
    lines.append(f"GT rows matched to an AI experiment row: {m['gt_rows_matched']}")
    lines.append(f"GT rows unmatched:                       {m['gt_rows_unmatched']}")
    lines.append(f"Extra AI rows (not in FLoRa, unpenalized): {m['extra_ai_rows_unpenalized']}")
    if "three_way" in m:
        t = m["three_way"]
        lines += ["", "RESULT CLASSIFICATION (3-way, pipeline reversal->failure)",
                  "-" * 72,
                  f"Accuracy:     {t['accuracy']:.1%}  (n={t['n']})",
                  f"Cohen kappa:  {t['cohen_kappa']:.3f}"]
        for cat, d in t["per_class"].items():
            rec = f"{d['recall']:.1%}" if d["recall"] is not None else "n/a"
            prec = f"{d['precision']:.1%}" if d["precision"] is not None else "n/a"
            lines.append(f"  {cat:<13} gt={d['gt_count']:<4} pred={d['pred_count']:<4} "
                         f"recall={rec:<7} precision={prec}")
        b = m["binary_success_failure"]
        acc = f"{b['accuracy']:.1%}" if b["accuracy"] is not None else "n/a"
        lines += ["", "BINARY success-vs-failure (GT success/failure rows only)",
                  "-" * 72,
                  f"Accuracy:            {acc}  (n={b['n']})",
                  f"AI said inconclusive: {b['ai_said_inconclusive']}"
                  "  (taxonomy mismatch candidates, count as errors above)",
                  f"Strict flips (success<->failure): {b['strict_flips']}"]
    lines += ["", "Note: FLoRa outcomes are the replication authors' own"
              " characterization (Mixed->inconclusive); the pipeline rubric is"
              " stricter (partial support -> inconclusive). Review"
              " disagreements.csv to separate taxonomy mismatch from real errors.",
              "=" * 72]
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("setup", help="xlsx -> ground truth CSV + doi runs")
    ps.add_argument("--xlsx", default=str(DEFAULT_XLSX))
    ps.add_argument("--pilot-size", type=int, default=50)
    ps.add_argument("--seed", type=int, default=42)
    ps.add_argument("--force", action="store_true",
                    help="recreate doi runs even if they exist")
    ps.set_defaults(func=cmd_setup)

    pt = sub.add_parser("status", help="coverage funnel per run")
    pt.set_defaults(func=cmd_status)

    pe = sub.add_parser("evaluate", help="score extracted results vs ground truth")
    pe.add_argument("--run", default=FULL_RUN, choices=[FULL_RUN, PILOT_RUN])
    pe.add_argument("--tags", default=None,
                    help="comma-separated extraction tags to accept "
                         "(default: run slug; flora_gt also falls back to flora_pilot)")
    pe.add_argument("--out-dir", default=None)
    pe.set_defaults(func=cmd_evaluate)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
