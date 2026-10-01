"""Recover source-only benchmark material; never read extractor tag outputs."""
from collections import Counter
import json
import subprocess
import urllib.request

import harness
from mo_pipeline.corpus.models import doi_to_folder

ROOT = harness.GOLD_DIR / 'workbench_v2'
DOI = '10.1007/s40279-025-02201-w'
# AI visual transcription of PDF p10, Tables 5 and 6. These are the AUTHORS'
# overall classifications, not classifications inferred from p-values.
OUTCOMES = [
    'informative failure', 'informative failure', 'practical failure', 'practical failure',
    'success', 'informative failure', 'informative failure', 'success', 'inconclusive',
    'success', 'informative failure',
    'practical failure', 'inconclusive', 'informative failure', 'practical failure',
    'practical failure', 'informative failure', 'practical failure', 'success',
    'practical failure', 'success', 'informative failure', 'informative failure', 'success', 'success',
]


def download(url, path, magic):
    """Only public source documents; never replace a prior downloaded artifact."""
    if not path.exists():
        request = urllib.request.Request(url, headers={'User-Agent': 'Metascience benchmark source review'})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(20_000_001)
        if len(data) > 20_000_000 or not data.startswith(magic):
            raise ValueError(f'Not the expected document or exceeds 20 MB: {url}')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if not path.read_bytes().startswith(magic):
        raise ValueError(f'Unexpected file content: {path}')
    return dict(url=url, path=str(path.resolve()), sha256=harness.sha256_file(path))


def prepare_sports():
    paper = harness.config.PAPERS_DIR/doi_to_folder(DOI)
    pdf = paper/f'{paper.name}.pdf'
    refs = {r['id']: r for r in json.loads((paper/'references.json').read_text())}
    rows = []
    for number, outcome in zip(range(45,70), OUTCOMES):
        r = refs[number]
        title = r['title']
        if number == 66:
            title = 'Further evidence for an external focus of attention in running: looking at specific focus instructions and individual differences'
        rows.append(dict(
            effect_id=f'{DOI}#ref{number}', original_reference=f'Reference {number}',
            original_title=title, original_url='https://doi.org/'+r['doi'],
            original_year=str(r['year']), original_authors='; '.join(r['authors']),
            original_journal=r['journal'], replication_study=f'Replication of reference {number}',
            outcome=f'Prespecified main effect for reference {number}, Table {5 if number<56 else 6}',
            contrast='Main contrast selected by replication authors; inspect original protocol for finer contrasts',
            timepoint='As reported for this study in the paper; not separately transcribed',
            description=title, expected_effect='nonzero',
            reported_result=outcome, result='failure' if 'failure' in outcome else outcome,
            replication_type='',
            evidence=[dict(path=str(pdf), sha256=harness.sha256_file(pdf), page=10,
                           locator=f'Table {5 if number<56 else 6}, reference {number}, Overall Outcome column',
                           quote=outcome, verification='AI visual transcription')],
            provenance='ai_source_transcription:codex', human_review=None,
            unresolved=['replication_type', 'verify bibliographic metadata and selected contrast', 'human approval']))
    assert len(rows)==25
    assert Counter(r['result'] for r in rows)==dict(success=7,failure=16,inconclusive=2)
    out = ROOT/'sources'/'sports'/'source_effects.json'
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(dict(replication_doi=DOI, records=rows,
        coverage='25 reported study-level outcomes; completeness is an AI proposal, not human-certified',
        mapping='Authors informative/practical failures map to failure; original wording retained. Do not infer reversal merely from a negative estimate.',
        source_pdf=str(pdf), source_sha256=harness.sha256_file(pdf)),indent=2))
    md=['# Recovered sports study outcomes', '',
        'AI visual transcription of Tables 5 and 6, PDF page 10. Human review pending.', '',
        '25 distinct studies: 7 reported successes, 16 reported failures, 2 inconclusive.',
        'This is a source transcription, not a scored gold release. Replication types remain unassigned.', '']
    for r in rows:
        md += [f"## {r['original_reference']}: {r['original_title']}", '',
               f"Author-reported outcome: {r['reported_result']}. Proposed codebook label: {r['result']}.",
               f"Original: {r['original_url']}", '']
    (out.parent/'source_effects.md').write_text('\n'.join(md))
    return dict(path=str(out), records=len(rows), result_counts=dict(Counter(r['result'] for r in rows)))


def prepare():
    ROOT.mkdir(parents=True,exist_ok=True)
    summary={'sports':prepare_sports(),'downloads':[]}
    sources=[('https://osf.io/download/hgres/', ROOT/'sources'/'scarcity'/'author_summary_table.pdf'),
             ('https://d-nb.info/1383888531/34', ROOT/'sources'/'sports'/'correction.pdf')]
    for url,path in sources:
        try:
            entry=download(url,path,b'%PDF')
            subprocess.run(['pdftotext','-layout',str(path),str(path.with_suffix('.txt'))],check=True)
            entry['text_path']=str(path.with_suffix('.txt'))
            summary['downloads'].append(entry)
        except Exception as exc:
            summary['downloads'].append(dict(url=url,status='unavailable',error=str(exc)))
    (ROOT/'source_recovery.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    prepare()
