"""Read-only production cross-reference and 206-paper extraction reconciliation."""
from collections import Counter, defaultdict
import json
import harness
import gold_build
from audit_coding import ROOT, SNAP
from mo_pipeline.label_centrality.common import latest_csv_path
from mo_pipeline.extract.extract_core import paper_artifacts

# AI source review, NOT human adjudication. Each phrase locates the evidence in
# the paper's primary rendition. No production database classification is edited.
REVIEWS = {
 '10.1073/pnas.1616921114': ('economics', 'behavioral and experimental economics',
     'direct mail field experiment', 'Donation incentives and motivation crowding out; experimental economics, not Safety Research.'),
 '10.1037/0022-3514.75.6.1411': ('psychology', 'judgment and decision making',
     'alternative-outcomes effect', 'Experiments on subjective probability and uncertainty judgments.'),
 '10.1037/pspa0000032': ('psychology', 'social psychology',
     'comparative framing effect', 'Social comparison, cognitive fluency and evaluative judgments; not sociology.'),
 '10.1016/j.cognition.2019.104157': ('psychology', 'cognitive psychology',
     'four studies that validate', 'Experiments on concepts, functions and teleological generalization.'),
 '10.1111/j.1467-9280.2008.02076.x': ('psychology', 'social psychology',
     'implicit attitude generalization', 'Implicit/explicit person evaluation and group membership; not sociology.'),
 '10.1037/xge0000157': ('psychology', 'psychometrics',
     'meta-analytic tools', 'Methodological comment on measurement of evidence and selection bias in psychological experiments; psychometrics is the closest available ontology subdiscipline.'),
}


def main():
    frame = harness.read_csv(SNAP/'frame_gold_v1_UNBLINDED.csv')
    db_path = latest_csv_path()
    db = gold_build._latest_db_rows()
    by_doi = defaultdict(list)
    for r in db:
        by_doi[r['_rep']].append(r)
    ontology = json.loads(harness.config.ONTOLOGY_PATH.read_text())
    allowed = {d: s for group in ontology.values() for d, s in group.items()}
    inventory, evidence, conflicts = [], [], []
    for r in frame:
        doi, folder = r['replication_doi'], r['paper_folder']
        matches = by_doi.get(harness.normalize_doi(doi), [])
        r['database_rows'] = len(matches)
        r['database_csv'] = db_path.name
        if matches:
            r['discipline'] = harness._paper_mode(matches, 'discipline')
            # Choose the subdiscipline only among rows of the selected discipline.
            r['subdiscipline'] = harness._paper_mode([x for x in matches if harness.norm_label(x.get('discipline'))==r['discipline']], 'subdiscipline')
            r['discipline_source'] = 'production_db:paper_mode'
            for field in ('discipline', 'subdiscipline'):
                values = sorted({x.get(field, '').strip() for x in matches if x.get(field, '').strip()})
                if len(values) > 1:
                    conflicts.append(dict(replication_doi=doi, field=field, values=' | '.join(values), chosen=r[field]))
        if doi in REVIEWS:
            d, s, phrase, reason = REVIEWS[doi]
            path = paper_artifacts(harness.paper_dir_for(folder))['primary']
            text = path.read_text(errors='replace')
            pos = text.lower().find(phrase.lower())
            assert pos >= 0, (doi, phrase)
            assert d in allowed and s in allowed[d], (d, s)
            evidence.append(dict(replication_doi=doi, old_discipline=r['discipline'],
                                 discipline=d, subdiscipline=s, source_path=str(path),
                                 line=text[:pos].count('\n')+1, evidence=text[max(0,pos-80):pos+220],
                                 reviewer='AI source review, not human adjudication', rationale=reason))
            r.update(discipline=d, subdiscipline=s, discipline_source='ai_source_review:2026-09-18')
        assert r['discipline'] in allowed, (doi, r['discipline'])
        path = harness.paper_dir_for(folder)
        tag = harness.config.PAPERS_DIR / folder / 'base_88_gold1'
        results = sorted(tag.glob('*_result.json'))
        if results:
            result = json.loads(results[0].read_text())
            status = 'extracted'
            n = len(result.get('replications', []))
        elif path and path.parent == harness.LEGACY_GT_PAPERS:
            status, n = 'not_attempted_legacy_only', ''
        else:
            status, n = 'attempted_failed', ''
        inventory.append(dict(replication_doi=doi, paper_folder=folder, readable_at=str(path or ''),
                              extraction_status=status, extracted_entries=n, discipline=r['discipline'],
                              subdiscipline=r['subdiscipline'], in_production_database=bool(matches)))
    harness.write_csv(ROOT/'frame_reconciled.csv', frame)
    harness.write_csv(ROOT/'discipline_source_review.csv', evidence)
    harness.write_csv(ROOT/'database_classification_conflicts.csv', conflicts,
                      ['replication_doi','field','values','chosen'])
    harness.write_csv(ROOT/'paper_inventory.csv', inventory)
    missing = [dict(csv_row=i+2, replication_url=r.get('replication_url',''), discipline=r.get('discipline',''))
               for i,r in enumerate(db) if not r.get('subdiscipline','').strip()]
    harness.write_csv(ROOT/'database_missing_subdiscipline.csv', missing)
    summary = dict(database=str(db_path), database_sha256=harness.sha256_file(db_path),
                   database_rows=len(db), database_missing_discipline=sum(not r.get('discipline','').strip() for r in db),
                   database_missing_subdiscipline=len(missing),
                   frame_papers=len(frame), frame_in_database=sum(bool(r['database_rows']) for r in frame),
                   frame_missing_discipline=sum(not r['discipline'] for r in frame),
                   frame_missing_subdiscipline=sum(not r['subdiscipline'] for r in frame),
                   discipline_counts=dict(Counter(r['discipline'] for r in frame).most_common()),
                   extraction_status=dict(Counter(r['extraction_status'] for r in inventory)))
    (ROOT/'inventory_summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
