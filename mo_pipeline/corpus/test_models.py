"""DOI <-> folder encoding regression tests (this encoding has regressed twice:
the multi-slash decode, and the colon collision). Run:
    cd mo_pipeline && python -m pytest mo_pipeline/corpus/test_models.py -q
"""
from mo_pipeline.corpus.models import doi_to_folder, folder_to_doi

# Real-world DOI shapes, especially the ones that broke before.
CASES = [
    "10.1001/archneurol.2010.292",     # ordinary single-slash
    "10.1093/jpepsy/jsy104",           # multi-slash (OSF / OUP style)
    "10.1023/a:1018769825030",         # colon (old Springer/Kluwer) — must round-trip
    "10.1023/a:1021350122677",         # colon that previously decoded to a slash
    "10.3758/s13428-021-01694-3",      # literal hyphens must be preserved
    "10.17605/osf.io/e9d3k",           # OSF triple segment
    # Ancient Wiley SICI DOI: contains ':' (incl. '::'), '<', and '>' — all NTFS-forbidden.
    "10.1002/1099-0879(200007)7:3<220::aid-cpp243>3.0.co;2-f",
    "10.18260/1-2--47556",              # ASEE: literal '--' must not decode to '/'
    "10.1037//0002-9432.71.3.379",     # old APA double-slash DOI
    "10.1234/a-/b",                    # hyphen adjacent to slash (hypothetical)
    "10.1044/cicsd_28_f_98",           # literal underscores stay raw
]

# Chars NTFS/exFAT forbid in filenames (the folder name must contain none of them).
_NTFS_FORBIDDEN = set('<>:"|?*\\')


def test_doi_folder_roundtrip():
    for doi in CASES:
        folder = doi_to_folder(doi)
        assert not (_NTFS_FORBIDDEN & set(folder)), \
            f"forbidden char leaked into folder name for {doi}: {folder}"
        assert folder_to_doi(folder) == doi, f"round-trip failed for {doi}"


def test_colon_encodes_to_tilde_not_slash():
    # The historical bug: ':' and '/' both became '--', so a colon DOI decoded
    # to a slash DOI. They must stay distinct.
    assert doi_to_folder("10.1023/a:123") == "10.1023--a~123"
    assert folder_to_doi("10.1023--a~123") == "10.1023/a:123"
    assert folder_to_doi("10.1023--a--123") == "10.1023/a/123"  # slash stays slash


def test_dedup_suffix_stripped():
    assert folder_to_doi("10.1001--foo (1)") == "10.1001/foo"


def test_trailing_whitespace_stripped():
    # Two real corpus folders had trailing U+00A0 / U+2009 in their names.
    assert folder_to_doi("10.1037--0022-3514.80.4.557\xa0") == "10.1037/0022-3514.80.4.557"
    assert folder_to_doi("10.1073--pnas.2015539118 ") == "10.1073/pnas.2015539118"


def test_literal_double_hyphen_not_confused_with_slash():
    # ASEE DOIs contain a literal '--'; it must stay distinct from the '/' encoding.
    assert doi_to_folder("10.18260/1-2--47556") == "10.18260--1-2~2d~~2d~47556"
    assert folder_to_doi("10.18260--1-2~2d~~2d~47556") == "10.18260/1-2--47556"
    # Old APA '//' DOIs encode to '----' and still round-trip.
    assert doi_to_folder("10.1037//0002-9432.71.3.379") == "10.1037----0002-9432.71.3.379"
    assert folder_to_doi("10.1037----0002-9432.71.3.379") == "10.1037//0002-9432.71.3.379"


