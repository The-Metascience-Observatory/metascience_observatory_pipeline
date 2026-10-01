"""Original-study identification: does the extractor name the study a paper replicates?

Scored per paper, as sets, and independently of row matching. `evaluate` pairs
extracted rows with ground-truth rows partly BY original DOI, so the original-DOI
accuracy it reports on matched pairs is high by construction. Here each paper's
human-labelled original DOIs (FLoRa, FReD, the Feb-2026 human rows) are compared
with every original DOI the extraction names, through the same canonical-DOI /
alias table the matcher uses.

Per paper:
  recall     |human ∩ extracted| / |human|
  precision  |human ∩ extracted| / |extracted|   -- a LOWER bound: the human lists
             are not exhaustive (FLoRa records one row per paper), so an extra
             original may be correct and simply unlisted
  hit        at least one human original named
  exact      the two sets are equal

A paper the extractor found no replications in scores 0 recall (a miss, reported
separately as `extracted_none`). Papers that are not converted or not extracted
are counted in the funnel and excluded from the rates.

  python benchmarking/harness.py originals --tags luna_orig_pilot [--run SLUG]
  python benchmarking/harness.py originals-select --n 50 --slug luna_orig_pilot
"""
from __future__ import annotations

import json
import random
import time
from collections import Counter, defaultdict
from pathlib import Path

import harness as h
import matching

GT_SETS = ("flora", "fred_v242", "main_gt_fred_api", "main_gt_human")


def _canon(doi: str) -> str:
    """matching.canonical_doi, plus APA's legacy double slash (10.1037//0022-...),
    which is the same registered DOI written two ways."""
    d = matching.canonical_doi(doi or "")
    if d.startswith("10.") and "//" in d:
        prefix, _, rest = d.partition("/")
        d = matching.canonical_doi(prefix + "/" + rest.lstrip("/"))
    return d


def _near(a: str, b: str) -> bool:
    """Same DOI up to a truncated tail (".x", a dropped final character): the right
    paper named with a broken DOI. Scored separately, never as a strict hit."""
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 12 and long_.startswith(short) and len(long_) - len(short) <= 3


def human_originals(sets=GT_SETS) -> dict[str, dict]:
    """{paper_folder: {doi, originals:set, sets:set, no_doi_rows:int}} over the sets."""
    papers: dict[str, dict] = {}
    for name in sets:
        rows, _, _ = h.load_ground_truth(f"silver:{name}", allow_paper_level=True)
        for r in rows:
            folder = r.get("paper_folder")
            if not folder:
                continue
            p = papers.setdefault(folder, {"doi": r["replication_doi"], "originals": set(),
                                           "sets": set(), "no_doi_rows": 0})
            p["sets"].add(name)
            orig = _canon(r.get("original_doi") or "")
            if orig:
                p["originals"].add(orig)
            else:
                p["no_doi_rows"] += 1
    return papers


def extracted_originals(rows: list[dict]) -> tuple[set, int]:
    """(canonical original DOIs named by the extraction, rows naming no DOI)."""
    found, missing = set(), 0
    for r in rows:
        d = _canon(matching.doi_of(r, "original_url", "original_doi"))
        if d:
            found.add(d)
        else:
            missing += 1
    return found, missing


def score_paper(gt: set, ext: set) -> dict:
    tp = len(gt & ext)
    near = tp > 0 or any(_near(g, e) for g in gt for e in ext)
    return {"n_gt": len(gt), "n_ext": len(ext), "tp": tp,
            "recall": tp / len(gt) if gt else None,
            "precision_lb": tp / len(ext) if ext else None,
            "hit": tp > 0, "hit_lenient": near, "exact": gt == ext}


