"""
Per-keyword yield stats, derived from the discover-stage CSVs.

For every effective search query (see keywords.API_FANOUT) this computes how many
raw candidates it discovered and how many of its papers survived each downstream
stage (filtered → classified → confirmed), plus zero-yield / not-yet-
searched detection and orphaned queries (results recorded for a query no longer
in the effective lists).

Attribution semantics — important:
  * Raw counts use FIRST-DISCOVERER CREDIT: search dedups at write time, so a
    row in candidates_raw.csv is credited to the first query that found the
    paper. A later query that re-finds it gets nothing. A zero-yield keyword
    may still be valuable if an earlier query keeps scooping its papers.
  * API↔query pairing is destroyed at dedup (deduplicate_candidates.merge_rows
    joins apis and queries into two independent strings), so downstream counts
    are per QUERY STRING. A query issued by several APIs shows the same
    downstream numbers in each API section.
  * candidates_dedup.csv is deliberately not read — raw provides discovery
    counts, the downstream files provide the rest, and skipping it halves the
    I/O (raw alone is ~265 MB).

A full pass takes tens of seconds, so results are cached to KEYWORD_STATS_JSON
keyed on input-file mtimes+sizes; the API only serves the cache and recomputes
via an explicit background refresh. Each recompute with changed inputs appends
a compact snapshot line to KEYWORD_STATS_HISTORY_JSONL for future time-series.

CLI: python -m mo_pipeline.discover.keyword_stats [--force] [--no-history] [--print]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone

from mo_pipeline.shared.jsonio import atomic_write_json
from mo_pipeline import config
from mo_pipeline.discover import keywords as kw

csv.field_size_limit(sys.maxsize)  # abstract fields can be enormous

STATS_PATH = config.KEYWORD_STATS_JSON
HISTORY_PATH = config.KEYWORD_STATS_HISTORY_JSONL

# Must match the join used by deduplicate_candidates.merge_rows for source_query.
QUERY_JOIN = " | "

DOWNSTREAM_STAGES = [
    ("filtered", config.CANDIDATES_FILTERED_CSV),
    ("classified", config.CLASSIFIED_CSV),
    ("confirmed", config.CONFIRMED_REPLICATIONS_CSV),
]

_INPUT_PATHS = {
    "candidates_raw.csv": config.CANDIDATES_RAW_CSV,
    "candidates_filtered.csv": config.CANDIDATES_FILTERED_CSV,
    "classified.csv": config.CLASSIFIED_CSV,
    "confirmed_replications.csv": config.CONFIRMED_REPLICATIONS_CSV,
    "search_progress.json": config.SEARCH_PROGRESS_FILE,
}

_LOCK = threading.Lock()


# ── fingerprinting / cache ───────────────────────────────────────────────────

def _stat_meta(path) -> dict:
    try:
        st = path.stat()
        return {"mtime": st.st_mtime, "size": st.st_size}
    except OSError:
        return {"mtime": None, "size": None}


def _fingerprint() -> dict[str, dict]:
    return {name: _stat_meta(p) for name, p in _INPUT_PATHS.items()}


def _fp_hash(inputs: dict) -> str:
    return hashlib.sha1(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def load_cached() -> dict | None:
    try:
        return json.loads(STATS_PATH.read_text())
    except Exception:
        return None


def is_stale(stats: dict) -> bool:
    return stats.get("inputs") != _fingerprint()


def write_cache(stats: dict) -> None:
    atomic_write_json(STATS_PATH, stats, indent=1)


# ── compute ──────────────────────────────────────────────────────────────────

def _day(mtime: float) -> str:
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")


def compute() -> dict:
    t0 = time.time()
    inputs = _fingerprint()  # captured before reading: conservative under concurrent writes

    kw.ensure_defaults()
    expected = kw.expected_queries()  # api -> ordered effective query list
    query_apis: Counter = Counter()  # query -> how many APIs issue it
    for qs in expected.values():
        query_apis.update(qs)
    known_queries = set(query_apis)
    expected_pairs = {(api, q) for api, qs in expected.items() for q in qs}

    try:
        progress = json.loads(config.SEARCH_PROGRESS_FILE.read_text())
    except Exception:
        progress = {}
    completed_keys = set(progress.get("completed_queries", []))

    # Pass 1: raw candidates → first-discoverer counts per (api, query).
    raw_counts: Counter = Counter()
    raw_total = 0
    if config.CANDIDATES_RAW_CSV.exists():
        with open(config.CANDIDATES_RAW_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                raw_total += 1
                raw_counts[(row.get("source_api") or "", row.get("source_query") or "")] += 1
    raw_queries = {q for (_a, q) in raw_counts}
    known_or_seen = known_queries | raw_queries

    # Pass 2: downstream files → per-query counts (source_query is pipe-merged).
    stage_counts = {name: Counter() for name, _ in DOWNSTREAM_STAGES}
    stage_totals: dict[str, int] = {}
    unknown_downstream: dict[str, dict] = {}
    for name, path in DOWNSTREAM_STAGES:
        total = 0
        unknown: Counter = Counter()
        if path.exists():
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    total += 1
                    for frag in (row.get("source_query") or "").split(QUERY_JOIN):
                        if frag in known_or_seen:
                            stage_counts[name][frag] += 1
                        elif frag:
                            unknown[frag] += 1
        stage_totals[name] = total
        if unknown:
            unknown_downstream[name] = dict(unknown.most_common(50))

    # Assemble per-API sections in fan-out order.
    apis_out = []
    tot_zero = tot_not_searched = 0
    for api, label, _keys in kw.API_FANOUT:
        rows = []
        api_raw = n_completed = n_zero = n_not = 0
        for q in expected.get(api, []):
            completed = f"{api}:{q}" in completed_keys
            raw_n = raw_counts.get((api, q), 0)
            classified_n = stage_counts["classified"].get(q, 0)
            confirmed_n = stage_counts["confirmed"].get(q, 0)
            zero = completed and raw_n == 0
            api_raw += raw_n
            n_completed += completed
            n_zero += zero
            n_not += not completed
            rows.append({
                "query": q,
                "raw": raw_n,
                "completed": completed,
                "apiCount": query_apis[q],
                "filtered": stage_counts["filtered"].get(q, 0),
                "classified": classified_n,
                "confirmed": confirmed_n,
                "confirmRate": (confirmed_n / classified_n) if classified_n else None,
                "zeroYield": zero,
                "notSearched": not completed,
            })
        rows.sort(key=lambda r: -r["raw"])
        tot_zero += n_zero
        tot_not_searched += n_not
        apis_out.append({
            "api": api, "label": label,
            "queriesExpected": len(expected.get(api, [])),
            "queriesCompleted": n_completed,
            "raw": api_raw, "zeroYield": n_zero, "notSearched": n_not,
            "queries": rows,
        })

    # Orphans: results/progress recorded for (api, query) pairs no longer effective.
    orphan_pairs: dict[tuple[str, str], int] = {}
    for (a, q), n in raw_counts.items():
        if (a, q) not in expected_pairs:
            orphan_pairs[(a, q)] = n
    for key in completed_keys:
        a, _, q = key.partition(":")  # queries may contain colons — split once
        if (a, q) not in expected_pairs:
            orphan_pairs.setdefault((a, q), raw_counts.get((a, q), 0))
    orphans = [
        {"api": a, "query": q, "raw": n, "inProgress": f"{a}:{q}" in completed_keys}
        for (a, q), n in sorted(orphan_pairs.items(), key=lambda kv: -kv[1])
    ]

    # Staleness notes.
    notes = []
    raw_m = inputs["candidates_raw.csv"]["mtime"]
    for name in ("candidates_filtered.csv", "classified.csv",
                 "confirmed_replications.csv"):
        m = inputs[name]["mtime"]
        if raw_m and m and m < raw_m:
            fmt = "%Y-%m-%d %H:%M" if _day(m) == _day(raw_m) else "%Y-%m-%d"
            notes.append(
                f"{name} ({datetime.fromtimestamp(m).strftime(fmt)}) is older than "
                f"candidates_raw.csv ({datetime.fromtimestamp(raw_m).strftime(fmt)}) — "
                f"downstream counts miss newer raw candidates")
    for q in sorted(known_queries):
        if QUERY_JOIN in q:
            notes.append(
                f"keyword contains the literal {QUERY_JOIN!r} separator and will "
                f"miscount downstream: {q[:80]}")

    return {
        "version": 2,  # 2: the "direct" stage is gone with stage 5
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "computeSeconds": round(time.time() - t0, 1),
        "inputs": inputs,
        "staleness": {"notes": notes},
        "totals": {
            "raw": raw_total,
            **stage_totals,
            "queriesExpected": sum(len(v) for v in expected.values()),
            "queriesCompleted": len(completed_keys),
            "zeroYield": tot_zero,
            "notSearched": tot_not_searched,
        },
        "apis": apis_out,
        "orphans": orphans,
        "unknownDownstream": unknown_downstream,
    }


# ── snapshot history ─────────────────────────────────────────────────────────

def _history_line(stats: dict) -> dict:
    raw: dict[str, int] = {}
    downstream: dict[str, list[int]] = {}
    for sec in stats["apis"]:
        for row in sec["queries"]:
            if row["raw"]:
                raw[f"{sec['api']}:{row['query']}"] = row["raw"]
            vals = [row["filtered"], row["classified"], row["confirmed"]]
            if any(vals):
                downstream[row["query"]] = vals  # identical across APIs — one entry
    t = stats["totals"]
    return {
        "ts": stats["generatedAt"],
        "fp": _fp_hash(stats["inputs"]),
        "totals": {k: t[k] for k in ("raw", "filtered", "classified", "confirmed")},
        "raw": raw,
        "downstream": downstream,
    }


def _last_history_fp() -> str | None:
    last = None
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        return json.loads(last).get("fp") if last else None
    except Exception:
        return None


def append_history(stats: dict) -> bool:
    """Append a compact snapshot line, skipped when inputs are unchanged."""
    line = _history_line(stats)
    if line["fp"] == _last_history_fp():
        return False
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")
    return True


def history_count() -> int:
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


# ── refresh orchestration ────────────────────────────────────────────────────

def is_computing() -> bool:
    return _LOCK.locked()


def refresh(history: bool = True) -> dict:
    """Compute + cache + (optionally) append history. Blocks if a refresh is running."""
    with _LOCK:
        stats = compute()
        write_cache(stats)
        if history:
            append_history(stats)
        return stats


def start_refresh_background() -> bool:
    """Kick off a refresh in a daemon thread. False if one is already running."""
    if not _LOCK.acquire(blocking=False):
        return False

    def _run():
        try:
            stats = compute()
            write_cache(stats)
            append_history(stats)
        finally:
            _LOCK.release()

    threading.Thread(target=_run, daemon=True, name="keyword-stats-refresh").start()
    return True


def status() -> dict:
    """Envelope served by GET /keywords/stats — never computes inline."""
    stats = load_cached()
    if is_computing():
        state = "computing"
    else:
        state = "ready" if stats else "missing"
    return {
        "state": state,
        "stale": bool(stats) and is_stale(stats),
        "stats": stats,
        "historyCount": history_count(),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def _print_summary(stats: dict) -> None:
    t = stats["totals"]
    print(f"\ngenerated {stats['generatedAt']} in {stats['computeSeconds']}s")
    print(f"{'api':<18}{'expected':>9}{'done':>6}{'raw':>9}{'zero':>6}{'unsearched':>11}")
    for sec in stats["apis"]:
        print(f"{sec['api']:<18}{sec['queriesExpected']:>9}{sec['queriesCompleted']:>6}"
              f"{sec['raw']:>9}{sec['zeroYield']:>6}{sec['notSearched']:>11}")
    print(f"\ntotals: raw {t['raw']:,} → filtered {t['filtered']:,} → classified "
          f"{t['classified']:,} → confirmed {t['confirmed']:,}")
    print(f"queries: {t['queriesCompleted']}/{t['queriesExpected']} completed, "
          f"{t['zeroYield']} zero-yield, {t['notSearched']} not searched, "
          f"{len(stats['orphans'])} orphaned")
    for n in stats["staleness"]["notes"]:
        print(f"  ! {n}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Compute per-keyword yield stats (cached)")
    ap.add_argument("--force", action="store_true", help="recompute even if the cache is fresh")
    ap.add_argument("--no-history", action="store_true", help="skip the history snapshot")
    ap.add_argument("--print", dest="do_print", action="store_true", help="print a per-API summary")
    args = ap.parse_args(argv)

    cached = load_cached()
    if cached and not is_stale(cached) and not args.force:
        print(f"cache is fresh ({STATS_PATH}, generated {cached['generatedAt']}); "
              f"use --force to recompute")
        stats = cached
    else:
        print("computing keyword yield stats (streams candidates_raw.csv — takes a while) …")
        stats = refresh(history=not args.no_history)
        print(f"wrote {STATS_PATH}")
    if args.do_print:
        _print_summary(stats)


if __name__ == "__main__":
    main()
