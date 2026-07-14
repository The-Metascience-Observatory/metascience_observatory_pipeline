"""Compare Dan's validation labels against the AI passes.

Run after validation_sheet.csv's dan_label column is filled in. Reports
Cohen's kappa (Dan vs each pass, and vs AI-consensus rows), a confusion
matrix, per-source-bucket agreement, and the disagreement list with the AI
rationales.

Decision rule (from the pilot plan):
  kappa >= 0.7  -> proceed to the full-database run
  0.5 - 0.7     -> refine the rubric and re-pilot
  < 0.5         -> approach not reliable as specified

Usage:
    python -m mo_pipeline.label_centrality.compute_agreement
"""
from __future__ import annotations

import csv
from collections import Counter, defaultdict

from mo_pipeline.label_centrality import common
from mo_pipeline.label_centrality.collate import cohen_kappa

LABELS = ("central", "secondary", "cannot_determine")


def main() -> int:
    with open(common.VALIDATION_SHEET_PATH, newline="") as f:
        dan = {r["row_id"]: r["dan_label"].strip().lower() for r in csv.DictReader(f)}
    dan = {k: v for k, v in dan.items() if v in LABELS}
    if not dan:
        print("No filled dan_label values found in", common.VALIDATION_SHEET_PATH)
        return 1

    with open(common.LABELS_CSV_PATH, newline="") as f:
        ai = {r["row_id"]: r for r in csv.DictReader(f)}
    with open(common.ROWS_CSV_PATH, newline="") as f:
        meta = {r["row_id"]: r for r in csv.DictReader(f)}

    print(f"Dan labeled {len(dan)} rows\n")
    for tag, key in (("pass A", "label_a"), ("pass B", "label_b")):
        pairs = [(dan[rid], ai[rid][key]) for rid in dan if ai.get(rid, {}).get(key)]
        agree = sum(a == b for a, b in pairs) / len(pairs)
        print(f"Dan vs {tag}: {agree:.1%} agreement, kappa {cohen_kappa(pairs):.3f} (n={len(pairs)})")

    consensus = {rid: r["label_a"] for rid, r in ai.items()
                 if r["ai_agree"] == "1" and rid in dan}
    pairs = [(dan[rid], lab) for rid, lab in consensus.items()]
    if pairs:
        agree = sum(a == b for a, b in pairs) / len(pairs)
        kappa = cohen_kappa(pairs)
        print(f"Dan vs AI-consensus rows: {agree:.1%}, kappa {kappa:.3f} (n={len(pairs)})")
        verdict = ("PROCEED to full run" if kappa >= 0.7 else
                   "REFINE rubric and re-pilot" if kappa >= 0.5 else
                   "NOT RELIABLE as specified")
        print(f"decision rule -> {verdict}")

    print("\nConfusion matrix (rows=Dan, cols=pass A):")
    mat = Counter((dan[rid], ai[rid]["label_a"]) for rid in dan if ai.get(rid, {}).get("label_a"))
    header = " " * 18 + "".join(f"{c:>18}" for c in LABELS)
    print(header)
    for d in LABELS:
        print(f"{d:>18}" + "".join(f"{mat.get((d, c), 0):>18}" for c in LABELS))

    print("\nAgreement by source bucket (Dan vs pass A):")
    by_bucket = defaultdict(list)
    for rid in dan:
        if ai.get(rid, {}).get("label_a") and rid in meta:
            by_bucket[meta[rid]["source_bucket"]].append(
                (dan[rid], ai[rid]["label_a"]))
    for b, pairs in sorted(by_bucket.items()):
        agree = sum(a == c for a, c in pairs) / len(pairs)
        print(f"  {b:16s} {agree:.0%} (n={len(pairs)})")

    print("\nDisagreements (Dan vs pass A):")
    for rid in sorted(dan):
        r = ai.get(rid)
        if r and r["label_a"] and r["label_a"] != dan[rid]:
            print(f"- {rid} Dan={dan[rid]} AI={r['label_a']}: "
                  f"{r['claim_description'][:80]!r}\n    AI rationale: {r['rationale_a'][:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