def summarize(recs: list[dict]) -> dict:
    scored = [r for r in recs if r["n_gt"]]
    if not scored:
        return {"n_papers": 0}
    tp = sum(r["tp"] for r in scored)
    n_gt = sum(r["n_gt"] for r in scored)
    n_ext = sum(r["n_ext"] for r in scored)
    with_ext = [r for r in scored if r["n_ext"]]
    return {
        "n_papers": len(scored),
        "micro_recall": tp / n_gt,
        "micro_precision_lb": tp / n_ext if n_ext else None,
        "macro_recall": sum(r["recall"] for r in scored) / len(scored),
        "hit_rate": sum(r["hit"] for r in scored) / len(scored),
        "hit_rate_lenient": sum(r["hit_lenient"] for r in scored) / len(scored),
        "exact_rate": sum(r["exact"] for r in scored) / len(scored),
        "hit_rate_when_extracted": (sum(r["hit"] for r in with_ext) / len(with_ext)) if with_ext else None,
        "extracted_none": len(scored) - len(with_ext),
        "ext_rows_without_doi": sum(r.get("ext_rows_without_doi", 0) for r in scored),
    }


def bootstrap(recs: list[dict], n_boot: int = 2000, seed: int = h.SEED_DEFAULT) -> dict:
    """Paper-cluster percentile CIs (95%) for each rate in summarize()."""
    scored = [r for r in recs if r["n_gt"]]
    if len(scored) < 2 or not n_boot:
        return {}
    rng = random.Random(seed)
    keys = ("micro_recall", "micro_precision_lb", "macro_recall", "hit_rate", "exact_rate")
    draws = defaultdict(list)
    for _ in range(n_boot):
        s = summarize([rng.choice(scored) for _ in scored])
        for k in keys:
            if s.get(k) is not None:
                draws[k].append(s[k])
    out = {}
    for k, v in draws.items():
        v.sort()
        out[k] = [v[int(0.025 * (len(v) - 1))], v[int(0.975 * (len(v) - 1))]]
    return out


def cmd_originals(args) -> None:
    tags = [t for t in args.tags.split(",") if t]
    papers_dir = Path(args.papers_dir) if args.papers_dir else h.config.PAPERS_DIR
    sets = [s for s in args.sets.split(",") if s]
    gt = human_originals(sets)
    if args.run:
        from mo_pipeline.corpus.models import doi_to_folder
        run_folders = {doi_to_folder(d) for d in h.load_dois(args.run)}
        gt = {f: p for f, p in gt.items() if f in run_folders}
    funnel, recs, models, versions = Counter(), [], Counter(), Counter()
    for folder, p in sorted(gt.items()):
        if not p["originals"]:
            funnel["gt_without_original_doi"] += 1
            continue
        ext = h.load_extraction(folder, tags, papers_dir)
        funnel[ext["status"]] += 1
        if ext["status"] != "ok":
            continue
        models[ext.get("model") or "?"] += 1
        versions[ext.get("ai_version") or "?"] += 1
        found, missing = extracted_originals(ext["rows"])
        rec = {"paper_folder": folder, "replication_doi": p["doi"], "sets": ";".join(sorted(p["sets"])),
               "gt_originals": ";".join(sorted(p["originals"])), "ext_originals": ";".join(sorted(found)),
               "ext_rows_without_doi": missing, "tag": ext.get("tag", ""), **score_paper(p["originals"], found)}
        recs.append(rec)
    overall = summarize(recs)
    by_set = {s: summarize([r for r in recs if s in r["sets"].split(";")]) for s in sets}
    cis = bootstrap(recs, args.bootstrap)
    prov = {"harness_version": h.HARNESS_VERSION, "tags": tags, "gt_sets": sets, "run": args.run,
            "models": dict(models), "ai_versions": dict(versions), "git_commit": h.git("rev-parse", "HEAD"),
            "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    out = Path(args.out_dir) if args.out_dir else \
        h.RESULTS_DIR / f"{time.strftime('%Y-%m-%d')}_originals_{'-'.join(tags)}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "metrics.json").write_text(json.dumps(
        {"overall": overall, "ci95": cis, "by_set": by_set, "funnel": dict(funnel), "provenance": prov},
        indent=2))
    h.write_csv(out / "per_paper.csv", recs)
    (out / "report.md").write_text(render(overall, cis, by_set, funnel, prov))
    print(render(overall, cis, by_set, funnel, prov))
    print(f"\nwrote {out}")


