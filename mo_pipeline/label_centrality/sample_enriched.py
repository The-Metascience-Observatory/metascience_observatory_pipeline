"""Enriched sample: ALL rows from high-multiplicity original papers.

The pilot's stratified sample landed only ~1 row per paper, so it could not
measure within-paper structure (does a multi-row paper hold multiple central
claims, or a central-plus-secondary mix?). This sampler instead selects a
handful of original papers that contribute MANY rows and takes *every* eligible
row from each, so labeling reveals the per-paper central/secondary breakdown.

Papers are drawn deterministically, stratified across multiplicity bands
(4-5 rows, 6-9, 10+) so structure is observed at different multiplicities.
Only rows whose replication paper has a usable corpus folder are eligible.

Outputs (DATA_DIR/label_centrality/, run="enriched"):
  enriched_manifest.json  — blinded work units (same schema as the pilot)
  enriched_rows_UNBLINDED_do_not_show_labelers.csv

Usage:
    python -m mo_pipeline.label_centrality.sample_enriched [--target-papers 30]
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter, defaultdict

from mo_pipeline.label_centrality import common
from mo_pipeline.label_centrality.sample_pilot import folder_usable, source_bucket

SEED = 20260714
BANDS = [(4, 5), (6, 9), (10, 10**9)]  # (min_rows, max_rows) inclusive


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target-papers", type=int, default=30,
                    help="~total papers, split evenly across the 3 bands")
    ap.add_argument("--max-rows", type=int, default=260,
                    help="hard cap on total rows (drops largest papers first)")
    args = ap.parse_args()

    rng = random.Random(SEED)
    _, rows = common.load_db_rows()
    catalog = common.load_catalog_folders()

    # eligible rows grouped by original paper
    by_orig: dict[str, list] = defaultdict(list)
    for r in rows:
        doi = common.normalize_doi(r["replication_url"])
        if not doi or doi not in catalog:
            continue
        if not r["original_url"] or not r["description"].strip():
            continue
        r["_folder"] = catalog[doi]
        r["_rep_doi"] = doi
        by_orig[r["original_url"]].append(r)

    # keep only papers whose every sampled row has a usable folder
    usable_cache: dict = {}
    def usable(folder):
        if folder not in usable_cache:
            usable_cache[folder] = folder_usable(folder)
        return usable_cache[folder]

    eligible_papers = {}
    for orig, rs in by_orig.items():
        if all(usable(r["_folder"]) for r in rs):
            eligible_papers[orig] = rs

    per_band = max(1, args.target_papers // len(BANDS))
    picked_papers = []
    for lo, hi in BANDS:
        pool = sorted((o for o, rs in eligible_papers.items() if lo <= len(rs) <= hi))
        picked_papers.extend(rng.sample(pool, min(per_band, len(pool))))

    # enforce row cap: drop the largest papers until under cap
    picked_papers.sort(key=lambda o: len(eligible_papers[o]))
    kept, total = [], 0
    for o in picked_papers:
        n = len(eligible_papers[o])
        if total + n > args.max_rows:
            continue
        kept.append(o)
        total += n

    # build work units grouped by replication paper (a paper may have >1 rep DOI)
    units_by_rep = defaultdict(list)
    for orig in kept:
        for r in eligible_papers[orig]:
            units_by_rep[r["_rep_doi"]].append(r)

    manifest = {
        "csv_version": common.latest_csv_path().name,
        "sampled_at_note": f"enriched seed={SEED}, target_papers={args.target_papers}, "
                           f"{len(kept)} papers, {total} rows",
        "units": [
            {
                "replication_doi": doi,
                "folder": str(rs[0]["_folder"]),
                "original_doi": common.normalize_doi(rs[0]["original_url"]) or "",
                "original_title": rs[0]["original_title"],
                "original_journal": rs[0]["original_journal"],
                "original_year": rs[0]["original_year"],
                "rows": [
                    {"row_id": r["row_id"], "claim_description": r["description"]}
                    for r in sorted(rs, key=lambda r: r["row_id"])
                ],
            }
            for doi, rs in sorted(units_by_rep.items())
        ],
    }
    common.assert_manifest_blinded(manifest)

    paths = common.Paths("enriched")
    common.OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    fields = ["row_id", "original_url", "replication_url", "description", "result",
              "replication_type", "original_title", "original_year",
              "validated_person", "source"]
    with open(paths.rows_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields + ["source_bucket", "n_rows_this_paper"],
                           extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for orig in kept:
            for r in sorted(eligible_papers[orig], key=lambda r: r["row_id"]):
                w.writerow({**{k: r.get(k, "") for k in fields},
                            "source_bucket": source_bucket(r),
                            "n_rows_this_paper": len(eligible_papers[orig])})

    band_counts = Counter()
    for orig in kept:
        n = len(eligible_papers[orig])
        band = next(f"{lo}-{hi if hi < 1000 else '+'}" for lo, hi in BANDS if lo <= n <= hi)
        band_counts[band] += 1
    print(f"eligible papers (all rows folder-backed): {len(eligible_papers)}")
    print(f"selected {len(kept)} papers, {total} rows")
    print("papers per band:", dict(band_counts))
    print(f"wrote {paths.manifest} ({len(manifest['units'])} work units)")
    print(f"wrote {paths.rows_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
