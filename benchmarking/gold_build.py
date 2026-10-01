"""Gold-set toolchain for the extraction benchmark: everything that BUILDS
ground truth, as opposed to scoring against it (harness.py).

  import-flora / import-fred   xlsx -> silver csv
  sample                       stratified sampling frame (Phase 2b)
  coding-sheet / agreement / adjudicate / build-gold   double-coded gold set
  match-audit                  spot-check the matcher's decisions

Run through the harness CLI (`python benchmarking/harness.py <cmd>`). Paths the
tests redirect (BENCH_DIR, CODING_DIR, GOLD_DIR, SILVER_DIR) are read through
`h.` at call time, so patching them on the harness module still takes effect.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import harness as h
import matching
from harness import (CODEBOOK, CODED_FIELDS, SEED_DEFAULT, SHEET_ALLOWED, STAT_FIELDS,
                     TYPE_COLLAPSE, TYPE_VALUES, discipline_group, norm_label, paper_dir_for,
                     read_csv, sha256_file, split_of, to_float, write_csv)
from matching import doi_of, string_ratio
from mo_pipeline import config
from mo_pipeline.corpus.models import doi_to_folder, normalize_doi
from mo_pipeline.discover.doi_runs import create_run, delete_run
from mo_pipeline.label_centrality.collate import cohen_kappa


# ── silver importers ─────────────────────────────────────────────────────────
def _load_xlsx(xlsx: Path) -> tuple[list[str], list[dict]]:
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    header = [str(h) for h in next(it)]
    rows = []
    for r in it:
        if any(v is not None and str(v).strip() for v in r):
            rows.append({h: ("" if v is None else str(v).strip()) for h, v in zip(header, r)})
    return header, rows


def _import_xlsx(xlsx: Path, out: Path, prefix: str, provenance: str) -> None:
    header, raw = _load_xlsx(xlsx)
    kept, skipped, seen = [], [], set()
    for i, row in enumerate(raw, start=2):
        rep = normalize_doi(row.get("replication_url", ""))
        orig = normalize_doi(row.get("original_url", ""))
        if rep is None:
            skipped.append({**row, "skip_reason": "replication_url is not a DOI", "xlsx_line": i}); continue
        pair = (orig or row.get("original_url", "").lower(), rep, norm_label(row.get("description")))
        if pair in seen:
            skipped.append({**row, "skip_reason": "duplicate (original, replication, description)", "xlsx_line": i}); continue
        seen.add(pair)
        kept.append({**row, "row_id": f"{prefix}_{i}", "replication_doi_norm": rep, "original_doi_norm": orig or "",
                     "provenance": provenance})
    write_csv(out, kept, header + ["row_id", "replication_doi_norm", "original_doi_norm", "provenance"])
    if skipped:
        write_csv(out.with_name(out.stem + "_skipped.csv"), skipped, header + ["skip_reason", "xlsx_line"])
    print(f"{xlsx.name}: kept {len(kept)}, skipped {len(skipped)} -> {out}")


def cmd_import_flora(args) -> None:
    _import_xlsx(Path(args.xlsx), h.SILVER_DIR / "flora.csv", "flora", "external:flora")


def cmd_import_fred(args) -> None:
    _import_xlsx(Path(args.xlsx), h.SILVER_DIR / "fred_v2_4_2.csv", "fred", "external:fred_v242")


# ── sampling frame (Phase 2b) ────────────────────────────────────────────────
def _catalog() -> dict[str, dict]:
    from mo_pipeline.corpus import catalog   # connect() sets busy_timeout: a scan may be writing
    con = catalog.connect()
    try:
        return {r["doi"].lower(): dict(r) for r in con.execute("select * from papers")}
    finally:
        con.close()


def _latest_db_rows() -> list[dict]:
    from mo_pipeline.label_centrality.common import latest_csv_path
    rows = read_csv(latest_csv_path())
    for r in rows:
        r["_rep"] = normalize_doi(r.get("replication_url", "")) or ""
    return rows


def _paper_mode(rows: list[dict], key: str) -> str:
    c = Counter(norm_label(r.get(key)) for r in rows if norm_label(r.get(key)))
    return c.most_common(1)[0][0] if c else ""


def cmd_sample(args) -> None:
    rng = random.Random(args.seed)
    cat = _catalog()
    def converted(doi: str) -> bool:
        c = cat.get(doi)
        return bool(c) and c.get("status") in ("converted", "screened", "extracted", "ingested")

    def on_disk(doi: str) -> bool:
        """Is the paper actually readable right now? The catalog is an index, not
        truth, so this asks the filesystem. The v1 frame sampled 45 FLoRa papers
        of which 38 never downloaded (that pull landed 24%), sending coders at
        folders that do not exist while 73 other FLoRa papers sat on the drive.
        """
        d = config.PAPERS_DIR / doi_to_folder(doi)
        return d.is_dir() and (any(d.glob("*_from_xml.md")) or any(d.glob("*_from_html.md"))
                               or (d / "body.md").exists() or any(d.glob("*.pdf"))
                               or any(d.glob("*.xml")))
    frame: list[dict] = []
    taken: set[str] = set()

    # Loaded before the first stratum, not just for the DB-derived ones: it is also
    # the discipline of last resort. The main_gt_human stratum reads its group from
    # `openalex_field` in the silver CSV, which is empty for 11 of its 27 papers,
    # while the production DB records a discipline for every row it has.
    try:
        db = _latest_db_rows()
    except Exception as exc:
        print(f"WARNING: production DB unavailable ({exc}); skipping DB-derived strata")
        db = []
    dbp = defaultdict(list)
    for r in db:
        if r["_rep"]:
            dbp[r["_rep"]].append(r)

    def _discipline(doi: str, expected: dict | None) -> str:
        # "unknown" counts as missing: discipline_group() returns that string, not
        # "", when its input is empty, so testing truthiness alone never falls back.
        g = (expected or {}).get("discipline_group", "")
        if g in ("", "unknown") and dbp.get(doi):
            g = discipline_group(_paper_mode(dbp[doi], "discipline")) or g
        return g

    def _fine_discipline(doi: str) -> tuple[str, str]:
        """The production DB's own `discipline` / `subdiscipline` for this paper.

        The five-way `discipline_group` is for stratifying results; the frame should
        also carry the vocabulary the database and the website actually use, so a
        breakdown can be read at the resolution the ontology defines. Negative papers
        are not in a replications database at all, so they come back blank here and
        are filled by `enrich_frame_disciplines`.
        """
        hits = dbp.get(doi)
        if not hits:
            return "", ""
        return _paper_mode(hits, "discipline"), _paper_mode(hits, "subdiscipline")

    def add(doi: str, source: str, stratum: str, expected: dict | None = None, note: str = ""):
        if not doi or doi in taken:
            return False
        taken.add(doi)
        frame.append({"replication_doi": doi, "paper_folder": doi_to_folder(doi), "source": source, "stratum": stratum,
                      "in_catalog": doi in cat, "converted": converted(doi),
                      "discipline_group": _discipline(doi, expected),
                      "discipline": _fine_discipline(doi)[0], "subdiscipline": _fine_discipline(doi)[1],
                      "expected_result": (expected or {}).get("result", ""), "expected_type": (expected or {}).get("replication_type", ""),
                      "external_row_ids": (expected or {}).get("external_row_ids", ""), "note": note})
        return True

    def pick(pool: list, n: int, key=lambda x: x) -> list:
        # Every caller passes a list of DOIs, so --require-on-disk filters here and
        # applies to every stratum at once: a quota is then filled from papers a
        # coder can actually open, instead of being spent on absent ones.
        if args.require_on_disk:
            pool = [d for d in pool if on_disk(d)]
        pool = sorted(pool, key=key)
        return rng.sample(pool, min(n, len(pool)))

    # 1. FLoRa: 15/15/15 by result, >=50% non-psychology
    flora = read_csv(h.SILVER_DIR / "flora.csv") if (h.SILVER_DIR / "flora.csv").exists() else []
    by_paper = defaultdict(list)
    for r in flora:
        by_paper[r["replication_doi_norm"]].append(r)
    for res in ("success", "failure", "inconclusive"):
        pool = [d for d, rs in by_paper.items() if _paper_mode(rs, "result") == res]
        nonpsych = [d for d in pool if discipline_group(by_paper[d][0].get("discipline")) != "psych"]
        psych = [d for d in pool if d not in set(nonpsych)]
        chosen = pick(nonpsych, args.n_flora // 3 - args.n_flora // 6) + pick(psych, args.n_flora // 6)
        for d in chosen:
            rs = by_paper[d]
            add(d, "flora", f"flora:{res}", {"discipline_group": discipline_group(rs[0].get("discipline")), "result": res,
                                             "external_row_ids": ";".join(r["row_id"] for r in rs)})
    # 2. FReD v2.4.2: 9/8/8 by result, converted preferred
    fred = read_csv(h.SILVER_DIR / "fred_v2_4_2.csv") if (h.SILVER_DIR / "fred_v2_4_2.csv").exists() else []
    fp = defaultdict(list)
    for r in fred:
        fp[r["replication_doi_norm"]].append(r)
    for res, n in (("success", 9), ("failure", 8), ("inconclusive", 8)):
        pool = [d for d, rs in fp.items() if _paper_mode(rs, "result") == res]
        chosen = pick([d for d in pool if converted(d)], n) or pick(pool, n)
        for d in chosen:
            rs = fp[d]
            add(d, "fred_v242", f"fred:{res}", {"discipline_group": discipline_group(rs[0].get("discipline")), "result": res,
                                                "external_row_ids": ";".join(r["row_id"] for r in rs[:6])})
    # 3. main-GT human rows: all papers
    human = read_csv(h.SILVER_DIR / "main_gt_human.csv") if (h.SILVER_DIR / "main_gt_human.csv").exists() else []
    hp = defaultdict(list)
    for r in human:
        hp[normalize_doi(r["replication_url"]) or ""].append(r)
    for d, rs in hp.items():
        add(d, "main_gt_human", "main_gt_human", {"discipline_group": discipline_group(rs[0].get("openalex_field", "")),
                                                   "result": _paper_mode(rs, "result"), "external_row_ids": ";".join(r["row_id"] for r in rs)},
            note="prior_exposure=yes (Dan has seen pipeline output for these papers)")
    # production DB derived strata
    def db_paper(d):
        rs = dbp[d]
        return {"discipline_group": discipline_group(_paper_mode(rs, "discipline")), "result": _paper_mode(rs, "result"),
                "replication_type": TYPE_COLLAPSE.get(_paper_mode(rs, "replication_type"), _paper_mode(rs, "replication_type"))}
    pipeline_papers = [d for d, rs in dbp.items() if converted(d) and all((r.get("validated") or "").lower() != "yes" for r in rs)]
    # 4. biomedical human-coded initiatives (RP:CB, BRI, RSESR) + Dan's biology rows
    for d, rs in dbp.items():
        tags = {r.get("replication_initiative_tag", "") for r in rs}
        src = " ".join(r.get("source", "") for r in rs)
        if tags & {"RP:CB", "BRI", "RSESR"} or ("Brazilian" in src):
            add(d, "biomed_initiative", f"biomed:{(tags & {'RP:CB','BRI','RSESR'} or {'BRI'}).pop()}", db_paper(d))
    # 5. medical-fields hand-built slice: stratified by subdiscipline
    med = [d for d in pipeline_papers if _paper_mode(dbp[d], "discipline") == "medical fields"]
    by_sub = defaultdict(list)
    for d in med:
        by_sub[_paper_mode(dbp[d], "subdiscipline") or "(none)"].append(d)
    quota = max(1, args.n_medical // max(1, len(by_sub)))
    med_chosen = []
    for sub, pool in sorted(by_sub.items()):
        med_chosen += pick(pool, quota)
    med_chosen += pick([d for d in med if d not in set(med_chosen)], max(0, args.n_medical - len(med_chosen)))
    for d in med_chosen[:args.n_medical]:
        add(d, "medical_slice", f"medical:{_paper_mode(dbp[d], 'subdiscipline') or '(none)'}", db_paper(d))
    # 6. prod slice: 5 discipline groups x 4 types, 3 per cell; ensure >=20 with inconclusive/reversal
    cells = defaultdict(list)
    for d in pipeline_papers:
        e = db_paper(d)
        if e["replication_type"] in TYPE_VALUES:
            cells[(e["discipline_group"], e["replication_type"])].append(d)
    for (g, t), pool in sorted(cells.items()):
        for d in pick(pool, args.per_cell):
            add(d, "prod_slice", f"prod:{g}:{t}", db_paper(d))
    incon = [d for d in pipeline_papers if any(norm_label(r.get("result")) in ("inconclusive", "reversal") for r in dbp[d])]
    have = sum(1 for f in frame if f["source"] == "prod_slice" and f["expected_result"] in ("inconclusive", "reversal"))
    for d in pick([d for d in incon if d not in taken], max(0, 20 - have)):
        add(d, "prod_slice", "prod:inconclusive_or_reversal", db_paper(d))
    # 7. ReplicationWiki economics (human-identified close replications)
    rw = [d for d, rs in dbp.items() if converted(d) and any("replication wiki" in (r.get("validated_person") or "").lower() for r in rs)]
    for d in pick(rw, args.n_repwiki):
        add(d, "replication_wiki", "repwiki:econ", db_paper(d))
    # 8. Curate Science (stats-rich)
    cs = [d for d, rs in dbp.items() if converted(d) and any("curate" in (r.get("source") or "").lower() or "curate" in (r.get("validated_person") or "").lower() for r in rs)]
    for d in pick(cs, args.n_curate):
        add(d, "curate_science", "curate", db_paper(d))
    # 9. reversal stratum: DB papers with a reversal row + 'opposite direction' text without the label
    rev = [d for d, rs in dbp.items() if converted(d) and any(norm_label(r.get("result")) == "reversal" for r in rs)]
    for d in pick(rev, args.n_reversal):
        add(d, "reversal_stratum", "reversal:labelled", db_paper(d))
    opp = [d for d, rs in dbp.items() if converted(d) and d not in taken and any(
        re.search(r"opposite direction|reversed|reversal", (r.get("explanation") or "") + " " + (r.get("description") or ""), re.I)
        and norm_label(r.get("result")) != "reversal" for r in rs)]
    for d in pick(opp, args.n_reversal // 2):
        add(d, "reversal_stratum", "reversal:opposite_wording_unlabelled", db_paper(d))
    # 10. negatives: random high-confidence screened negatives + adversarial from classified.csv
    neg_pool = [doi for doi, c in cat.items() if str(c.get("contains_replications")) in ("0", "False", "false")
                and (c.get("screened_confidence") or "") == "high" and c.get("status") in ("screened", "extracted")]
    for d in pick(neg_pool, args.n_negative // 2):
        add(d, "negative_random", "negative:random", note="human must confirm non-replication")
    adv = []
    if config.CLASSIFIED_CSV.exists():
        pat = re.compile(r"reproducib|re-?analys|meta-?analy|biological replicate|technical replicate|commentary|within-study", re.I)
        for r in read_csv(config.CLASSIFIED_CSV):
            if str(r.get("is_replication")).lower() == "false" and pat.search((r.get("title") or "") + " " + (r.get("abstract") or "")):
                d = normalize_doi(r.get("doi", ""))
                if d and converted(d):
                    adv.append(d)
    for d in pick(adv, args.n_negative - args.n_negative // 2):
        add(d, "negative_adversarial", "negative:adversarial", note="human must confirm non-replication")

    frame_path = h.CODING_DIR / f"frame_gold_v{args.gold_version}_UNBLINDED.csv"
    write_csv(frame_path, frame)
    print(f"sampling frame: {len(frame)} papers -> {frame_path}")
    print("by source:", dict(Counter(f['source'] for f in frame)))
    print("by discipline group:", dict(Counter(f['discipline_group'] or '(none)' for f in frame)))
    nodisc = [f for f in frame if not f["discipline_group"]]
    print(f"no discipline: {len(nodisc)}"
          + (f" ({sum(1 for f in nodisc if f['source'].startswith('negative'))} of them negatives, "
             f"which no replications database can cover)" if nodisc else ""))
    print("converted already:", sum(1 for f in frame if f['converted']))
    absent = [f for f in frame if not on_disk(f["replication_doi"])]
    print(f"not readable on disk: {len(absent)}"
          + (" (--require-on-disk was set; these came from strata that add without pick)" if args.require_on_disk and absent else ""))
    slug = f"gold_v{args.gold_version}"
    if (config.DOI_RUNS_DIR / slug).exists():
        if args.force:
            delete_run(slug)
        else:
            print(f"doi run {slug} exists; --force to recreate"); return
    meta = create_run(slug, csv_text="doi\n" + "\n".join(f["replication_doi"] for f in frame) + "\n")
    print(f"doi run {slug}: {meta['n_dois']} DOIs -> {config.DOI_RUNS_DIR / slug}")


# ── coding sheets / agreement / adjudication / build ─────────────────────────
def _frame(gv: int) -> list[dict]:
    p = h.CODING_DIR / f"frame_gold_v{gv}_UNBLINDED.csv"
    if not p.exists():
        sys.exit(f"ERROR: {p} not found — run `harness.py sample --gold-version {gv}` first")
    return read_csv(p)


def _external_rows(row_ids: set[str]) -> dict[str, dict]:
    out = {}
    for name in ("flora.csv", "fred_v2_4_2.csv", "main_gt_human.csv"):
        p = h.SILVER_DIR / name
        if p.exists():
            for r in read_csv(p):
                if r.get("row_id") in row_ids:
                    out[r["row_id"]] = r
    return out


def cmd_coding_sheet(args) -> None:
    frame = _frame(args.gold_version)
    # A paper that is not on the drive cannot be coded, and listing it only earns
    # blank rows an adjudicator has to explain later. Some strata add without
    # going through `pick`, so --require-on-disk at sample time does not catch all.
    absent = [f for f in frame if paper_dir_for(f["paper_folder"]) is None]
    frame = [f for f in frame if paper_dir_for(f["paper_folder"]) is not None]
    if absent:
        print(f"skipping {len(absent)} paper(s) not readable on disk: "
              + ", ".join(f["paper_folder"] for f in absent[:5])
              + (" ..." if len(absent) > 5 else ""))
    ext_ids = {i for f in frame for i in (f.get("external_row_ids") or "").split(";") if i}
    ext = _external_rows(ext_ids)
    sheet = []
    for f in frame:
        neg = f["source"].startswith("negative")
        ids = [i for i in (f.get("external_row_ids") or "").split(";") if i]
        if neg:
            sheet.append({"row_id": f"{f['paper_folder']}#neg", "replication_doi": f["replication_doi"], "paper_folder": f["paper_folder"],
                          "external_row_id": "", "original_hint": "", "is_replication_paper": "", "why_negative": ""})
            continue
        if ids:
            for i in ids:
                e = ext.get(i, {})
                hint = f"{e.get('original_title','')} ({e.get('original_year','')})".strip()
                sheet.append({"row_id": f"{f['paper_folder']}#{i}", "replication_doi": f["replication_doi"], "paper_folder": f["paper_folder"],
                              "external_row_id": i, "original_hint": hint, "is_replication_paper": "yes"})
        else:
            sheet.append({"row_id": f"{f['paper_folder']}#1", "replication_doi": f["replication_doi"], "paper_folder": f["paper_folder"],
                          "external_row_id": "", "original_hint": "", "is_replication_paper": "yes"})
    # The statistical columns are optional by codebook ("lower priority than the
    # identification and classification fields") and worthless against a stat-free
    # extractor, which emits none of them. Dropping them takes the sheet from 23
    # coded columns to 9 -- the difference between a tractable coding job and an
    # untractable one -- and `build-gold` records that they were never coded so a
    # later reader cannot mistake blank for measured.
    coded = [f for f in CODED_FIELDS if f not in set(STAT_FIELDS)] if args.no_stats else CODED_FIELDS
    cols = ["row_id", "replication_doi", "paper_folder", "external_row_id", "original_hint", "is_replication_paper",
            "why_negative", *coded, "gt_ambiguity", "notes"]
    for r in sheet:
        for c in cols:
            r.setdefault(c, "")
    bad = set(cols) - SHEET_ALLOWED
    assert not bad, f"blinding violation: sheet would contain {bad}"
    out = h.CODING_DIR / f"sheet_gold_v{args.gold_version}_{args.coder}.csv"
    if out.exists() and not args.force:
        sys.exit(f"{out} exists (coding in progress?) — use --force to overwrite")
    write_csv(out, sheet, cols)
    print(f"blinded coding sheet: {len(sheet)} rows, {len(coded)} coded fields"
          + (" (statistics omitted)" if args.no_stats else "") + f" -> {out}")
    print("Coders: add rows for extra entries with a blank row_id; never open <tag>/ subfolders (see codebook.md).")


def _read_sheet(gv: int, coder: str) -> list[dict]:
    p = h.CODING_DIR / f"sheet_gold_v{gv}_{coder}.csv"
    if not p.exists():
        sys.exit(f"ERROR: {p} not found")
    rows = read_csv(p)
    if any(r.get('result') and re.search(r'\[[^\]]+: no entry matched this anchor\]\s*$', r.get('notes', '')) for r in rows):
        sys.exit(f"ERROR: {p} contains retained stale coding rows. Reconstruct against run logs "
                 "with audit_coding.py or rerun the coder before computing agreement.")
    seen = Counter()
    for r in rows:
        if not r.get("row_id", "").endswith("#neg"):
            payload = {k: v for k, v in r.items() if k not in ("row_id", "notes")}
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
            seen[digest] += 1
            r["row_id"] = f"{r.get('paper_folder','')}#{coder}:{digest}:{seen[digest]}"
    return rows


def _within(a, b, tol: float) -> bool | None:
    x, y = to_float(a), to_float(b)
    if x is None or y is None:
        return None
    return abs(x - y) <= tol * max(abs(x), 1e-9)


def effect_similarity(a: dict, b: dict) -> float:
    """Label/DOI-blind descriptive similarity; conservative, not a truth judge."""
    x, y = (norm_label(r.get("description")) for r in (a, b))
    if not x or not y:
        return 0.0
    # Do not confuse otherwise nearly identical descriptions of different studies.
    studies = lambda s: set(re.findall(r"\b(?:study|experiment|exp\.?)\s*(\d+[a-z]?)\b", s))
    sx, sy = studies(x), studies(y)
    if sx and sy and sx != sy:
        return 0.0
    # Strip framing that says nothing about WHICH claim, only that it is one:
    # "the original study claimed that ...", "martucci et al. (2018) reported that
    # ...", "study 3 tested the original claim that ...". One coder writing that
    # frame and the other writing the bare finding made identical claims score
    # 0.36-0.56 and go unpaired (2026-09-28 re-code). Applied after the study-number
    # guard above, which needs the frame's "study 3".
    frame = re.compile(r"^.{0,80}?\b(?:claimed|claims|reported|found|showed|demonstrated|"
                       r"proposed|argued|suggested)\s+that\s+")
    x, y = frame.sub("", x, count=1), frame.sub("", y, count=1)
    stop = set("the a an of in to and with for on is was were that this replication replicating effect study "
               "experiment original claim claimed reported found showed tested replicated authors et al".split())
    tx, ty = (set(re.findall(r"[a-z0-9]+", s)) - stop for s in (x, y))
    overlap = 2 * len(tx & ty) / (len(tx) + len(ty)) if tx and ty else 0.0
    return max(string_ratio(x, y), overlap)


def pair_extra_entries(A: dict, B: dict, threshold: float = 0.65,
                       margin: float = 0.08) -> list[tuple[str, str]]:
    """Unique mutual best description matches; ambiguous cases remain UNMATCHED.

    Apply to ALL entries, not just extras. Same original DOI or worksheet anchor
    does not establish the same study/effect. No result, type or DOI contributes
    to selection. Unmatched means unresolved identity, not a proven omission.
    Threshold/margin are audit heuristics, not calibrated accuracy guarantees.
    """

    by_paper: dict[str, list] = defaultdict(lambda: [[], []])
    for i, r in A.items():
        by_paper[r.get("paper_folder", "")][0].append(i)
    for i, r in B.items():
        by_paper[r.get("paper_folder", "")][1].append(i)

    pairs: list[tuple[str, str]] = []
    for folder, (ia, ib) in by_paper.items():
        if not folder or not ia or not ib:
            continue
        scores = {(i, j): effect_similarity(A[i], B[j]) for i in ia for j in ib}
        for i in ia:
            ranked = sorted(((scores[i, j], j) for j in ib), reverse=True)
            score, j = ranked[0]
            reverse = sorted(((scores[k, j], k) for k in ia), reverse=True)
            if score < threshold or reverse[0][1] != i:
                continue
            if len(ranked) > 1 and score - ranked[1][0] < margin:
                continue
            if len(reverse) > 1 and score - reverse[1][0] < margin:
                continue
            pairs.append((i, j))
    return pairs


def cmd_agreement(args) -> None:
    A = {r["row_id"]: r for r in _read_sheet(args.gold_version, args.coder_a)}
    B = {r["row_id"]: r for r in _read_sheet(args.gold_version, args.coder_b)}
    coded = lambda d, i: bool(norm_label(d[i].get("result")))
    anchored = []  # External worksheet anchors identify originals, not effects.
    unanchored_a = {i: r for i, r in A.items() if coded(A, i)}
    unanchored_b = {i: r for i, r in B.items() if coded(B, i)}
    extra_pairs = pair_extra_entries(unanchored_a, unanchored_b)
    shared = [(i, j) for i, j in anchored + extra_pairs if coded(A, i) and coded(B, j)]
    matched_a = {i for i, _ in extra_pairs}
    matched_b = {j for _, j in extra_pairs}
    res = [(norm_label(A[i]["result"]), norm_label(B[j]["result"])) for i, j in shared]
    typ = [(norm_label(A[i]["replication_type"]), norm_label(B[j]["replication_type"])) for i, j in shared
           if norm_label(A[i].get("replication_type")) and norm_label(B[j].get("replication_type"))]
    doi = [(doi_of(A[i], "original_url") == doi_of(B[j], "original_url")) for i, j in shared
           if doi_of(A[i], "original_url") or doi_of(B[j], "original_url")]
    n5 = [v for i, j in shared for v in (_within(A[i].get("replication_n"), B[j].get("replication_n"), 0.05),) if v is not None]
    negs = [i for i in A if i in B and i.endswith("#neg") and norm_label(A[i].get("is_replication_paper")) and norm_label(B[i].get("is_replication_paper"))]
    neg_pairs = [(norm_label(A[i]["is_replication_paper"]), norm_label(B[i]["is_replication_paper"])) for i in negs]
    k_res = cohen_kappa(res) if res else float("nan")
    k_typ = cohen_kappa(typ) if typ else float("nan")
    print(f"rows paired by effect description: {len(shared)} (conditional agreement, not whole-set accuracy)")
    print(f"result:            agreement {sum(a==b for a,b in res)/len(res) if res else float('nan'):.1%}  kappa {k_res:.3f}")
    print(f"replication_type:  agreement {sum(a==b for a,b in typ)/len(typ) if typ else float('nan'):.1%}  kappa {k_typ:.3f} (n={len(typ)})")
    print(f"original DOI exact: {sum(doi)/len(doi) if doi else float('nan'):.1%} (n={len(doi)})")
    print(f"replication_n within 5%: {sum(n5)/len(n5) if n5 else float('nan'):.1%} (n={len(n5)})")
    if neg_pairs:
        print(f"negative papers: agreement {sum(a==b for a,b in neg_pairs)/len(neg_pairs):.1%} (n={len(neg_pairs)})")
    print(f"pair coverage: {len(shared)}/{len(unanchored_a)} coder A entries; "
          f"{len(shared)}/{len(unanchored_b)} coder B entries")
    print("DIAGNOSTIC ONLY: do not apply the publication ladder to a selected automatic-match subset. "
          "Resolve effect identities, eligibility and enumeration before adjudicating gold.")
    # adjudication queue: every disagreement on result/type/DOI + seeded 20% of agreements
    rng = random.Random(args.seed)
    queue = []
    for i, j in shared:
        a, b = A[i], B[j]
        fields = []
        if norm_label(a["result"]) != norm_label(b["result"]):
            fields.append("result")
        if norm_label(a.get("replication_type")) != norm_label(b.get("replication_type")):
            fields.append("replication_type")
        if doi_of(a, "original_url") != doi_of(b, "original_url"):
            fields.append("original_url")
        reason = "disagreement" if fields else ("audit_sample" if rng.random() < args.audit_frac else "")
        if not reason:
            continue
        for f_ in (fields or ["result"]):
            queue.append({"row_id": i if i == j else f"{i}~{j}",
                          "paper_folder": a.get("paper_folder", ""), "field": f_, "reason": reason,
                          "a_value": a.get(f_, ""), "b_value": b.get(f_, ""), "a_description": a.get("description", "")[:200],
                          "b_description": b.get("description", "")[:200], "adjudicated_value": "", "adjudicator_id": "",
                          "adjudication_note": "", "gt_ambiguity": ""})
    for i in negs:
        if neg_pairs and norm_label(A[i]["is_replication_paper"]) != norm_label(B[i]["is_replication_paper"]):
            queue.append({"row_id": i, "paper_folder": A[i].get("paper_folder", ""), "field": "is_replication_paper", "reason": "disagreement",
                          "a_value": A[i]["is_replication_paper"], "b_value": B[i]["is_replication_paper"], "a_description": "", "b_description": "",
                          "adjudicated_value": "", "adjudicator_id": "", "adjudication_note": "", "gt_ambiguity": ""})
    only_a = [i for i in unanchored_a if i not in matched_a]
    only_b = [i for i in unanchored_b if i not in matched_b]
    for i, who in [(i, "a") for i in only_a] + [(i, "b") for i in only_b]:
        src = A if who == "a" else B
        queue.append({"row_id": i, "paper_folder": src[i].get("paper_folder", ""), "field": "row_exists", "reason": f"unmatched coder {who} entry; identity unresolved",
                      "a_value": "present" if who == "a" else "", "b_value": "present" if who == "b" else "",
                      "a_description": src[i].get("description", "")[:200], "b_description": "", "adjudicated_value": "",
                      "adjudicator_id": "", "adjudication_note": "", "gt_ambiguity": ""})
    qp = h.CODING_DIR / f"adjudication_queue_gold_v{args.gold_version}.csv"
    if qp.exists() and not args.force:
        old = {(r["row_id"], r["field"]): r for r in read_csv(qp)}
        for q in queue:
            o = old.get((q["row_id"], q["field"]))
            if o and all(o.get(k, '') == q.get(k, '') for k in
                         ('a_value', 'b_value', 'a_description', 'b_description')):
                for k in ("adjudicated_value", "adjudicator_id", "adjudication_note", "gt_ambiguity"):
                    q[k] = o.get(k, "")
    write_csv(qp, queue)
    print(f"adjudication queue: {len(queue)} items ({sum(1 for q in queue if q['reason']=='disagreement')} disagreements, "
          f"{sum(1 for q in queue if q['reason']=='audit_sample')} audit samples) -> {qp}")


def _discussion_excerpt(folder: str, papers_dir: Path, max_chars: int = 2500) -> str:
    for name in ("body.md",) + tuple(p.name for p in (papers_dir / folder).glob("*_from_*.md")) if (papers_dir / folder).is_dir() else ():
        p = papers_dir / folder / name
        if p.exists():
            t = p.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"(?:^|\n)#+\s*(General\s+)?(Discussion|Conclusion)", t, re.I)
            return (t[m.start():m.start() + max_chars] if m else t[-max_chars:])
    return "(no full text found)"


def cmd_adjudicate(args) -> None:
    qp = h.CODING_DIR / f"adjudication_queue_gold_v{args.gold_version}.csv"
    queue = read_csv(qp)
    papers_dir = Path(args.papers_dir) if args.papers_dir else config.PAPERS_DIR
    todo = [q for q in queue if not q.get("adjudicated_value")]
    print(f"{len(todo)} of {len(queue)} queue items need adjudication")
    for k, q in enumerate(todo, 1):
        print("\n" + "=" * 70 + f"\n[{k}/{len(todo)}] {q['row_id']}  field={q['field']}  ({q['reason']})")
        print(f"  A: {q['a_value']!r}   B: {q['b_value']!r}")
        if q.get("a_description") or q.get("b_description"):
            print(f"  A desc: {q['a_description']}\n  B desc: {q['b_description']}")
        print("-" * 70 + "\n" + _discussion_excerpt(q["paper_folder"], papers_dir) + "\n" + "-" * 70)
        while True:
            ans = input("[a] take A  [b] take B  [v] enter value  [s] skip  [q] quit: ").strip().lower()
            if ans == "q":
                write_csv(qp, queue); return
            if ans == "s":
                break
            if ans in ("a", "b", "v"):
                q["adjudicated_value"] = q["a_value"] if ans == "a" else q["b_value"] if ans == "b" else input("value: ").strip()
                q["adjudicator_id"] = args.adjudicator
                q["adjudication_note"] = input("note (optional): ").strip()
                q["gt_ambiguity"] = "ambiguous" if input("ambiguous? [y/N]: ").strip().lower() == "y" else "clear"
                break
        write_csv(qp, queue)
    print(f"saved -> {qp}")


def row_provenance(row_id: str, adj: dict, coder_a: str, coder_b: str | None) -> str:
    """`human:adjudicated` only where a human actually ruled on this row.

    Coders may be models (a different family from the one under test; see
    benchmarking/README.md). Where two of them agreed and no human ever read the
    row, the row is evidence, not adjudicated truth, and stamping it
    `human:adjudicated` would overstate it in exactly the way that made the
    February 2026 ground truth unusable. Such rows stay scoreable -- they are not
    `pipeline:`-authored, so the provenance guard passes them -- but they say what
    they are, and `evaluate` breaks results down by provenance so the mix is
    visible in every report.
    """
    ruled = any(r_ == row_id and q.get("adjudicated_value") and q.get("adjudicator_id")
                for (r_, _), q in adj.items())
    if ruled:
        return "human:adjudicated"
    return f"ai_consensus:{coder_a}+{coder_b}" if coder_b else f"single_coder:{coder_a}"


def cmd_build_gold(args) -> None:
    gv = args.gold_version
    frame = {f["paper_folder"]: f for f in _frame(gv)}
    A = _read_sheet(gv, args.coder_a)
    B = {r["row_id"]: r for r in _read_sheet(gv, args.coder_b)} if args.coder_b else {}
    if args.coder_b:
        # (Tested on the flag, not on B: an empty coder-B sheet made B falsy and
        # let a two-coder build through as if single-coder.)
        # The old builder joined worksheet IDs, ignored B-only effects and could
        # stamp a partly adjudicated row as human truth. Fail closed until a
        # human reconciles enumeration into a single stable effect-ID sheet.
        sys.exit("ERROR: two-coder gold export requires human-reconciled effect identities. "
                 "The legacy row-ID join is unsafe for multi-effect papers; agreement outputs "
                 "are proposals, not publishable gold. No gold files were written.")
    qp = h.CODING_DIR / f"adjudication_queue_gold_v{gv}.csv"
    queue = read_csv(qp) if qp.exists() else []
    adj = {(q["row_id"], q["field"]): q for q in queue}
    pending = [q for q in queue if q["reason"] == "disagreement" and not q.get("adjudicated_value")]
    if pending and not args.allow_unadjudicated:
        sys.exit(f"ERROR: {len(pending)} disagreements are not adjudicated; run `harness.py adjudicate` or pass --allow-unadjudicated (drops them).")
    dropped = {q["row_id"] for q in pending}
    salt = args.salt or f"gold_v{gv}"
    gold, negs = [], []
    for a in A:
        rid = a["row_id"]
        if rid in dropped:
            continue
        f = frame.get(a.get("paper_folder", ""), {})
        doi = a.get("replication_doi") or f.get("replication_doi", "")
        b = B.get(rid, {})
        if rid.endswith("#neg"):
            lab = adj.get((rid, "is_replication_paper"), {}).get("adjudicated_value") or a.get("is_replication_paper", "")
            negs.append({"replication_doi": doi, "paper_folder": a.get("paper_folder", ""), "source": f.get("source", ""),
                         "label": "negative" if norm_label(lab) in ("no", "negative", "n") else "positive_reclassified",
                         "why_negative": a.get("why_negative", ""), "coder_a_id": args.coder_a, "coder_b_id": args.coder_b or "",
                         "a_label": a.get("is_replication_paper", ""), "b_label": b.get("is_replication_paper", ""),
                         "split": split_of(doi, salt),
                         "provenance": row_provenance(rid, adj, args.coder_a, args.coder_b)})
            continue
        if not norm_label(a.get("result")):
            continue
        row = {"row_id": rid, "replication_doi": doi, "paper_folder": a.get("paper_folder", ""), "source": f.get("source", "gold"),
               "provenance": row_provenance(rid, adj, args.coder_a, args.coder_b), "split": split_of(doi, salt),
               "discipline_group": f.get("discipline_group", ""), "year_bucket": "", "tier_available": "",
               "external_label": f.get("expected_result", ""), "external_row_id": a.get("external_row_id", ""),
               "prior_exposure": "yes" if "prior_exposure" in (f.get("note") or "") else "no",
               "coder_a_id": args.coder_a, "coder_b_id": args.coder_b or ""}
        for fld in CODED_FIELDS:
            row[f"a_{fld}"] = a.get(fld, "")
            row[f"b_{fld}"] = b.get(fld, "")
            q = adj.get((rid, fld))
            row[fld] = q["adjudicated_value"] if q and q.get("adjudicated_value") else a.get(fld, "")
        qa = [q for (r_, _), q in adj.items() if r_ == rid and q.get("gt_ambiguity")]
        row["gt_ambiguity"] = "ambiguous" if any(q["gt_ambiguity"] == "ambiguous" for q in qa) else (a.get("gt_ambiguity") or "clear")
        row["adjudicator_id"] = ";".join(sorted({q["adjudicator_id"] for q in qa if q.get("adjudicator_id")}))
        row["adjudication_note"] = " | ".join(q["adjudication_note"] for q in qa if q.get("adjudication_note"))
        row["codebook_version"] = args.codebook_version
        row["coded_at"] = time.strftime("%Y-%m-%d")
        pd_ = config.PAPERS_DIR / row["paper_folder"]
        if pd_.is_dir():
            row["tier_available"] = "xml" if list(pd_.glob("*_from_xml.md")) else "html" if list(pd_.glob("*_from_html.md")) else "grobid" if (pd_ / "body.md").exists() else "pdf"
        gold.append(row)
    h.GOLD_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(h.GOLD_DIR / "gold_rows.csv", gold)
    write_csv(h.GOLD_DIR / "gold_negatives.csv", negs, ["replication_doi", "paper_folder", "source", "label", "why_negative",
                                                       "coder_a_id", "coder_b_id", "a_label", "b_label", "split", "provenance"])
    files = {str(p.relative_to(h.BENCH_DIR)): sha256_file(p) for p in
             [h.GOLD_DIR / "gold_rows.csv", h.GOLD_DIR / "gold_negatives.csv", CODEBOOK] + sorted(h.SILVER_DIR.glob("*.csv"))}
    manifest = {"gold_version": gv, "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "seed": SEED_DEFAULT, "salt": salt,
                "codebook_version": args.codebook_version, "codebook_sha256": sha256_file(CODEBOOK), "files": files,
                "n_rows": len(gold), "n_negatives": len(negs),
                "splits": {s: sorted({r["replication_doi"] for r in gold if r["split"] == s}) for s in ("dev", "test")},
                "n_by_source": dict(Counter(r["source"] for r in gold)),
                "n_by_split": dict(Counter(r["split"] for r in gold)),
                "n_result": dict(Counter(norm_label(r["result"]) for r in gold)),
                "coder_a_id": args.coder_a, "coder_b_id": args.coder_b or "",
                # Blank statistics mean "never coded", not "the paper reports none".
                "stats_coded": any((r.get(f) or "").strip() for r in gold for f in STAT_FIELDS),
                "n_by_provenance": dict(Counter(r["provenance"] for r in gold))}
    (h.GOLD_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"gold_v{gv}: {len(gold)} rows, {len(negs)} negatives; splits {manifest['n_by_split']}; results {manifest['n_result']}")
    print(f"provenance: {manifest['n_by_provenance']}; statistics coded: {manifest['stats_coded']}")
    if not manifest["stats_coded"]:
        print("  (stat-free gold: `evaluate` will say so instead of scoring statistics)")
    print(f"-> {h.GOLD_DIR}/gold_rows.csv, gold_negatives.csv, manifest.json")


# ── match audit ──────────────────────────────────────────────────────────────
def cmd_match_audit(args) -> None:
    res_dir = Path(args.results)
    log = res_dir / "match_log.jsonl"
    if not log.exists():
        sys.exit(f"ERROR: {log} not found (evaluate with --matcher llm first)")
    decisions = [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]
    rng = random.Random(args.seed)
    picked = [d for d in decisions if d.get("confidence") in ("low", "medium") or rng.random() < args.frac]
    sheet_path = res_dir / "match_audit_sheet.csv"
    if args.score:
        rows = read_csv(sheet_path)
        judged = [r for r in rows if norm_label(r.get("human_verdict")) in ("correct", "wrong")]
        if not judged:
            sys.exit("no human verdicts filled in yet (human_verdict = correct | wrong)")
        pos = [r for r in judged if r["relation"] in matching.MATCH_RELATIONS]
        neg = [r for r in judged if r["relation"] not in matching.MATCH_RELATIONS]
        prec = sum(norm_label(r["human_verdict"]) == "correct" for r in pos) / len(pos) if pos else None
        neg_ok = sum(norm_label(r["human_verdict"]) == "correct" for r in neg) / len(neg) if neg else None
        out = {"n_audited": len(judged), "matcher_precision_on_matches": prec, "n_matches_audited": len(pos),
               "non_match_correct_rate": neg_ok, "n_non_matches_audited": len(neg),
               "by_confidence": {c: {"n": len([r for r in judged if r["confidence"] == c]),
                                     "correct": sum(norm_label(r["human_verdict"]) == "correct" for r in judged if r["confidence"] == c)}
                                 for c in ("high", "medium", "low")}}
        (res_dir / "match_audit_metrics.json").write_text(json.dumps(out, indent=2))
        print(json.dumps(out, indent=2)); return
    rows = [{"key": d["key"], "gt_row_id": d.get("gt_row_id"), "replication_doi": d.get("replication_doi"),
             "n_candidates": d.get("n_candidates"), "match": d.get("match"), "relation": d.get("relation"),
             "confidence": d.get("confidence"), "reason": d.get("reason"), "human_verdict": "", "human_note": ""} for d in picked]
    write_csv(sheet_path, rows)
    print(f"audit sheet: {len(rows)} of {len(decisions)} decisions ({args.frac:.0%} seeded sample + all low/medium) -> {sheet_path}")
    print("Fill human_verdict with correct|wrong, then re-run with --score.")