def _pct(v):
    return "n/a" if v is None else f"{100 * v:.1f}%"


def render(overall, cis, by_set, funnel, prov) -> str:
    def ci(k):
        return f" [{_pct(cis[k][0])}, {_pct(cis[k][1])}]" if k in cis else ""
    L = [f"# Original-study identification — tags {','.join(prov['tags'])}", "",
         f"Models: {prov['models']}  ·  ai_version: {prov['ai_versions']}  ·  harness {prov['harness_version']}", "",
         f"Funnel: {dict(funnel)}", ""]
    if not overall.get("n_papers"):
        return "\n".join(L + ["No scorable papers."])
    L += [f"Scored papers: {overall['n_papers']} (extractor found no replications in {overall['extracted_none']})", "",
          "| Measure | Value (95% CI, paper bootstrap) |", "|---|---|",
          f"| Hit rate (≥1 human original named) | {_pct(overall['hit_rate'])}{ci('hit_rate')} |",
          f"| Hit rate, lenient (truncated DOI tail accepted) | {_pct(overall['hit_rate_lenient'])} |",
          f"| Recall of human originals (micro) | {_pct(overall['micro_recall'])}{ci('micro_recall')} |",
          f"| Recall (macro, per paper) | {_pct(overall['macro_recall'])}{ci('macro_recall')} |",
          f"| Precision, lower bound (micro) | {_pct(overall['micro_precision_lb'])}{ci('micro_precision_lb')} |",
          f"| Exact set match | {_pct(overall['exact_rate'])}{ci('exact_rate')} |",
          f"| Hit rate where something was extracted | {_pct(overall['hit_rate_when_extracted'])} |",
          f"| Extracted rows with no original DOI | {overall['ext_rows_without_doi']} |", "",
          "Precision is a lower bound: the human lists are not exhaustive.", "",
          "| Set | Papers | Hit | Recall (micro) | Precision LB |", "|---|---|---|---|---|"]
    for s, m in by_set.items():
        if m.get("n_papers"):
            L.append(f"| {s} | {m['n_papers']} | {_pct(m['hit_rate'])} | {_pct(m['micro_recall'])} | "
                     f"{_pct(m['micro_precision_lb'])} |")
    return "\n".join(L) + "\n"


def cmd_select(args) -> None:
    """Seeded, set-stratified sample of GT papers with full text on the drive -> a doi run."""
    from mo_pipeline.discover import doi_runs
    gt = human_originals([s for s in args.sets.split(",") if s])
    pool = defaultdict(list)
    for folder, p in sorted(gt.items()):
        d = h.paper_dir_for(folder)
        if p["originals"] and d is not None and h._has_fulltext(d):
            pool[sorted(p["sets"])[0]].append(p["doi"])
    exclude = set()
    for slug in filter(None, (args.exclude_runs or "").split(",")):
        exclude |= {x.lower() for x in h.load_dois(slug)}
    rng = random.Random(args.seed)
    total = sum(len(v) for v in pool.values())
    picked = []
    for s, dois in sorted(pool.items()):
        dois = [d for d in dois if d.lower() not in exclude]
        k = min(len(dois), round(args.n * len(pool[s]) / total)) if args.n else len(dois)
        picked += rng.sample(dois, k)
    print(f"pool by set: { {s: len(v) for s, v in pool.items()} }; picked {len(picked)}")
    if args.dry_run:
        return
    meta = doi_runs.create_run(args.slug, csv_text="doi\n" + "\n".join(picked) + "\n")
    print(f"created doi run {meta['slug']} ({meta['n_dois']} DOIs) -> extract with "
          f"`harness.py run --run {meta['slug']} --tag {meta['slug']} --level base --usecodex --model gpt-5.6-luna`")
