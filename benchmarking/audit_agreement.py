"""Recompute transparent conditional agreement; never report this as accuracy."""
from collections import Counter, defaultdict
import json
import math
import harness
from audit_coding import ROOT


def stats(pairs, A, B):
    out = dict(pairs=len(pairs), papers=len({A[i]['paper_folder'] for i,j in pairs}))
    for field in ('result', 'replication_type'):
        labels = [(A[i][field], B[j][field]) for i,j in pairs if A[i][field] and B[j][field]]
        kappa = harness.cohen_kappa(labels) if labels else float('nan')
        out[field] = dict(n=len(labels), agree=sum(a==b for a,b in labels),
                          agreement=sum(a==b for a,b in labels)/len(labels) if labels else None,
                          kappa=kappa if math.isfinite(kappa) else None)
    doi_pairs = [(harness.doi_of(A[i], 'original_url'), harness.doi_of(B[j], 'original_url')) for i,j in pairs]
    doi_pairs = [(a,b) for a,b in doi_pairs if a or b]
    out['original_doi'] = dict(n=len(doi_pairs), agree=sum(a==b for a,b in doi_pairs),
                              agreement=sum(a==b for a,b in doi_pairs)/len(doi_pairs) if doi_pairs else None)
    return out


def main():
    sheets = {c: harness.read_csv(ROOT/f'clean_{c}.csv') for c in ('ling26', 'luna56')}
    reviews = {r['paper_folder']:r for r in harness.read_csv(ROOT/'source_review_20.csv')}
    excluded = {f for f,r in reviews.items() if r['action']=='exclude_outcome'}
    A,B = ({r['row_id']:r for r in sheets[c] if r['result'] and r['paper_folder'] not in excluded}
           for c in ('ling26','luna56'))
    raw_pairs = harness.pair_extra_entries(A,B)
    kept, reviewed, rows = [], [], []
    for i,j in raw_pairs:
        a,b = A[i],B[j]
        review = reviews.get(a['paper_folder'], {})
        action = review.get('action')
        text = (a['description']+' '+b['description']).lower()
        verified = action == 'accept_pair' and review['pair_hint'].lower() in text
        # NAcc can be written in full by one coder: the source review includes both descriptions.
        verdict = 'ai_source_verified' if verified else 'automatic_candidate_unverified'
        if action in ('reject_pair','ambiguous_pair'):
            verdict = action
        else:
            kept.append((i,j))
        if verified:
            reviewed.append((i,j))
        rows.append(dict(a_id=i,b_id=j,paper_folder=a['paper_folder'], similarity=harness.effect_similarity(a,b),
                         identity_review=verdict, a_description=a['description'],b_description=b['description'],
                         a_result=a['result'],b_result=b['result'],a_type=a['replication_type'],b_type=b['replication_type'],
                         a_original_url=a['original_url'],b_original_url=b['original_url']))
    harness.write_csv(ROOT/'effect_pair_audit.csv',rows)
    matched_a,matched_b = {i for i,j in kept},{j for i,j in kept}
    unmatched = [dict(coder=c, row_id=i, paper_folder=r['paper_folder'], description=r['description'],
                      result=r['result'],replication_type=r['replication_type'],reason='identity unresolved; not proof of an omitted effect')
                 for c,data,matched in [('ling26',A,matched_a),('luna56',B,matched_b)] for i,r in data.items() if i not in matched]
    harness.write_csv(ROOT/'unmatched_effects.csv',unmatched)
    folders = {c:{r['paper_folder'] for r in sheets[c]} for c in sheets}
    counts = {c:Counter(r['paper_folder'] for r in sheets[c] if r['description']) for c in sheets}
    common = sorted(folders['ling26'] & folders['luna56'])
    enumeration = [dict(paper_folder=f, ling_entries=counts['ling26'][f],luna_entries=counts['luna56'][f],
                        counts_agree=counts['ling26'][f]==counts['luna56'][f],excluded_from_outcome=f in excluded) for f in common]
    harness.write_csv(ROOT/'enumeration_audit.csv',enumeration)
    paper_labels = {c:{r['paper_folder']:r['is_replication_paper'] for r in sheets[c] if r['is_replication_paper'] in ('yes','no')} for c in sheets}
    known = sorted(paper_labels['ling26'].keys() & paper_labels['luna56'].keys())
    sensitivity = {}
    for threshold in (0.55,0.65,0.75):
        pp = harness.pair_extra_entries(A,B,threshold=threshold)
        pp = [(i,j) for i,j in pp if reviews.get(A[i]['paper_folder'],{}).get('action') not in ('reject_pair','ambiguous_pair')]
        sensitivity[str(threshold)] = stats(pp,A,B)
    summary = dict(harness_version=harness.HARNESS_VERSION,
        status='diagnostic_only_not_extractor_accuracy_or_human_gold',
        method='Unique mutual best effect-description match; threshold .65, margin .08. No labels, original DOI or anchor IDs in matching.',
        excluded_from_outcome=sorted(excluded), scored_entries={'ling26':len(A),'luna56':len(B)},
        automatic_candidates_before_review=len(raw_pairs), candidate_pairs_after_review=stats(kept,A,B),
        ai_source_verified_pairs=stats(reviewed,A,B),
        pairing_coverage={'ling26':len(kept)/len(A) if A else None,'luna56':len(kept)/len(B) if B else None},
        unmatched={'ling26':len(A)-len(matched_a),'luna56':len(B)-len(matched_b)},
        enumeration=dict(papers=len(enumeration),equal_counts=sum(r['counts_agree'] for r in enumeration)),
        paper_label_agreement=dict(n=len(known),agree=sum(paper_labels['ling26'][f]==paper_labels['luna56'][f] for f in known)),
        threshold_sensitivity=sensitivity,
        limitations=['Historical reconstructed runs lack raw replies and input hashes.',
                     'Coverage is low and matched pairs are selected, so kappa is not a whole-frame reliability estimate.',
                     'Manual source checks here were by an AI, not by a human adjudicator.',
                     'Equal enumeration counts do not establish the same effects.',
                     'Unpaired, unscored and unrecoverable records are not silently counted as agreement.',
                     'Eligibility exclusions are a targeted audit, not an exhaustive screening of the frame.'])
    (ROOT/'agreement_metrics.json').write_text(json.dumps(summary,indent=2,allow_nan=False))
    print(json.dumps({k:v for k,v in summary.items() if k not in ('threshold_sensitivity','limitations','excluded_from_outcome')}, indent=2))


if __name__ == '__main__':
    main()
