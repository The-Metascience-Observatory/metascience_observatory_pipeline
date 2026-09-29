#!/usr/bin/env python3
"""One-off (2026-09-02): stamp provenance onto the Feb-2026 ground truth and split
it into quarantine vs silver.

  archive/legacy_feb2026/ground_truth_enhanced_V6DERIVED.csv
      the 189-row file that scored v6/v8, with a `provenance` column: rows the
      V6 pipeline authored (absent from ground_truth_data_filtered.csv by
      (replication_url, original_title) key) are `pipeline:v6`; the rest keep
      their human/FReD provenance but get `v6_filled_cells` = number of
      statistical cells that were empty in the human file and filled from V6.
  silver/main_gt_fred_api.csv   FReD_API_team rows (external:fred_api) after fixes
  silver/main_gt_human.csv      Dan / forrt.org rows (human:*) — gold candidates
  silver/main_gt_label_flips.csv  rows whose `result` changed between the Feb-6
      backup and the Feb-19 file with no recorded rationale (needs adjudication)
  silver/main_gt_dropped.csv    rows removed by the fixes, with reasons
  silver/fred_v2_4_2.csv        FReD v2.4.2 xlsx import (external:fred_v242)
  silver/flora.csv              provenance column added in place (external:flora)

Idempotent; re-running rewrites the same outputs. Never run improve_ground_truth.py
again — the whole point of this file is that GT and pipeline output stay apart.
"""
from __future__ import annotations

import csv
import hashlib
import sys
from collections import Counter
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mo_pipeline.discover.doi_runs import normalize_doi  # noqa: E402

BENCH = Path(__file__).resolve().parents[1]
ARCH = BENCH / "archive" / "legacy_feb2026"
SILVER = BENCH / "silver"
FRED_XLSX = BENCH.parent / "fred_v2_4_2_replications_for_extraction_testing.xlsx"

STAT_FIELDS = ["original_n", "original_es", "original_es_type", "original_es_95_CI",
               "original_p_value", "original_p_value_type", "original_p_value_tails",
               "replication_n", "replication_es", "replication_es_type",
               "replication_es_95_CI", "replication_p_value", "replication_p_value_type",
               "replication_p_value_tails"]

PROVENANCE_BY_PERSON = {
    "FReD_API_team": "external:fred_api",
    "Dan Elton": "human:dan_elton",
    "forrt.org team: LK": "human:forrt_lk",
    "forrt.org team: CD": "human:forrt_cd",
}


