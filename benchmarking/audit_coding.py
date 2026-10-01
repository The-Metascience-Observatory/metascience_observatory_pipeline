"""Recover the September coding runs without treating retained rows as new data.

An unmatched row ALWAYS receives a trailing no-entry marker in the old writer.
Matched rows may retain that marker if the new model supplied no note. Therefore
remove marked rows, and accept a paper only when its remaining count exactly
equals the latest success log. Otherwise require a fresh, checkpointed reply.
Historical negatives that were relabelled positive lost all entries and need
fresh coding. This is reconstruction, not a recovered raw model response.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import re
import harness

ROOT = harness.RESULTS_DIR / "audit_2026_09_18"
SNAP = ROOT / "snapshot"


def recover(coder):
    rows = harness.read_csv(SNAP / f"sheet_gold_v1_{coder}.csv")
    log = SNAP / ("code_ling26_retry.log" if coder == "ling26" else "code_luna56.log")
    counts = {f: int(n) for f, n in re.findall(r"\[\d+/206\] (\S+): (\d+) entry", log.read_text())}
    groups = defaultdict(list)
    for r in rows:
        groups[r["paper_folder"]].append(r)
    clean, audit, needs = [], [], []
    for folder, rr in groups.items():
        coded = [r for r in rr if r.get("result")]
        kept = [r for r in coded if not r.get("notes", "").rstrip().endswith(f"[{coder}: no entry matched this anchor]")]
        expected = counts.get(folder)
        complete = expected is not None and len(kept) == expected
        if complete:
            clean.extend(dict(r) for r in kept)
            if expected == 0:
                r = {k: "" for k in rr[0]}
                negative_anchor = next((x for x in rr if x['row_id'].endswith('#neg')), {})
                r.update(row_id=f"{folder}#neg", paper_folder=folder,
                         replication_doi=rr[0]["replication_doi"],
                         is_replication_paper=negative_anchor.get('is_replication_paper', ''),
                         why_negative=negative_anchor.get('why_negative', ''),
                         notes="Reconstructed zero-entry result from latest successful run log; no raw reply retained. Paper label unknown unless explicitly saved on a negative anchor.")
                clean.append(r)
        else:
            needs.append(folder)
        audit.append(dict(coder=coder, paper_folder=folder, saved_labelled=len(coded),
                          latest_logged_entries=expected, unmarked_labelled=len(kept),
                          removed_stale_candidates=len(coded)-len(kept),
                          status="reconstructed" if complete else "requires_fresh_reply"))
    return clean, audit, needs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prepare", action="store_true")
    args = ap.parse_args()
    ROOT.mkdir(exist_ok=True)
    summary = {}
    for coder in ("ling26", "luna56"):
        clean, audit, needs = recover(coder)
        if args.prepare:
            (ROOT / f"retry_{coder}.txt").write_text("\n".join(needs)+"\n")
        fresh = {}
        for run in sorted(ROOT.glob(f"{coder}_run*")):
            # The initial schema check was too strict for the codebook's allowed
            # blanks. Retain complete, valid replies with explicit blank results
            # as unscored entries; do not turn absence of evidence into failure.
            from ai_coder import validate_reply, parse_json_reply
            for path in sorted((run/'attempts').glob('*.json')):
                attempt = json.loads(path.read_text())
                if attempt.get('error') or attempt.get('truncated'):
                    continue
                data = parse_json_reply(attempt.get('text', ''))
                try:
                    validate_reply(data)
                except (ValueError, AttributeError):
                    continue
                folder = path.name.split('.attempt')[0]
                fresh[folder] = data
            for path in run.glob("*.json"):
                if path.name in ("manifest.json", "completion.json") or path.name.endswith(".error.json"):
                    continue
                data = json.loads(path.read_text())
                validate_reply(data)
                fresh[path.stem] = data
        frame = {r['paper_folder']: r for r in harness._frame(1)}
        fields = list(harness.read_csv(SNAP / f"sheet_gold_v1_{coder}.csv")[0])
        for folder, data in fresh.items():
            clean = [r for r in clean if r["paper_folder"] != folder]
            for e in data["entries"] or [{}]:
                r = {k: str(e.get(k) or "") for k in fields}
                r.update(paper_folder=folder, replication_doi=frame[folder]['replication_doi'],
                         row_id="" if data["entries"] else f"{folder}#neg",
                         is_replication_paper=data["is_replication_paper"],
                         why_negative=data.get('why_negative', ''))
                clean.append(r)
        for row in audit:
            if row['paper_folder'] in fresh:
                row['status'] = 'fresh_checkpointed_reply'
        # Persist coder-specific, content-derived IDs, never worksheet positions.
        occurrences = Counter()
        for r in clean:
            if r.get('description'):
                digest = hashlib.sha256(json.dumps({k: r[k] for k in fields if k not in
                    ('row_id', 'notes', 'external_row_id', 'original_hint')}, sort_keys=True).encode()).hexdigest()[:16]
                occurrences[digest] += 1
                r['row_id'] = f"{r['paper_folder']}#{coder}:{digest}:{occurrences[digest]}"
        harness.write_csv(ROOT / f"clean_{coder}.csv", clean, fields)
        harness.write_csv(ROOT / f"recovery_{coder}.csv", audit)
        unresolved = [r['paper_folder'] for r in audit if r['status']=='requires_fresh_reply']
        summary[coder] = dict(papers=len({r['paper_folder'] for r in clean}),
                              entries=sum(bool(r['result']) for r in clean),
                              unscored_entries=sum(bool(r['description']) and not r['result'] for r in clean),
                              statuses=dict(Counter(r['status'] for r in audit)), unresolved=unresolved)
    (ROOT/'recovery_summary.json').write_text(json.dumps(summary, indent=2))
    manifest = {str(p.relative_to(ROOT)): harness.sha256_file(p) for p in sorted(SNAP.iterdir()) if p.is_file()}
    (ROOT/'snapshot_sha256.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
