"""Create source-linked review packets, never automatically promote AI labels to gold.

The packets intentionally preserve BOTH coders' candidates. DOI agreement is not
effect agreement. Human decisions live separately and are never regenerated.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path

import harness
from mo_pipeline.extract.extract_core import paper_artifacts
from prepare_gold_sources import ROOT, DOI as SPORTS_DOI

AUDIT = harness.RESULTS_DIR / 'audit_2026_09_18'
PROTOCOLS = {'10.7554/elife.' + s for s in
             ('11566', '11999', '09976', '11414', '10860', '04363')}
NOTICE = '10.31234/osf.io/esu9z'
# 'ai_adjudicator' decisions are a separate, weaker class: exported with
# ai_adjudicated:<model> provenance and never mixed with human:* rows.
REVIEWER_KINDS = ('human', 'ai_adjudicator')
SCARCITY = '10.1073/pnas.2103313118'


def read_csv(path):
    with path.open(newline='') as stream:
        return list(csv.DictReader(stream))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def candidate(row, coder):
    # Exclude imported external labels and hints from the review packet. The
    # source coder may already have seen hints: this does not restore blinding.
    allowed = ('result', 'replication_type', 'original_url', 'original_title',
               'description', 'citation_sentence', 'gt_ambiguity', 'notes',
               'is_replication_paper', 'why_negative')
    # Coder-prefixed: both coders' sheets name a paper's negative row '<folder>#neg',
    # so the bare row_id collided on 29 packets and one resolution covered both.
    return dict(candidate_id=f'{coder}:{row["row_id"]}', coder=coder,
                provenance='ai_candidate:' + coder,
                **{key: row.get(key, '') for key in allowed})


def validate_decision(packet, decision):
    """Workflow checks, not proof of human identity or scientific correctness.

    There is deliberately no gold exporter until the final effect schema and
    codebook boundary decisions have been reviewed.
    """
    errors = []
    if decision.get('packet_sha256') != digest(packet):
        errors.append('packet changed since review')
    kind = decision.get('reviewer_kind')
    if not decision.get('reviewer') or kind not in REVIEWER_KINDS:
        errors.append('reviewer required (reviewer_kind human or ai_adjudicator)')
    if kind == 'ai_adjudicator' and not decision.get('adjudicator_model'):
        errors.append('ai_adjudicator must name adjudicator_model')
    if not decision.get('reviewed_at'):
        errors.append('review date required')
    if decision.get('eligibility') not in ('positive', 'negative', 'excluded'):
        errors.append('eligibility unresolved')
    if not decision.get('eligibility_reason'):
        errors.append('eligibility reason required')
    if decision.get('sources_checked') is not True:
        errors.append('sources not checked')
    for source in packet['sources']:
        path = Path(source['path'])
        if not path.is_file() or harness.sha256_file(path) != source['sha256']:
            errors.append('source missing or changed: ' + str(path))
    expected = {r['candidate_id'] for r in packet['candidates']}
    resolutions = decision.get('candidate_resolutions', {})
    if set(resolutions) != expected:
        errors.append('every coder candidate must be resolved, including one-sided entries')
    effects = decision.get('effects', [])
    ids = [r.get('effect_id') for r in effects]
    if len(set(ids)) != len(ids) or any(not i for i in ids):
        errors.append('effect IDs missing or duplicated')
    for resolution in resolutions.values():
        targets = resolution.get('effect_ids', [])
        if not resolution.get('reason') or any(t not in ids for t in targets):
            errors.append('invalid candidate resolution')
        if not targets and resolution.get('action') != 'reject':
            errors.append('unmapped candidate needs explicit rejection')
    if decision.get('eligibility') == 'positive':
        if decision.get('enumeration_complete') is not True or not effects:
            errors.append('complete source-based effect enumeration required')
        for effect in effects:
            for field in ('original_reference', 'replication_study', 'outcome',
                          'contrast', 'timepoint', 'result', 'replication_type', 'evidence'):
                if not effect.get(field):
                    errors.append('effect missing ' + field)
            if effect.get('unresolved'):
                errors.append('effect has unresolved issues')
    elif effects:
        errors.append('negative/excluded papers cannot carry scored effects')
    return sorted(set(errors))


def blank_decision(packet):
    return dict(packet_sha256=digest(packet), reviewer='', reviewer_kind='', adjudicator_model='',
                reviewed_at='', eligibility='', eligibility_reason='', sources_checked=False,
                enumeration_complete=False, effects=[], candidate_resolutions={})


def is_untouched(decision):
    return not any(decision.get(k) for k in ('reviewer', 'reviewer_kind', 'reviewed_at', 'eligibility',
                                             'eligibility_reason', 'sources_checked',
                                             'enumeration_complete', 'effects', 'candidate_resolutions'))


def build(root=ROOT):
    frame = read_csv(AUDIT / 'frame_reconciled.csv')
    candidates = defaultdict(list)
    for coder in ('ling26', 'luna56'):
        for row in read_csv(AUDIT / f'clean_{coder}.csv'):
            candidates[row['replication_doi']].append(candidate(row, coder))
    reviews = {r['paper_folder']: r for r in
               json.loads((AUDIT / 'source_review_20.json').read_text())}
    sports = json.loads((ROOT / 'sources/sports/source_effects.json').read_text())
    registry = []
    (root / 'packets').mkdir(parents=True, exist_ok=True)
    (root / 'decisions').mkdir(exist_ok=True)
    for row in frame:
        doi, folder = row['replication_doi'], row['paper_folder']
        paper = harness.paper_dir_for(folder)
        primary = paper_artifacts(paper)['primary']
        sources = []
        if primary and Path(primary).is_file():
            sources.append(dict(path=str(primary), sha256=harness.sha256_file(primary)))
        flags = []
        if doi in PROTOCOLS:
            flags.append('protocol: exclude from outcome scoring pending human confirmation')
        if doi == NOTICE:
            flags.append('revision notice only: full paper needed')
        if doi == SCARCITY:
            flags.append('supplement required: recovered study list does not establish outcomes')
        if not row['subdiscipline']:
            flags.append('subdiscipline missing')
        review = reviews.get(folder)
        if doi == '10.7554/elife.04363':
            source_text = Path(primary).read_text()
            phrase = 'This replication attempt will perform'
            position = source_text.find(phrase)
            if position < 0:
                raise ValueError('New protocol evidence no longer matches source')
            review = dict(category='eligibility', action='exclude_outcome',
                          reviewer='Codex AI source review; not human adjudication',
                          evidence=phrase, source_path=str(primary),
                          source_line=source_text[:position].count('\n') + 1,
                          source_sha256=harness.sha256_file(primary),
                          finding='Registered report describes planned work, not completed replication outcomes.')
        if review:
            flags.append('targeted source review: ' + review['category'])
        proposals = sports['records'] if doi == SPORTS_DOI else []
        if proposals:
            sources.append(dict(path=sports['source_pdf'], sha256=sports['source_sha256']))
            flags.append('25 PDF table outcomes recovered; type and fine-grained identity pending')
        packet = dict(schema_version=1, replication_doi=doi, paper_folder=folder,
                      discipline=row['discipline'], subdiscipline=row['subdiscipline'],
                      discipline_source=row['discipline_source'], split='development',
                      status='AI proposals; no human adjudication', sources=sources,
                      flags=flags, candidates=candidates[doi], source_proposals=proposals,
                      prior_source_review=review)
        (root / 'packets' / f'{folder}.json').write_text(json.dumps(packet, indent=2))
        decision_path = root / 'decisions' / f'{folder}.json'
        template = blank_decision(packet)
        # A started decision is never rewritten: if its packet changed, the stale
        # hash makes `validate` report it. Only an untouched template follows the packet.
        if not decision_path.exists() or is_untouched(json.loads(decision_path.read_text())):
            decision_path.write_text(json.dumps(template, indent=2))
        registry.append(dict(replication_doi=doi, discipline=row['discipline'],
                             subdiscipline=row['subdiscipline'], candidates=len(candidates[doi]),
                             source_proposals=len(proposals), flags='; '.join(flags),
                             packet=f'packets/{folder}.json', decision=f'decisions/{folder}.json'))
    harness.write_csv(root / 'registry.csv', registry)
    inputs = [AUDIT / 'frame_reconciled.csv', AUDIT / 'clean_ling26.csv',
              AUDIT / 'clean_luna56.csv', AUDIT / 'source_review_20.json',
              ROOT / 'sources/sports/source_effects.json']
    manifest = dict(papers=len(frame), candidates=sum(r['candidates'] for r in registry),
                    source_proposals=sum(r['source_proposals'] for r in registry),
                    protocol_flags=sum(r['replication_doi'] in PROTOCOLS for r in registry),
                    discipline_counts=dict(Counter(r['discipline'] for r in frame)),
                    missing_subdiscipline=sum(not r['subdiscipline'] for r in frame),
                    split='development: previously inspected; not a fresh holdout',
                    inputs={str(p): harness.sha256_file(p) for p in inputs})
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


def _pairs(root):
    for path in sorted((root / 'packets').glob('*.json')):
        packet = json.loads(path.read_text())
        decision = json.loads((root / 'decisions' / path.name).read_text())
        yield path.stem, packet, decision


def status(root=ROOT):
    counts = Counter()
    for _, packet, decision in _pairs(root):
        if is_untouched(decision):
            counts['unreviewed'] += 1
        elif validate_decision(packet, decision):
            counts['started_with_errors'] += 1
        else:
            counts[f'complete:{decision["reviewer_kind"]}'] += 1
    print(json.dumps(dict(counts), indent=2))


def validate(root=ROOT, show_untouched=False):
    """Per-paper workflow errors for every decision that has been started."""
    bad = 0
    for folder, packet, decision in _pairs(root):
        if is_untouched(decision) and not show_untouched:
            continue
        errors = validate_decision(packet, decision)
        if errors:
            bad += 1
            print(f'{folder}:')
            for e in errors:
                print(f'  - {e}')
    print(f'{bad} decision(s) with errors')
    return bad


EFFECT_FIELDS = ('effect_id', 'original_reference', 'original_url', 'original_title',
                 'replication_study', 'outcome', 'contrast', 'timepoint', 'result',
                 'replication_type', 'description', 'citation_sentence', 'evidence', 'gt_ambiguity')


def decision_provenance(decision):
    if decision['reviewer_kind'] == 'human':
        return 'human:' + decision['reviewer'].strip().lower().replace(' ', '_')
    return 'ai_adjudicated:' + decision['adjudicator_model']


def export(root=ROOT, out=None, kind='human'):
    """Write complete, valid decisions of one reviewer kind as harness ground truth.

    Output is a directory with gold_rows.csv, gold_negatives.csv and manifest.json,
    the layout `harness.py evaluate --gt gold --gold-dir <out>` reads. Human and
    AI-adjudicated decisions are exported separately and never combined.
    """
    if kind not in REVIEWER_KINDS:
        raise SystemExit(f'unknown kind {kind!r}')
    out = Path(out or root / f'export_{kind}')
    frame = {r['paper_folder']: r for r in read_csv(AUDIT / 'frame_reconciled.csv')}
    rows, negatives, skipped = [], [], Counter()
    for folder, packet, decision in _pairs(root):
        if is_untouched(decision):
            skipped['unreviewed'] += 1
            continue
        if decision.get('reviewer_kind') != kind:
            skipped['other_kind'] += 1
            continue
        if validate_decision(packet, decision):
            skipped['invalid'] += 1
            continue
        f = frame.get(folder, {})
        base = dict(replication_doi=packet['replication_doi'], paper_folder=folder,
                    source=f.get('source', 'workbench_v2'), provenance=decision_provenance(decision),
                    split='dev', discipline=packet.get('discipline', ''),
                    discipline_group=f.get('discipline_group', ''),
                    reviewer=decision['reviewer'], reviewed_at=decision['reviewed_at'],
                    packet_sha256=decision['packet_sha256'])
        if decision['eligibility'] != 'positive':
            negatives.append(dict(base, label='negative' if decision['eligibility'] == 'negative' else 'excluded',
                                  why_negative=decision['eligibility_reason']))
            continue
        for e in decision['effects']:
            row = dict(base, row_id=f'{folder}#{e["effect_id"]}')
            row.update({k: e.get(k, '') if not isinstance(e.get(k), (list, dict)) else json.dumps(e[k])
                        for k in EFFECT_FIELDS})
            if not row['original_url'] and '/' in (e.get('original_reference') or '') and ' ' not in e['original_reference']:
                row['original_url'] = e['original_reference']
            rows.append(row)
    out.mkdir(parents=True, exist_ok=True)
    harness.write_csv(out / 'gold_rows.csv', rows, ['row_id', *base_keys(), *EFFECT_FIELDS])
    harness.write_csv(out / 'gold_negatives.csv', negatives, [*base_keys(), 'label', 'why_negative'])
    files = {p.name: harness.sha256_file(p) for p in (out / 'gold_rows.csv', out / 'gold_negatives.csv')}
    manifest = dict(kind=kind, rows=len(rows), negatives=len(negatives), skipped=dict(skipped),
                    papers=len({r['paper_folder'] for r in rows}) + len(negatives),
                    codebook='codebook_v2', split='dev: previously inspected; not a fresh holdout',
                    files_local=files)
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))
    return manifest


def base_keys():
    return ['replication_doi', 'paper_folder', 'source', 'provenance', 'split', 'discipline',
            'discipline_group', 'reviewer', 'reviewed_at', 'packet_sha256']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['build', 'status', 'validate', 'export'])
    parser.add_argument('--kind', choices=REVIEWER_KINDS, default='human', help='export: which reviewer kind')
    parser.add_argument('--out', default=None, help='export: output directory')
    parser.add_argument('--all', action='store_true', help='validate: include untouched templates')
    args = parser.parse_args()
    if args.command == 'build':
        build()
    elif args.command == 'status':
        status()
    elif args.command == 'validate':
        raise SystemExit(1 if validate(show_untouched=args.all) else 0)
    else:
        export(out=args.out, kind=args.kind)