def read(p: Path) -> list[dict]:
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write(p: Path, rows: list[dict], fields: list[str]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {p.relative_to(BENCH)} ({len(rows)} rows)")


def row_id(prefix: str, r: dict) -> str:
    key = "\x1f".join([r.get("original_url", ""), r.get("replication_url", ""),
                       r.get("description", "")])
    return f"{prefix}_{hashlib.sha1(key.encode()).hexdigest()[:10]}"


def title_key(r: dict) -> tuple[str, str]:
    return (normalize_doi(r.get("replication_url", "")) or r.get("replication_url", "").lower(),
            (r.get("original_title") or "").strip().lower())


def pair_key(r: dict) -> tuple[str, str]:
    return (normalize_doi(r.get("original_url", "")) or r.get("original_url", "").lower(),
            normalize_doi(r.get("replication_url", "")) or r.get("replication_url", "").lower())


def main() -> None:
    filtered = read(ARCH / "ground_truth_data_filtered.csv")
    backup = read(ARCH / "ground_truth_data_filtered_backup.csv")
    enhanced = read(ARCH / "ground_truth_enhanced.csv")
    print(f"filtered={len(filtered)} backup={len(backup)} enhanced={len(enhanced)}")

    # ── 1. quarantine file with provenance ──────────────────────────────────
    filt_by_title = {}
    for r in filtered:
        filt_by_title.setdefault(title_key(r), r)
    tagged = []
    n_pipeline = 0
    for r in enhanced:
        src = filt_by_title.get(title_key(r))
        out = dict(r)
        if src is None:
            out["provenance"] = "pipeline:v6"
            out["v6_filled_cells"] = ""
            n_pipeline += 1
        else:
            out["provenance"] = PROVENANCE_BY_PERSON.get(r.get("validated_person", ""), "unknown")
            out["v6_filled_cells"] = sum(
                1 for f in STAT_FIELDS
                if (r.get(f) or "").strip() and not (src.get(f) or "").strip())
        tagged.append(out)
    fields = list(enhanced[0].keys()) + ["provenance", "v6_filled_cells"]
    write(ARCH / "ground_truth_enhanced_V6DERIVED.csv", tagged, fields)
    print(f"  pipeline:v6 rows: {n_pipeline}; V6-filled cells on human rows: "
          f"{sum(int(t['v6_filled_cells'] or 0) for t in tagged)}")

    # ── 2. split the human file into silver sets, applying the B1 fixes ─────
    backup_by_pair = {}
    for r in backup:
        backup_by_pair.setdefault(pair_key(r), []).append(r)
    pair_counts = Counter(pair_key(r) for r in filtered)

    dropped, flips, fred_rows, human_rows = [], [], [], []
    seen_exact = set()
    for r in filtered:
        out = dict(r)
        o, p = pair_key(r)
        out["row_id"] = row_id("mainf", r)
        out["provenance"] = PROVENANCE_BY_PERSON.get(r.get("validated_person", ""), "unknown")
        out["fix_note"] = ""
        out["needs_adjudication"] = ""
        # self-replication row: original == replication DOI
        if o and o == p:
            dropped.append({**out, "drop_reason": "original_url == replication_url (self-replication data-entry error)"})
            continue
        exact = (o, p, (r.get("description") or "").strip().lower())
        if exact in seen_exact:
            dropped.append({**out, "drop_reason": "exact duplicate of an earlier row (same original, replication, description)"})
            continue
        seen_exact.add(exact)
        if pair_counts[(o, p)] > 1:
            out["fix_note"] = "same (original, replication) pair appears on several rows: multi-effect, not a duplicate"
        if o == "10.1037/0022-3514.71.2.230" and (r.get("original_year") or "").startswith("2013"):
            out["original_year"] = "1996"
            out["fix_note"] = (out["fix_note"] + "; " if out["fix_note"] else "") + "original_year 2013 -> 1996 (Bargh, Chen & Burrows 1996)"
        # undocumented label flips between the Feb-6 backup and the Feb-19 file
        prev = backup_by_pair.get((o, p))
        if prev and len(prev) == 1 and pair_counts[(o, p)] == 1:
            r0 = (prev[0].get("result") or "").strip().lower()
            r1 = (r.get("result") or "").strip().lower()
            out["result_feb06"] = r0
            if r0 and r1 and r0 != r1:
                out["needs_adjudication"] = "yes"
                flips.append({"row_id": out["row_id"], "replication_url": r["replication_url"],
                              "original_url": r["original_url"], "original_title": r.get("original_title", ""),
                              "result_feb06": r0, "result_feb19": r1, "provenance": out["provenance"],
                              "adjudicated_result": "", "adjudicator_id": "", "adjudication_note": ""})
        else:
            out["result_feb06"] = ""
        (fred_rows if out["provenance"] == "external:fred_api" else human_rows).append(out)

    base_fields = list(filtered[0].keys()) + ["row_id", "provenance", "result_feb06",
                                              "needs_adjudication", "fix_note"]
    write(SILVER / "main_gt_fred_api.csv", fred_rows, base_fields)
    write(SILVER / "main_gt_human.csv", human_rows, base_fields)
    write(SILVER / "main_gt_label_flips.csv", flips,
          ["row_id", "replication_url", "original_url", "original_title", "result_feb06",
           "result_feb19", "provenance", "adjudicated_result", "adjudicator_id", "adjudication_note"])
    write(SILVER / "main_gt_dropped.csv", dropped, base_fields + ["drop_reason"])
    print(f"  human provenance: {Counter(r['provenance'] for r in human_rows)}")

    # ── 3. FReD v2.4.2 xlsx -> silver ────────────────────────────────────────
    wb = openpyxl.load_workbook(FRED_XLSX, read_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    header = [str(h) for h in next(it)]
    fred = []
    for i, row in enumerate(it, start=2):
        if not any(v is not None and str(v).strip() for v in row):
            continue
        r = {h: ("" if v is None else str(v).strip()) for h, v in zip(header, row)}
        rep = normalize_doi(r.get("replication_url", ""))
        if not rep:
            continue
        r["row_id"] = f"fred_{i}"
        r["provenance"] = "external:fred_v242"
        r["replication_doi_norm"] = rep
        r["original_doi_norm"] = normalize_doi(r.get("original_url", "")) or ""
        fred.append(r)
    write(SILVER / "fred_v2_4_2.csv", fred,
          header + ["row_id", "provenance", "replication_doi_norm", "original_doi_norm"])

    # ── 4. flora.csv: add provenance in place ────────────────────────────────
    flora = read(SILVER / "flora.csv")
    for r in flora:
        r["provenance"] = "external:flora"
    ffields = list(flora[0].keys())
    if "provenance" not in ffields:
        ffields.append("provenance")
    write(SILVER / "flora.csv", flora, ffields)


if __name__ == "__main__":
    main()
