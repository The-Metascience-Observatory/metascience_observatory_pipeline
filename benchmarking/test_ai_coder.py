"""AI-coder regression tests (no model calls, no drive). Run:
    cd mo_pipeline && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest benchmarking/test_ai_coder.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH.parent))
sys.path.insert(1, str(BENCH))

import ai_coder  # noqa: E402

SHEET_COLS = ["row_id", "replication_doi", "paper_folder", "external_row_id", "original_hint",
              "is_replication_paper", "why_negative", "result", "replication_type", "original_url",
              "original_title", "original_authors", "original_year", "original_journal",
              "description", "citation_sentence", "gt_ambiguity", "notes"]


def _row(**kw):
    r = {c: "" for c in SHEET_COLS}
    r.update(kw)
    return r


def test_extra_entries_become_new_rows_with_a_blank_row_id():
    """Exhaustive enumeration is the point: entries the external record never
    coded must survive as rows, or entry precision stays unmeasurable."""
    rows = [_row(row_id="10.1--p#f1", replication_doi="10.1/p", paper_folder="10.1--p",
                 external_row_id="f1", original_hint="Ego depletion and self-control (1998)",
                 is_replication_paper="yes")]
    data = {"is_replication_paper": "yes", "entries": [
        {"result": "failure", "replication_type": "direct",
         "original_title": "Ego depletion and self-control", "original_year": "1998",
         "original_url": "https://doi.org/10.1/orig", "description": "the depletion effect",
         "citation_sentence": "We replicate Baumeister et al. (1998)."},
        {"result": "success", "replication_type": "close extension",
         "original_title": "A different original", "original_year": "2005",
         "original_url": "https://doi.org/10.1/other", "description": "a second effect"},
    ]}
    out = ai_coder.fill_sheet(rows, {"10.1--p": data}, "ling26")

    assert len(out) == 2
    anchored = [r for r in out if r["row_id"]]
    extra = [r for r in out if not r["row_id"]]
    assert len(anchored) == 1 and len(extra) == 1
    # the anchor got the entry that actually matches its hint, not just the first one
    assert anchored[0]["result"] == "failure"
    assert anchored[0]["original_url"] == "https://doi.org/10.1/orig"
    assert extra[0]["result"] == "success"
    assert extra[0]["paper_folder"] == "10.1--p" and extra[0]["replication_doi"] == "10.1/p"
    assert "extra entry" in extra[0]["notes"]


def test_two_anchors_are_not_claimed_by_the_same_entry():
    rows = [_row(row_id="p#1", paper_folder="p", original_hint="Cleanliness and moral judgment (2008)"),
            _row(row_id="p#2", paper_folder="p", original_hint="Money priming and helping (2006)")]
    data = {"is_replication_paper": "yes", "entries": [
        {"result": "success", "original_title": "Money priming and helping", "original_year": "2006"},
        {"result": "failure", "original_title": "Cleanliness and moral judgment", "original_year": "2008"},
    ]}
    out = ai_coder.fill_sheet(rows, {"p": data}, "ling26")
    by_id = {r["row_id"]: r for r in out if r["row_id"]}
    assert by_id["p#1"]["result"] == "failure"
    assert by_id["p#2"]["result"] == "success"


def test_a_negative_paper_fills_the_negative_row():
    rows = [_row(row_id="n#neg", paper_folder="n", replication_doi="10.1/n")]
    out = ai_coder.fill_sheet(rows, {"n": {"is_replication_paper": "no",
                                           "why_negative": "A meta-analysis, not a replication.",
                                           "entries": []}}, "luna56")
    assert out[0]["is_replication_paper"] == "no"
    assert "meta-analysis" in out[0]["why_negative"]


def test_a_paper_that_failed_to_code_is_left_blank_not_guessed():
    rows = [_row(row_id="p#1", paper_folder="p", original_hint="Something (2010)")]
    out = ai_coder.fill_sheet(rows, {}, "ling26")
    assert out[0]["result"] == "" and out[0]["original_url"] == ""


def test_blinding_guard_rejects_a_path_inside_a_tag_subfolder(tmp_path):
    """Coders must never see pipeline output; a tag subfolder is exactly that."""
    paper = tmp_path / "10.1--p"
    (paper / "base_88_r1").mkdir(parents=True)
    good = paper / "body.md"
    good.write_text("prose")
    leaked = paper / "base_88_r1" / "result.json"
    leaked.write_text("{}")

    ai_coder._assert_blinded(paper, {"primary": good, "pdf": None, "structured_raw": None, "supporting": []})
    with pytest.raises(AssertionError, match="blinding violation"):
        ai_coder._assert_blinded(paper, {"primary": leaked, "pdf": None, "structured_raw": None, "supporting": []})


def test_the_model_under_test_is_refused_as_a_coder(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ai_coder.py", "--gold-version", "1", "--coder", "x",
                                      "--model", "sonnet", "--provider", "claude_cli"])
    with pytest.raises(SystemExit) as e:
        ai_coder.main()
    assert "model under test" in str(e.value)


def _prompt(refs_body, source="references.json"):
    return (f"Extract.\n\n[PAPER DOI]\nhttps://doi.org/10.1/p\n\n[FULL TEXT: grobid]\n"
            f"Real prose about a replication study.\n\n[REFERENCE LIST: {source}]\n{refs_body}\n\n"
            "Reply with the JSON object only.")


def test_a_garbage_reference_list_is_withheld_not_handed_over():
    """GROBID on a bad PDF yields OCR noise; a [REFERENCE LIST] header over noise
    invites a fabricated original_url."""
    junk = "\n".join(["D 3&0201. 01&%00: 3.", "G 2&0121/123&(. %9%;3D)%&1: 1.",
                      "% 3$\":3&1%)D3$($@\"#H$2:(&. ($G/%0: 11."])
    out, dropped = ai_coder.drop_unreadable_references(_prompt(junk))
    assert dropped is True
    assert "3&0201" not in out and "[REFERENCE LIST: none]" in out
    assert "leave `original_url` empty rather than guessing" in out
    assert "Reply with the JSON object only." in out      # the tail survives

    real = "\n".join([
        "Baumeister, R. F., et al. (1998). Ego depletion: Is the active self a limited resource?",
        "Sripada, C., et al. (2014). Please tell me I do not have ego depletion. Journal of Research.",
        "Hagger, M. S. (2016). A multilab preregistered replication of the ego-depletion effect."])
    out2, dropped2 = ai_coder.drop_unreadable_references(_prompt(real))
    assert dropped2 is False and out2 == _prompt(real)


def test_a_prompt_without_a_reference_section_is_untouched():
    p = "Extract.\n\n[PAPER DOI]\nhttps://doi.org/10.1/p\n\n[FULL TEXT: grobid]\nprose."
    assert ai_coder.drop_unreadable_references(p) == (p, False)


def test_uncoded_papers_are_preserved_not_dropped():
    """The sheet is written back whole, so a pilot on a subset must not delete the rest."""
    rows = [_row(row_id="a#1", paper_folder="a", original_hint="X (2001)"),
            _row(row_id="b#1", paper_folder="b", original_hint="Y (2002)"),
            _row(row_id="c#neg", paper_folder="c")]
    out = ai_coder.fill_sheet(rows, {"a": {"is_replication_paper": "yes", "entries": [
        {"result": "success", "original_title": "X", "original_year": "2001"}]}}, "ling26")
    assert len(out) == 3
    by_id = {r["row_id"]: r for r in out}
    assert by_id["a#1"]["result"] == "success"
    assert by_id["b#1"]["result"] == "" and by_id["c#neg"]["is_replication_paper"] == ""


class _Reply:
    def __init__(self, finish_reason=None, text=""):
        self.text, self.error, self.usage = text, None, {}
        self.raw = {"choices": [{"finish_reason": finish_reason}]} if finish_reason else None


def test_running_out_of_output_tokens_is_reported_as_truncation():
    """A reasoning model can spend the whole budget before emitting JSON; that must
    not be indistinguishable from a model that answered badly."""
    assert ai_coder._hit_the_token_ceiling(_Reply("length")) is True
    assert ai_coder._hit_the_token_ceiling(_Reply("stop", '{"entries": []}')) is False
    assert ai_coder._hit_the_token_ceiling(_Reply()) is False


def test_rerun_replaces_entries_idempotently_and_clears_unmatched_anchors():
    rows = [_row(row_id='p#1', paper_folder='p', original_hint='Original (2000)')]
    data = {'is_replication_paper': 'yes', 'entries': [
        {'original_title': 'Original', 'original_year': '2000', 'result': 'success'},
        {'original_title': 'Other', 'result': 'failure'}]}
    first = ai_coder.fill_sheet(rows, {'p': data}, 'a')
    second = ai_coder.fill_sheet(first, {'p': data}, 'a')
    assert second == first
    zero = ai_coder.fill_sheet(second, {'p': {'is_replication_paper': 'no', 'entries': [], 'why_negative': 'review'}}, 'a')
    assert len(zero) == 1 and zero[0]['result'] == ''
    assert zero[0]['is_replication_paper'] == 'no'
    assert first[0]['result'] == 'success'  # no mutation of input


def test_negative_stratum_positive_entries_are_not_discarded():
    rows = [_row(row_id='p#neg', paper_folder='p', replication_doi='10.1/p')]
    data = {'is_replication_paper': 'yes', 'entries': [{'result': 'success'}, {'result': 'failure'}]}
    out = ai_coder.fill_sheet(rows, {'p': data}, 'a')
    assert len(out) == 3
    assert [r['result'] for r in out if r['result']] == ['success', 'failure']
    assert ai_coder.fill_sheet(out, {'p': data}, 'a') == out


@pytest.mark.parametrize('data', [ {}, {'entries': [], 'is_replication_paper': 'yes'},
    {'entries': [], 'is_replication_paper': 'no'},
    {'entries': [{'result':'success'}], 'is_replication_paper': 'yes'}])
def test_invalid_reply_fails_instead_of_silently_becoming_zero_entries(data):
    with pytest.raises(ValueError):
        ai_coder.validate_reply(data)


def test_explicit_blank_result_is_not_invented_when_paper_has_no_outcome():
    ai_coder.validate_reply({'is_replication_paper': 'yes', 'entries': [
        {'result': '', 'replication_type': 'direct', 'description': 'planned experiment',
         'notes': 'Protocol only; results not reported.'}]})


def test_parseable_but_truncated_reply_is_not_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_coder, 'paper_artifacts', lambda p: {'has_fulltext': True})
    monkeypatch.setattr(ai_coder, 'assemble_input', lambda *args: ('paper text', {}))
    class Backend:
        name, model, max_tokens = 'fake', 'fake', 100
        def complete(self, *args, **kwargs):
            return _Reply('length', '{"is_replication_paper":"no","entries":[],"why_negative":"review"}')
    with pytest.raises(RuntimeError, match='ran out of output tokens'):
        ai_coder.code_paper(tmp_path, Backend(), 'codebook')
