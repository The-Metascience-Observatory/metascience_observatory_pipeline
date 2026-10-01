"""Evidence pointers for the targeted 20-paper AI review (not human gold).

Purposeful diagnostic sample: five protocol papers, three input/coverage cases,
seven outcome/scope disagreements, four type disagreements, one eligibility case.
It is not a representative sample and does not estimate prevalence of errors.
"""
import json
import harness
from audit_coding import ROOT
from mo_pipeline.extract.extract_core import paper_artifacts

CASES = [
 ('10.7554--elife.11566', 'eligibility', 'Registered report:', 'exclude_outcome', '',
  'Protocol for planned cancer experiments, not a completed replication result. Blank outcomes are legitimate; do not turn them into failure or inconclusive.'),
 ('10.7554--elife.11999', 'eligibility', 'This experiment will be replicated in Protocol 1', 'exclude_outcome', '',
  'Future-tense protocols describe original findings. Outcome labels risk attributing the original results to the planned replication.'),
 ('10.7554--elife.09976', 'eligibility', 'This experiment will be replicated in Protocol 1', 'exclude_outcome', '',
  'Registered protocol: the RAF findings in the introduction belong to the original study, not to completed replication experiments.'),
 ('10.7554--elife.11414', 'eligibility', 'Registered Report:', 'exclude_outcome', '',
  'Registered protocol without completed replication outcomes. Fresh Ling responses switched between blanks and labels: successful JSON parsing is not evidence validity.'),
 ('10.7554--elife.10860', 'eligibility', 'Registered report:', 'exclude_outcome', '',
  'Three planned IDH protocols are not three observed successes. Keep the document in the eligibility audit, outside outcome accuracy.'),
 ('10.1007--s40279-025-02201-w', 'input_coverage', 'Table 1Original and replication study descriptives', 'exclude_outcome', '',
  'Primary XML rendition preserves table captions but omits the study-level table bodies (front matter says tables: 0). Ling has no valid complete enumeration. Review PDF/tables before coding; neither zero nor 25 guessed outcomes is a reference answer.'),
 ('10.7554--elife.45120', 'outcome', 'not in the same direction as the original study and not statistically significant', 'accept_pair', 'metastatic burden',
  'Descriptions identify the same Cav1 metastasis experiment. The result paragraph reports non-significant, nonmatching direction and a shorter endpoint. This is an interpretation disagreement, not a wrong-original pairing; failure is better supported than calling it a significant reversal.'),
 ('10.7554--elife.17044', 'codebook_null', 'did not result in an observational difference in tumor volume', 'accept_pair', 'ACHN',
  'Descriptions identify the ACHN negative-control contrast. Both original and replication report no treatment difference. The codebook needs an explicit rule for replication of a null result; do not equate non-significance with either proven equivalence or automatic failure.'),
 ('10.7554--elife.43511', 'outcome', 'this creates a potential confound', 'accept_pair', 'CD44+',
  'Both describe miR-34a in CD44-positive versus negative LAPC4 cells. Direction differs from the original but changed gating creates a confound. Failure/inconclusive needs adjudication; an opposite point estimate alone does not establish reversal.'),
 ('10.7554--elife.18173', 'outcome', 'Both IgG and anti-CD47 treated tumors resulted in minimal to moderate lymphocytic infiltrate', 'accept_pair', 'lymphocytic',
  'Both target the same infiltration comparison. The ordinal infiltration assessment must be distinguished from tumor growth, blood lymphocytes and exploratory neutrophil effects. Source supports uncertainty about how strongly to classify this contrast.'),
 ('10.1016--j.ynirp.2022.100147', 'codebook_null', 'NAcc response to reward anticipation did not differ', 'accept_pair', 'NAcc',
  'Both descriptions identify the NAcc null contrast. Do not transfer the hedged MPFC findings or paper-wide summary to this contrast. The null-replication convention is the primary unresolved rule.'),
 ('10.1016--j.jsat.2013.07.013', 'scope', 'using only the e-SBI without any incentives or motivational mailings', 'ambiguous_pair', '',
  'Original intervention included mailings; the replication removed them. Three-month abstinence improved, six-month point-prevalence and use-frequency measures did not. Descriptions lack a common endpoint/timepoint, so success/inconclusive agreement is not interpretable until scope is fixed.'),
 ('10.1177--073953298600700307', 'matching', 'reduction  of  the  factor analytic  solution', 'reject_pair', '',
  'One description combines factor structure and readership prediction, the other only factor structure. The source separates mixed predictive findings from a two-factor rather than three-factor solution. The automatic candidate is not equivalent in scope.'),
 ('10.1525--mp.2012.30.2.161', 'type', 'representative sample of Internet users, as opposed to music fans', 'accept_pair', 'five orthogonal',
  'The broad-genre MUSIC comparison is recognizable, distinct from the jazz-only and rock-only studies. Sampling/generalization and added attribute measures explain why direct versus close experiment is not simply clerical; close extension also needs human consideration.'),
 ('10.3389--fpsyg.2018.02476', 'type', 'relative to a post-neutral trial baseline', 'accept_pair', 'sequential congruency',
  'Both descriptions target the bilingual sequential-congruency contrast and agree on failure. Added neutral-baseline trials and language-group differences must be weighed under the method/context type rule.'),
 ('10.1525--collabra.216', 'type', 'Participants placed the box of a leader in a more elevated position', 'accept_pair', 'leader',
  'The matched descriptions target Study 1 (power to placement). Study 2 reverses the causal direction and is not interchangeable even though the original DOI is shared. A genuine type disagreement remains for Study 1.'),
 ('10.1001--archneurol.2011.155', 'type', 'failed to find a significant association between the polyT polymorphism', 'accept_pair', 'age at onset',
  'The age-at-onset comparison is recognizable and both coders call it failure. Risk, onset, expression and endophenotype analyses must not be merged merely because they cite the same TOMM40 original.'),
 ('10.1016--j.cognition.2019.104157', 'eligibility_boundary', 'Experiments 1-4 served to assess', 'review_required', '',
  'Four experiments test a newly developed theory and linguistic tests. Luna lists five conceptual replications. A human must decide which prior published empirical findings, if any, are deliberately replicated rather than merely cited or used as theoretical background.'),
 ('10.1073--pnas.2103313118', 'input_coverage', 'The remaining studies are cited in SI Appendix', 'exclude_outcome', '',
  'Paper reports 19 study results, while many original identities/details are in the SI and figure. The coding input has only main text/references and no local SI. The fresh one-entry summary is not exhaustive ground truth.'),
 ('10.31234--osf.io--esu9z', 'input_document', 'Due to a bug in the analysis code', 'exclude_outcome', '',
  'The entire available body is a 362-character revision notice, not a paper. The extraction failure is an input-document problem; do not treat it as a negative replication paper or a model accuracy failure.'),
]


