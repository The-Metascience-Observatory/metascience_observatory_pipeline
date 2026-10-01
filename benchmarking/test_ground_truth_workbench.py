import json

import ground_truth_workbench as work
from prepare_gold_sources import OUTCOMES


def fixture(tmp_path):
    source = tmp_path / 'source.txt'
    source.write_text('source evidence')
    packet = dict(sources=[dict(path=str(source), sha256=work.harness.sha256_file(source))],
                  candidates=[dict(candidate_id='A:1'), dict(candidate_id='B:2')])
    decision = dict(packet_sha256=work.digest(packet), reviewer='Test fixture',
                    reviewer_kind='human', reviewed_at='2026-09-19',
                    eligibility='excluded', eligibility_reason='Protocol', sources_checked=True,
                    effects=[], candidate_resolutions={
                        key: dict(action='reject', effect_ids=[], reason='Protocol')
                        for key in ['A:1', 'B:2']})
    return packet, decision, source


def test_completed_exclusion(tmp_path):
    packet, decision, _ = fixture(tmp_path)
    assert work.validate_decision(packet, decision) == []


def test_ai_cannot_be_human(tmp_path):
    packet, decision, _ = fixture(tmp_path)
    decision['reviewer_kind'] = 'AI'
    assert any(e.startswith('reviewer required') for e in work.validate_decision(packet, decision))


def test_ai_adjudicator_must_name_its_model(tmp_path):
    packet, decision, _ = fixture(tmp_path)
    decision['reviewer_kind'] = 'ai_adjudicator'
    assert 'ai_adjudicator must name adjudicator_model' in work.validate_decision(packet, decision)
    decision['adjudicator_model'] = 'claude-opus-5-5'
    assert work.validate_decision(packet, decision) == []


def test_candidate_ids_are_coder_prefixed():
    # Both coders name a paper's negative row '<folder>#neg'.
    a = work.candidate(dict(row_id='p#neg'), 'ling26')
    b = work.candidate(dict(row_id='p#neg'), 'luna56')
    assert a['candidate_id'] != b['candidate_id']


def test_untouched_template_is_refreshed_but_started_decision_is_not():
    assert work.is_untouched(work.blank_decision({'x': 1}))
    started = work.blank_decision({'x': 1}) | {'eligibility': 'negative'}
    assert not work.is_untouched(started)


def _positive(tmp_path, kind, reviewer):
    packet, decision, _ = fixture(tmp_path)
    packet.update(replication_doi='10.1/x', discipline='psychology')
    effect = dict(effect_id='s1', original_reference='10.1/orig', replication_study='Study 1',
                  outcome='o', contrast='c', timepoint='t', result='success',
                  replication_type='direct', evidence='p.3')
    decision.update(packet_sha256=work.digest(packet), eligibility='positive', reviewer=reviewer,
                    reviewer_kind=kind, adjudicator_model='claude-opus-5-5', enumeration_complete=True,
                    effects=[effect], candidate_resolutions={
                        'A:1': dict(action='map', effect_ids=['s1'], reason='same claim'),
                        'B:2': dict(action='reject', effect_ids=[], reason='control')})
    assert work.validate_decision(packet, decision) == []
    return packet, decision


def test_export_keeps_reviewer_kinds_apart(tmp_path, monkeypatch):
    root = tmp_path / 'wb'
    (root / 'packets').mkdir(parents=True); (root / 'decisions').mkdir()
    for name, kind in (('h', 'human'), ('a', 'ai_adjudicator')):
        (tmp_path / name).mkdir()
        packet, decision = _positive(tmp_path / name, kind, 'Dan Elton')
        (root / 'packets' / f'{name}.json').write_text(json.dumps(packet))
        (root / 'decisions' / f'{name}.json').write_text(json.dumps(decision))
    (root / 'packets' / 'u.json').write_text(json.dumps({'candidates': [], 'sources': []}))
    (root / 'decisions' / 'u.json').write_text(json.dumps(work.blank_decision({})))
    monkeypatch.setattr(work, 'read_csv', lambda p: [])
    m = work.export(root, tmp_path / 'out_h', 'human')
    assert m['rows'] == 1 and m['skipped'] == {'unreviewed': 1, 'other_kind': 1}
    rows = work.harness.read_csv(tmp_path / 'out_h' / 'gold_rows.csv')
    assert rows[0]['provenance'] == 'human:dan_elton' and rows[0]['original_url'] == '10.1/orig'
    work.export(root, tmp_path / 'out_a', 'ai_adjudicator')
    rows = work.harness.read_csv(tmp_path / 'out_a' / 'gold_rows.csv')
    assert rows[0]['provenance'] == 'ai_adjudicated:claude-opus-5-5'
    # the harness reads the export and checks its manifest
    gt, _, info = work.harness.load_ground_truth('gold', gold_dir=tmp_path / 'out_a')
    assert len(gt) == 1 and gt[0]['has_reversal_class'] and info['gold_kind'] == 'ai_adjudicator'


