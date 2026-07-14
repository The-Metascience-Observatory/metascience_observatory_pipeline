"""Collate the two AI labeling passes and build Dan's validation sheet.

Reads {folder}/{tag}/centrality_result.json for both tags across all manifest
units, writes centrality_labels_pilot.csv (labels from both passes + agreement
flag), prints AI-AI agreement (percent + Cohen's kappa), and writes
validation_sheet.csv for human labeling. The validation sheet contains ONLY
blinded fields and an empty dan_label column — AI labels are deliberately
withheld so the human labels stay independent.

Usage:
    python -m mo_pipeline.label_centrality.collate --tag-a centrality_pilot_a --tag-b centrality_pilot_b
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from mo_pipeline.label_centrality import common


def cohen_kappa(pairs: list[tuple[str, str]]) -> float:
    n = len(pairs)
    if n == 0:
        return float("nan")
    cats = sorted({c for p in pairs for c in p})
    po = sum(a == b for a, b in pairs) / n
    pe = sum((sum(a == c for a, _ in pairs) / n) * (sum(b == c for _, b in pairs) / n)
             for c in cats)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def read_pass(units: list[dict], tag: str) -> dict[str, dict]:
    out = {}
    missing = []
    for u in units:
        path = Path(u["folder"]) / tag / "centrality_result.json"
        if not path.exists():
            missing.append(Path(u["folder"]).name)
            continue
        for l in json.loads(path.read_text())["labels"]:
            out[l["row_id"]] = l
    if missing:
        print(f"WARNING: tag {tag!r} missing results for {len(missing)} units: {missing[:5]}...")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag-a", default="centrality_pilot_a")
    ap.add_argument("--tag-b", default="centrality_pilot_b")
    args = ap.parse_args()

    manifest = common.load_manifest()
    abstracts = json.loads(common.ABSTRACTS_PATH.read_text())
    units = manifest["units"]
    pass_a = read_pass(units, args.tag_a)
    pass_b = read_pass(units, args.tag_b)

    rows_out = []
    for u in units:
        for r in u["rows"]:
            rid = r["row_id"]
            a, b = pass_a.get(rid), pass_b.get(rid)
            rows_out.append({
                "row_id": rid,
                "replication_doi": u["replication_doi"],
                "original_doi": u["original_doi"],
                "original_title": u["original_title"],
                "claim_description": r["claim_description"],
                "label_a": a["label"] if a else "",
                "confidence_a": a["confidence"] if a else "",
                "rationale_a": a["rationale"] if a else "",
                "label_b": b["label"] if b else "",
                "confidence_b": b["confidence"] if b else "",
                "rationale_b": b["rationale"] if b else "",
                "ai_agree": int(bool(a and b and a["label"] == b["label"])),
            })

    with open(common.LABELS_CSV_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows_out)
    print(f"wrote {common.LABELS_CSV_PATH} ({len(rows_out)} rows)")

    pairs = [(r["label_a"], r["label_b"]) for r in rows_out if r["label_a"] and r["label_b"]]
    if pairs:
        agree = sum(a == b for a, b in pairs) / len(pairs)
        print(f"AI-AI agreement: {agree:.1%} on {len(pairs)} rows | "
              f"Cohen's kappa: {cohen_kappa(pairs):.3f}")
        print("pass A labels:", Counter(a for a, _ in pairs).most_common())
        print("pass B labels:", Counter(b for _, b in pairs).most_common())

    # Validation sheet: blinded fields only, no AI labels.
    with open(common.VALIDATION_SHEET_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "row_id", "original_title", "original_journal", "original_year",
            "original_abstract_snippet", "claim_description", "dan_label"],
            lineterminator="\n")
        w.writeheader()
        for u in units:
            snippet = (abstracts.get(u["original_doi"], {}).get("abstract") or "")[:600]
            for r in u["rows"]:
                w.writerow({
                    "row_id": r["row_id"],
                    "original_title": u["original_title"],
                    "original_journal": u["original_journal"],
                    "original_year": u["original_year"],
                    "original_abstract_snippet": snippet,
                    "claim_description": r["claim_description"],
                    "dan_label": "",
                })
    print(f"wrote {common.VALIDATION_SHEET_PATH} — fill dan_label with "
          f"central / secondary / cannot_determine")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
