#!/usr/bin/env python3
"""
Discovery-recall harness: how many KNOWN replications does the pipeline FIND?

This is the measurement the pipeline has never had. `harness.py evaluate` scores
*extraction accuracy* on papers handed to the pipeline -- its doi runs inject
the answer key as the work list, so every metric it reports is conditional on
discovery having already succeeded. This harness asks the prior
question: of replications we know exist, how many does stage 1 search surface,
and where do the rest die?

Ground truth is external (FLoRa / FReD -- curated by other people, not by this
pipeline), which is what makes the number honest. A ground-truth set drawn from
our own production database is *circular*: it can only reveal leaks for genres
already represented, so it flatters the system. Both are reported, and the
circular one is labelled as such.

Usage:
    python benchmarking/recall_harness.py                    # FLoRa + FReD
    python benchmarking/recall_harness.py --set flora
    python benchmarking/recall_harness.py --set prod         # circular; labelled
    python benchmarking/recall_harness.py --json out.json
    python benchmarking/recall_harness.py --list-missing 30  # what search missed

Exit status is always 0; this reports, it does not gate.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
# Repo root first so `mo_pipeline` resolves however this is invoked; BENCH_DIR so
# the sibling harness modules import the same way flora_harness expects.
sys.path.insert(0, str(BENCH_DIR.parent))
sys.path.insert(1, str(BENCH_DIR))

from harness import FLORA_XLSX, FRED_XLSX, _load_xlsx as load_ground_truth_xlsx  # noqa: E402

from mo_pipeline import config  # noqa: E402
from mo_pipeline.corpus.models import normalize_doi  # noqa: E402

csv.field_size_limit(sys.maxsize)

# Rows whose `source` marks them as bulk-imported rather than discovered. Counting
# these reports ~100% coverage instead of the real ~7%, because the DB simply
# contains the ground truth by construction.
IMPORT_SOURCE_MARKERS = ("flora", "fred")

# The discovery funnel, in order. Each entry is (label, path, doi_column).
FUNNEL = [
    ("candidates_raw", config.CANDIDATES_RAW_CSV, "doi"),
    ("candidates_dedup", config.CANDIDATES_DEDUP_CSV, "doi"),
    ("candidates_filtered", config.CANDIDATES_FILTERED_CSV, "doi"),
    ("classified", config.CLASSIFIED_CSV, "doi"),
    ("confirmed", config.CONFIRMED_REPLICATIONS_CSV, "doi"),
]


def _dois_from_csv(path: Path, column: str) -> set[str] | None:
    """Normalized DOI set from `column`, or None if the file is absent."""
    if not Path(path).exists():
        return None
    out: set[str] = set()
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            d = normalize_doi(row.get(column) or "")
            if d:
                out.add(d)
    return out


def load_xlsx_ground_truth(xlsx: Path, name: str) -> dict[str, dict]:
    """{normalized replication DOI: row} from a FLoRa/FReD-schema spreadsheet."""
    if not xlsx.exists():
        print(f"  ! {name}: {xlsx.name} not found, skipping", file=sys.stderr)
        return {}
    _, rows = load_ground_truth_xlsx(xlsx)
    out = {}
    for r in rows:
        d = normalize_doi(r.get("replication_url") or "")
        if d:
            out[d] = r
    return out


def load_production_ground_truth() -> tuple[dict[str, dict], dict[str, dict]]:
    """(all rows, discovered-only rows) keyed by normalized replication DOI.

    Splitting these matters: rows whose `source` names FLoRa/FReD arrived by bulk
    import, so counting them measures the import, not the pipeline.
    """
    latest = _latest_production_csv()
    if latest is None:
        return {}, {}
    allrows: dict[str, dict] = {}
    discovered: dict[str, dict] = {}
    with open(latest, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            d = normalize_doi(r.get("replication_url") or "")
            if not d:
                continue
            allrows[d] = r
            src = (r.get("source") or "").lower()
            if not any(m in src for m in IMPORT_SOURCE_MARKERS):
                discovered[d] = r
    return allrows, discovered


def _latest_production_csv() -> Path | None:
    """Newest replications_database_*.csv in the website data dir."""
    files = sorted(Path(config.WEBSITE_DATA_DIR).glob("replications_database_*.csv"))
    return files[-1] if files else None


def _provenance() -> dict:
    """What state of the discovery stages produced this baseline."""
    import hashlib
    import subprocess
    import time
    def run(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                                  cwd=config.REPO_ROOT).stdout.strip()
        except Exception:
            return ""
    kw = config.DATA_DIR / "keywords.json"
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "git_commit": run(["git", "rev-parse", "HEAD"]),
        "git_dirty_discover": bool(run(["git", "status", "--porcelain", "mo_pipeline/discover/"])),
        "keywords_json_sha256": hashlib.sha256(kw.read_bytes()).hexdigest() if kw.exists() else "",
        "stage_files": {name: {"path": str(path), "mtime": (Path(path).stat().st_mtime if Path(path).exists() else None)}
                        for name, path, _ in FUNNEL},
    }


def measure(truth: dict[str, dict], label: str, circular: bool) -> dict:
    """Walk `truth` through the funnel, recording presence and per-stage loss."""
    total = len(truth)
    stages = []
    prev = set(truth)
    for name, path, col in FUNNEL:
        present = _dois_from_csv(path, col)
        if present is None:
            stages.append({"stage": name, "present": None, "missing_file": str(path)})
            continue
        here = set(truth) & present
        stages.append({
            "stage": name,
            "present": len(here),
            "pct": round(100 * len(here) / total, 1) if total else 0.0,
            "lost_here": len(prev) - len(here),
            "mtime": Path(path).stat().st_mtime,
        })
        prev = here
    raw_present = _dois_from_csv(FUNNEL[0][1], FUNNEL[0][2]) or set()
    never_found = sorted(set(truth) - raw_present)
    return {
        "label": label,
        "circular": circular,
        "total": total,
        "stages": stages,
        "never_found": never_found,
        "provenance": _provenance(),
    }


def compare(old_path: Path, new_path: Path) -> str:
    """Per-stage deltas between two baseline JSONs (matched by label)."""
    old = {r["label"]: r for r in json.loads(Path(old_path).read_text())}
    new = {r["label"]: r for r in json.loads(Path(new_path).read_text())}
    L = [f"\nBaseline comparison: {Path(old_path).name} -> {Path(new_path).name}"]
    for label, n in new.items():
        o = old.get(label)
        if not o:
            L.append(f"\n=== {label}: (not in old baseline)")
            continue
        L.append(f"\n=== {label} (n {o['total']} -> {n['total']})")
        L.append(f"    {'stage':22} {'old':>7} {'new':>7} {'delta':>8}")
        os_ = {s["stage"]: s for s in o["stages"]}
        for s in n["stages"]:
            a = os_.get(s["stage"], {}).get("pct")
            b = s.get("pct")
            d = f"{b - a:+.1f}pp" if a is not None and b is not None else "n/a"
            L.append(f"    {s['stage']:22} {a if a is not None else 'n/a':>7} {b if b is not None else 'n/a':>7} {d:>8}")
    return "\n".join(L)


def profile_missing(truth: dict[str, dict], dois: list[str], fields=("discipline",)) -> dict:
    """Counter per field over the never-found rows -- shows WHERE recall fails."""
    out = {}
    for f in fields:
        c = Counter()
        for d in dois:
            row = truth.get(d) or {}
            v = str(row.get(f) or "?").strip()[:44]
            if v:
                c[v] += 1
        if c:
            out[f] = c.most_common(12)
    return out


def render(result: dict, truth: dict, list_missing: int) -> str:
    L = []
    tag = "  [CIRCULAR -- flatters the system]" if result["circular"] else "  [external]"
    L.append(f"\n=== {result['label']} (n={result['total']}){tag}")
    if not result["total"]:
        L.append("    (no ground truth loaded)")
        return "\n".join(L)
    L.append(f"    {'stage':22} {'present':>9} {'pct':>7} {'lost':>7}")
    for s in result["stages"]:
        if s.get("present") is None:
            L.append(f"    {s['stage']:22} {'(missing file)':>9}")
            continue
        L.append(f"    {s['stage']:22} {s['present']:>9,} {s['pct']:>6.1f}% {s['lost_here']:>7,}")
    nf = result["never_found"]
    L.append(f"\n    never found by search: {len(nf):,} ({100*len(nf)/result['total']:.1f}%)")
    prof = profile_missing(truth, nf, ("discipline", "replication_journal"))
    for field, items in prof.items():
        L.append(f"    by {field}:")
        for k, v in items:
            L.append(f"      {v:>5}  {k}")
    if list_missing:
        L.append("\n    sample of never-found titles:")
        for d in nf[:list_missing]:
            t = str((truth.get(d) or {}).get("replication_title") or "")[:82]
            L.append(f"      - {t}")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", choices=["flora", "fred", "prod", "all"], default="all")
    ap.add_argument("--json", type=Path, help="write full results as JSON")
    ap.add_argument("--list-missing", type=int, default=0,
                    help="print N never-found titles per set")
    ap.add_argument("--compare", nargs=2, metavar=("OLD_JSON", "NEW_JSON"),
                    help="print per-stage deltas between two saved baselines and exit")
    args = ap.parse_args()

    if args.compare:
        print(compare(Path(args.compare[0]), Path(args.compare[1])))
        return

    sets = []
    if args.set in ("flora", "all"):
        t = load_xlsx_ground_truth(FLORA_XLSX, "FLoRa")
        if t:
            sets.append((measure(t, "FLoRa (external)", False), t))
    if args.set in ("fred", "all"):
        t = load_xlsx_ground_truth(FRED_XLSX, "FReD")
        if t:
            sets.append((measure(t, "FReD (external)", False), t))
    if args.set in ("prod", "all"):
        allrows, discovered = load_production_ground_truth()
        if allrows:
            sets.append((measure(allrows, "production DB -- all rows", True), allrows))
            sets.append((measure(discovered, "production DB -- excluding bulk imports",
                                 True), discovered))

    print("Discovery-recall harness")
    src = _latest_production_csv()
    print(f"  production DB: {src.name if src else '(none)'}")
    for result, truth in sets:
        print(render(result, truth, args.list_missing))

    print("\n  Note: external (FLoRa/FReD) is the honest number. A ground-truth set drawn")
    print("  from our own DB can only find leaks for genres already represented.")

    if args.json:
        args.json.write_text(json.dumps([r for r, _ in sets], indent=2))
        print(f"\n  wrote {args.json}")


if __name__ == "__main__":
    main()
