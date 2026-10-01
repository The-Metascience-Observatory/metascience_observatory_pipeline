"""Audit reconstruction checks: never recover a stale label as current truth."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_coding
import harness
import gold_build


def test_reconstruction_requires_log_count_and_keeps_unknown_paper_label(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_coding, 'SNAP', tmp_path)
    rows = [dict(row_id='p#1', paper_folder='p', replication_doi='10.1/p', result='failure', notes='new'),
            dict(row_id='', paper_folder='p', replication_doi='10.1/p', result='success', notes='[ling26: no entry matched this anchor]'),
            dict(row_id='q#1', paper_folder='q', replication_doi='10.1/q', result='', notes=''),
            dict(row_id='r#1', paper_folder='r', replication_doi='10.1/r', result='success', notes='[ling26: no entry matched this anchor]')]
    harness.write_csv(tmp_path/'sheet_gold_v1_ling26.csv', rows)
    (tmp_path/'code_ling26_retry.log').write_text('[1/206] p: 1 entry(ies)\n[2/206] q: 0 entry(ies)\n[3/206] r: 1 entry(ies)\n')
    clean, audit, needs = audit_coding.recover('ling26')
    assert needs == ['r']
    assert [r['result'] for r in clean if r['paper_folder']=='p'] == ['failure']
    assert next(r for r in clean if r['paper_folder']=='q')['is_replication_paper'] == ''


def test_two_coder_export_fails_before_writing_gold(tmp_path, monkeypatch):
    from argparse import Namespace
    import pytest
    monkeypatch.setattr(gold_build, '_frame', lambda gv: [])
    monkeypatch.setattr(gold_build, '_read_sheet', lambda gv,c: [dict(row_id='p#'+c)])
    with pytest.raises(SystemExit, match='human-reconciled effect identities'):
        gold_build.cmd_build_gold(Namespace(gold_version=1,coder_a='a',coder_b='b'))