def main():
    rows = []
    for folder, category, phrase, action, pair_hint, finding in CASES:
        path = paper_artifacts(harness.paper_dir_for(folder))['primary']
        text = path.read_text(errors='replace')
        pos = text.lower().find(phrase.lower())
        assert pos >= 0, (folder, phrase)
        rows.append(dict(paper_folder=folder, category=category, action=action,
                         pair_hint=pair_hint, finding=finding, source_path=str(path),
                         source_line=text[:pos].count('\n')+1,
                         source_sha256=harness.sha256_file(path),
                         evidence=text[max(0,pos-80):pos+len(phrase)+220],
                         reviewer='Codex AI source review; not human adjudication'))
    assert len(rows) == len({r['paper_folder'] for r in rows}) == 20
    harness.write_csv(ROOT/'source_review_20.csv', rows)
    (ROOT/'source_review_20.json').write_text(json.dumps(rows, indent=2))
    md = ['# Targeted 20-paper source review', '',
          'AI diagnostic review, not human-adjudicated ground truth. Purposefully selected problems, not a prevalence sample.', '']
    for n,r in enumerate(rows,1):
        md += [f"## {n}. {r['paper_folder']}", '', f"Category: {r['category']}. Decision: {r['action']}.", '',
               r['finding'], '', f"[Source passage]({r['source_path']}:{r['source_line']})", '']
    (ROOT/'source_review_20.md').write_text('\n'.join(md))
    print('Reviewed 20 distinct papers; source phrases and hashes verified.')


if __name__ == '__main__':
    main()