# ── Which extraction run the catalog treats as a paper's current answer ──────
#
# Three regressions, all silent, all fixed by the same rewrite of
# `_tag_findings`/`_key`:
#   * an unanchored `*_result.json` glob also matched the claim-centrality
#     pilot's `centrality_result.json`, so a centrality run was read as an
#     extraction and blanked the paper's verdict;
#   * `ai_version` was read from `reps[0]` alone, so it was absent for every
#     "no replications" result, collapsing the ordering key to the alphabetical
#     order of the tag folder names (sonnetv5 "beat" sonnetv6);
#   * the verdict was overwritten unconditionally, so a result with no
#     `contains_replications` key erased one derived from screening.

import json as _json

from mo_pipeline.corpus.models import scan_folder


def _paper(tmp_path, stem="10.1234--foo"):
    d = tmp_path / stem
    d.mkdir()
    # 'converted' = readable full text: abstract.md + body.md, or a rendition.
    (d / "abstract.md").write_text("an abstract")
    (d / "body.md").write_text("x" * 200)
    return d


def _run(folder, tag, *, suffix="_result_full.json", reps=None,
         contains=True, mtime=None, provenance_ts=None):
    t = folder / tag
    t.mkdir()
    payload = {"contains_replications": contains, "replications": reps or []}
    f = t / f"{folder.name}{suffix}"
    f.write_text(_json.dumps(payload))
    if provenance_ts:
        (t / "provenance.json").write_text(_json.dumps({"timestamp": provenance_ts}))
    if mtime:
        import os
        os.utime(f, (mtime, mtime))
    return t


def test_centrality_output_is_not_an_extraction(tmp_path):
    """The exact shape that marked 42 papers 'extracted' with a blank verdict."""
    d = _paper(tmp_path)
    t = d / "centrality_pilot_a"
    t.mkdir()
    (t / "centrality_result.json").write_text(_json.dumps({"labels": []}))
    p = scan_folder(d)
    assert p.tags == []
    assert p.status == "converted"          # still queueable, not 'extracted'
    assert p.latest_tag is None


def test_result_must_be_named_after_the_paper_folder(tmp_path):
    d = _paper(tmp_path)
    t = d / "sometag"
    t.mkdir()
    (t / "someone_elses_result.json").write_text(_json.dumps({"contains_replications": True}))
    assert scan_folder(d).tags == []


def test_newer_run_wins_when_neither_carries_a_version(tmp_path):
    """sonnetv5 vs sonnetv6: alphabetical order used to pick the older run."""
    d = _paper(tmp_path)
    _run(d, "sonnetv5", contains=False, mtime=1_000_000)
    _run(d, "sonnetv6", contains=True, mtime=2_000_000)
    assert scan_folder(d).latest_tag == "sonnetv6"


def test_provenance_timestamp_outranks_mtime(tmp_path):
    d = _paper(tmp_path)
    _run(d, "aaa_old", mtime=9_000_000, provenance_ts="2020-01-01T00:00:00")
    _run(d, "zzz_new", mtime=1_000_000, provenance_ts="2026-01-01T00:00:00")
    assert scan_folder(d).latest_tag == "zzz_new"


def test_ai_version_still_outranks_recency(tmp_path):
    d = _paper(tmp_path)
    _run(d, "new_but_older_prompt", reps=[{"ai_version": "8.5"}], mtime=2_000_000)
    _run(d, "old_but_newer_prompt", reps=[{"ai_version": "8.8"}], mtime=1_000_000)
    assert scan_folder(d).latest_tag == "old_but_newer_prompt"


def test_ai_version_read_from_first_entry_that_has_one(tmp_path):
    d = _paper(tmp_path)
    _run(d, "mixed", reps=[{"result": "success"}, {"ai_version": "8.8"}])
    assert scan_folder(d).ai_version == "8.8"


def test_a_verdictless_result_does_not_erase_the_screening_verdict(tmp_path):
    d = _paper(tmp_path)
    (d / "replication_check.json").write_text(
        _json.dumps({"contains_replications": True, "confidence": "high"}))
    t = d / "odd_run"
    t.mkdir()
    (t / f"{d.name}_result_full.json").write_text(_json.dumps({"replications": []}))
    assert scan_folder(d).contains_replications is True
