"""Draw the deterministic pilot sample for centrality labeling.

Samples 150 rows from the current replications database, restricted to rows
whose replication paper has a corpus folder with usable fulltext (body.md or
a PDF): ~100 rows from multi-row original papers (where the central/secondary
distinction does real work) and ~50 from single-row originals, stratified
across data sources within each group.

Outputs (DATA_DIR/label_centrality/):
  pilot_manifest.json  — BLINDED work units grouped by replication paper:
                         original metadata + claim descriptions only. A key
                         whitelist assertion guarantees no result/effect-size
                         fields can leak in.
  pilot_rows_UNBLINDED_do_not_show_labelers.csv — the full sampled rows,
                         used only by compute_agreement.py at analysis time.

Usage:
    python -m mo_pipeline.label_centrality.sample_pilot [--n-multi 100] [--n-single 50]
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter, defaultdict

from mo_pipeline.label_centrality import common
from mo_pipeline.corpus.models import normalize_doi

SEED = 20260713
BODY_OR_PDF = ("body.md",)  # body.md preferred; else any *.pdf


def source_bucket(row: dict) -> str:
    vp = (row.get("validated_person") or "").lower()
    src = (row.get("source") or "").lower()
    if "fred" in vp:
        return "fred"
    if "curate science" in vp or "curate science" in src:
        return "curate_science"
    if "replication wiki" in vp:
        return "repwiki_ai"
    if "score" in src:
        return "darpa_score"
    if vp.strip():
        return "manual_other"
    if (row.get("ai_version") or "").strip():
        return "ai_pipeline"
    return "unattributed"


def folder_usable(folder) -> bool:
    if not folder.is_dir():
        return False
    names = [p.name for p in folder.iterdir()]
    return "body.md" in names or any(n.lower().endswith(".pdf") for n in names)


def stratified_pick(rows: list[dict], n: int, rng: random.Random) -> list[dict]:
    """Proportional-by-source sample of n rows (deterministic)."""
    by_bucket = defaultdict(list)
    for r in rows:
        by_bucket[source_bucket(r)].append(r)
    total = len(rows)
    picked = []
    buckets = sorted(by_bucket)
    for b in buckets:
        pool = sorted(by_bucket[b], key=lambda r: r["row_id"])
        quota = max(1, round(n * len(pool) / total)) if pool else 0
        picked.extend(rng.sample(pool, min(quota, len(pool))))
    # trim/extend to exactly n
    rng.shuffle(picked)
    if len(picked) > n:
        picked = picked[:n]
    else:
        remaining = sorted(
            (r for r in rows if r not in picked), key=lambda r: r["row_id"])
        picked.extend(rng.sample(remaining, min(n - len(picked), len(remaining))))
    return picked


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-multi", type=int, default=100)
    ap.add_argument("--n-single", type=int, default=50)
    args = ap.parse_args()

    rng = random.Random(SEED)
    csv_version, rows = common.load_db_rows()
    catalog = common.load_catalog_folders()

    paper_counts = Counter(r["original_url"] for r in rows if r["original_url"])

    eligible = []
    skipped_no_folder = 0
    for r in rows:
        doi = normalize_doi(r["replication_url"])
        if not doi or doi not in catalog:
            skipped_no_folder += 1
            continue
        if not r["original_url"] or not r["description"].strip():
            continue
        r["_folder"] = catalog[doi]
        r["_rep_doi"] = doi
        eligible.append(r)

    multi = [r for r in eligible if paper_counts[r["original_url"]] > 1]
    single = [r for r in eligible if paper_counts[r["original_url"]] == 1]
    print(f"db rows: {len(rows)} | eligible (folder + original + description): "
          f"{len(eligible)} | multi-row-paper pool: {len(multi)} | single: {len(single)}")

    picked = stratified_pick(multi, args.n_multi, rng) + stratified_pick(single, args.n_single, rng)

    # Drop rows whose folder lacks usable fulltext; replace deterministically.
    usable_cache: dict = {}
    def usable(r):
        f = r["_folder"]
        if f not in usable_cache:
            usable_cache[f] = folder_usable(f)
        return usable_cache[f]

    kept = [r for r in picked if usable(r)]
    dropped = len(picked) - len(kept)
    if dropped:
        picked_ids = {r["row_id"] for r in picked}
        spare = sorted((r for r in eligible if r["row_id"] not in picked_ids),
                       key=lambda r: r["row_id"])
        rng.shuffle(spare)
        for r in spare:
            if len(kept) >= args.n_multi + args.n_single:
                break
            if usable(r):
                kept.append(r)
    print(f"sampled {len(kept)} rows ({dropped} replaced for unusable folders)")

    # Group into work units by replication paper.
    units = defaultdict(list)
    for r in kept:
        units[r["_rep_doi"]].append(r)

    manifest = {
        "csv_version": csv_version,
        "sampled_at_note": f"seed={SEED}, n_multi={args.n_multi}, n_single={args.n_single}",
        "units": [
            {
                "replication_doi": doi,
                "folder": str(unit_rows[0]["_folder"]),
                "original_doi": normalize_doi(unit_rows[0]["original_url"]) or "",
                "original_title": unit_rows[0]["original_title"],
                "original_journal": unit_rows[0]["original_journal"],
                "original_year": unit_rows[0]["original_year"],
                "rows": [
                    {"row_id": r["row_id"], "claim_description": r["description"]}
                    for r in sorted(unit_rows, key=lambda r: r["row_id"])
                ],
            }
            for doi, unit_rows in sorted(units.items())
        ],
    }
    common.assert_manifest_blinded(manifest)

    common.OUT_DIR.mkdir(parents=True, exist_ok=True)
    common.MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    unblinded_fields = ["row_id", "original_url", "replication_url", "description",
                        "result", "replication_type", "original_title",
                        "original_year", "validated_person", "source"]
    with open(common.ROWS_CSV_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=unblinded_fields + ["source_bucket", "multi_row_paper"],
                           extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in sorted(kept, key=lambda r: r["row_id"]):
            w.writerow({**{k: r.get(k, "") for k in unblinded_fields},
                        "source_bucket": source_bucket(r),
                        "multi_row_paper": int(paper_counts[r["original_url"]] > 1)})

    n_units = len(manifest["units"])
    print(f"wrote {common.MANIFEST_PATH} ({n_units} work units, {len(kept)} rows)")
    print(f"wrote {common.ROWS_CSV_PATH}")
    print("strata:", Counter(source_bucket(r) for r in kept).most_common())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