def test_source_drift(tmp_path):
    packet, decision, source = fixture(tmp_path)
    source.write_text('changed')
    assert any('source missing or changed' in e for e in work.validate_decision(packet, decision))


def test_packet_drift(tmp_path):
    packet, decision, _ = fixture(tmp_path)
    packet['new_candidate'] = 'different'
    assert 'packet changed since review' in work.validate_decision(packet, decision)


def test_one_sided_entries_cannot_disappear(tmp_path):
    packet, decision, _ = fixture(tmp_path)
    del decision['candidate_resolutions']['B:2']
    assert any('every coder candidate' in e for e in work.validate_decision(packet, decision))


def test_positive_needs_enumeration(tmp_path):
    packet, decision, _ = fixture(tmp_path)
    decision['eligibility'] = 'positive'
    assert 'complete source-based effect enumeration required' in work.validate_decision(packet, decision)


def test_empty_decision_rejected(tmp_path):
    packet, _, _ = fixture(tmp_path)
    assert len(work.validate_decision(packet, {})) >= 5


def test_source_outcome_controls():
    assert len(OUTCOMES) == 25
    assert OUTCOMES.count('success') == 7
    assert OUTCOMES.count('informative failure') == 9
    assert OUTCOMES.count('practical failure') == 7
    assert OUTCOMES.count('inconclusive') == 2


def test_retired_hints_not_in_candidates():
    value = work.candidate(dict(row_id='A:1', original_hint='FLoRa hint',
                                expected_result='success', description='claim'), 'A')
    assert 'original_hint' not in value
    assert 'expected_result' not in value


def test_rebuild_preserves_decisions(tmp_path, monkeypatch):
    # Use a tiny frame while keeping the real source-backed proposal fixture.
    original = work.read_csv
    def read(path):
        rows = original(path)
        return rows[:1] if path.name == 'frame_reconciled.csv' else rows
    monkeypatch.setattr(work, 'read_csv', read)
    work.build(tmp_path)
    path = next((tmp_path / 'decisions').glob('*.json'))
    value = json.loads(path.read_text())
    value['reviewer'] = 'Preserve me'
    path.write_text(json.dumps(value))
    before = path.read_bytes()
    work.build(tmp_path)
    assert path.read_bytes() == before


def test_adjudication_view_withholds_the_system_under_test():
    packet = dict(candidates=[dict(candidate_id='ling26:1', coder='ling26'),
                              dict(candidate_id='luna56:2', coder='luna56'),
                              dict(candidate_id='luna56@v2:3', coder='luna56@v2')], sources=[])
    view = work.adjudication_view(packet)
    assert [c['coder'] for c in view['candidates']] == ['ling26']
    assert '2 candidate(s)' in view['blinding']
    assert len(packet['candidates']) == 3   # the full packet is untouched


def test_v2_candidates_carry_codebook_provenance():
    c = work.candidate(dict(row_id='p#1'), 'ling26@v2', 'ai_candidate:ling26@codebook_v2')
    assert c['candidate_id'] == 'ling26@v2:p#1' and c['provenance'].endswith('@codebook_v2')


def test_added_entries_without_row_id_get_distinct_ids():
    a = work.candidate(dict(row_id='', paper_folder='p', description='claim A'), 'ling26@v2')
    b = work.candidate(dict(row_id='', paper_folder='p', description='claim B'), 'ling26@v2')
    assert a['candidate_id'] != b['candidate_id'] and a['candidate_id'].startswith('ling26@v2:p#added_')
