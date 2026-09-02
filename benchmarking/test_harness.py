"""Benchmark harness regression tests (no CLI, no drive). Run:
    cd mo_pipeline && python -m pytest benchmarking/test_harness.py -q
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH.parent))
sys.path.insert(1, str(BENCH))

import harness  # noqa: E402
import matching  # noqa: E402


def _gt(**kw):
    base = {"row_id": "g", "original_url": "", "original_title": "", "original_authors": "", "original_year": "",
            "original_journal": "", "description": "", "source": "t"}
    base.update(kw)
    return base


def test_doi_match_is_one_to_one_and_extra_rows_are_fp():
    gt = [_gt(row_id="g1", original_url="https://doi.org/10.1037/a0001"),
          _gt(row_id="g2", original_url="https://doi.org/10.1037/a0001", description="study 2 effect on memory")]
    ext = [{"original_url": "https://doi.org/10.1037/A0001", "description": "study 1 effect on speed"},
           {"original_url": "https://doi.org/10.1037/a0001", "description": "study 2 effect on memory"},
           {"original_url": "https://doi.org/10.9999/other", "description": "unrelated"}]
    o = matching.assign(gt, ext)
    assert sorted((m.gt_index, m.ext_index) for m in o.matches) == [(0, 0), (1, 1)]
    assert o.ext_unmatched == [2] and o.gt_unmatched == []
    # the same extracted row can never satisfy two GT rows
    o2 = matching.assign(gt, ext[:1])
    assert len(o2.matches) == 1 and len(o2.gt_unmatched) == 1


def test_fuzzy_fallback_needs_two_of_three():
    gt = [_gt(original_title="Elderly priming slows walking", original_authors="Bargh, J.", original_year="1996")]
    ext_ok = [{"original_url": "", "original_title": "elderly priming slows walking speed", "original_authors": "Bargh, John; Chen, M.", "original_year": "1996"}]
    ext_bad = [{"original_url": "", "original_title": "Something else entirely", "original_authors": "Nobody", "original_year": "1996"}]
    assert matching.assign(gt, ext_ok).matches[0].method == "fuzzy"
    assert matching.assign(gt, ext_bad).matches == []


def test_cached_judge_decides_without_cli(tmp_path):
    gt = [_gt(row_id="g1", original_title="Alpha", original_authors="A, B", original_year="2000", description="alpha effect")]
    ext = [{"original_url": "", "original_title": "Totally different", "original_authors": "Z", "original_year": "1990", "description": "x"},
           {"original_url": "", "original_title": "Alpha (reprint)", "original_authors": "A, B", "original_year": "2000", "description": "alpha effect"}]
    judge = matching.MatchJudge(offline=True, cache_dir=tmp_path)
    key = judge._key(gt[0], ext, "effect")
    (tmp_path / f"{key}.json").write_text(json.dumps({"match": 1, "relation": "same_effect", "confidence": "high", "reason": "cached"}))
    o = matching.assign(gt, ext, judge=judge)
    assert [(m.gt_index, m.ext_index, m.method) for m in o.matches] == [(0, 1, "llm")]
    assert judge.calls == 0 and judge.cache_hits == 1


def test_judge_validation_rejects_garbage():
    j = matching.MatchJudge(offline=True)
    assert j._validate({"match": 7, "relation": "same_effect", "confidence": "high"}, 2)["relation"] == "none"
    assert j._validate({"match": "1", "relation": "subanalysis_of_gt", "confidence": "HIGH"}, 2)["match"] == 1
    assert j._validate(None, 2)["error"]


def test_collapse_policy_only_where_source_lacks_reversal():
    m = matching.Match(0, 0, "doi", "same_effect", "high", 0.0)
    ext = {"result": "reversal", "replication_type": "direct", "original_url": ""}
    gt_ext = harness._gt_row({"row_id": "a", "replication_url": "https://doi.org/10.1/x", "result": "failure", "provenance": "external:flora"},
                             source="external:flora", granularity="paper", has_reversal_class=False)
    gt_gold = harness._gt_row({"row_id": "b", "replication_url": "https://doi.org/10.1/x", "result": "reversal", "provenance": "human:adjudicated"},
                              source="gold", granularity="effect", has_reversal_class=True)
    r1 = harness.score_pair(gt_ext, ext, m, {})
    r2 = harness.score_pair(gt_gold, ext, m, {})
    assert r1["collapse_applied"] and r1["ext_result"] == "failure" and r1["result_ok"]
    assert not r2["collapse_applied"] and r2["ext_result"] == "reversal" and r2["result_ok"]


def test_stat_tiers_are_type_aware():
    gt = {"replication_es": "0.50", "replication_es_type": "d", "replication_n": "100", "replication_p_value": "0.03", "replication_p_value_type": "="}
    ext = {"replication_es": "0.25", "replication_es_type": "r", "replication_n": "104", "replication_p_value": "0.03", "replication_p_value_type": "="}
    assert harness._score_stat("replication_es", gt, ext).get("type_mismatch")
    n = harness._score_stat("replication_n", gt, ext)
    assert n["both"] and not n["tier1"] and n["tier2"] and n["tier3"]
    p = harness._score_stat("replication_p_value", gt, ext)
    assert p["tier1"] and p["tier3"]


def test_result_metrics_and_kappa():
    pairs = [("success", "success"), ("failure", "failure"), ("inconclusive", "success"), ("success", "success")]
    m = harness.result_metrics(pairs, ("success", "failure", "inconclusive"))
    assert m["n"] == 4 and abs(m["accuracy"] - 0.75) < 1e-9
    assert m["per_class"]["inconclusive"]["recall"] == 0.0 and m["per_class"]["success"]["precision"] == 2 / 3
    assert 0 < m["cohen_kappa"] < 1


def test_bootstrap_is_deterministic():
    recs = []
    for i in range(12):
        recs.append({"replication_doi": f"10.1/{i % 4}", "gt_result": "success" if i % 3 else "failure",
                     "ext_result": "success" if i % 2 else "failure", "ext_result_raw": "success" if i % 2 else "failure",
                     "result_ok": (i % 3 != 0) == (i % 2 != 0), "source": "gold", "granularity": "effect",
                     "collapse_applied": False, "type_scored": False, "doi_scored": False, "bib_scored": False,
                     "cit_present": False, "stats": {f: {"gt_has": False, "ext_has": False, "both": False} for f in harness.NUMERIC_STATS}})
    a = harness.cluster_bootstrap(recs, {}, {}, {}, n_boot=200, seed=0)
    b = harness.cluster_bootstrap(recs, {}, {}, {}, n_boot=200, seed=0)
    assert a == b and "result_3class.accuracy" in a


def test_provenance_guard_refuses_pipeline_rows(tmp_path):
    p = tmp_path / "gt.csv"
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["row_id", "replication_url", "original_url", "result", "provenance"])
        w.writeheader()
        w.writerow({"row_id": "1", "replication_url": "https://doi.org/10.1/a", "original_url": "https://doi.org/10.1/b", "result": "success", "provenance": "pipeline:v6"})
    with pytest.raises(SystemExit):
        harness.load_ground_truth(str(p))
    rows, negs, info = harness.load_ground_truth(str(p), allow_dirty=True)
    assert info["gt_dirty"] and rows[0]["provenance"] == "pipeline:v6"


def test_split_hash_is_deterministic_and_disjoint():
    dois = [f"10.1/{i}" for i in range(500)]
    s1 = {d: harness.split_of(d, "salt") for d in dois}
    s2 = {d: harness.split_of(d, "salt") for d in dois}
    assert s1 == s2
    test = sum(1 for v in s1.values() if v == "test")
    assert 0.5 < test / 500 < 0.7
    assert not (set(d for d, v in s1.items() if v == "test") & set(d for d, v in s1.items() if v == "dev"))


def test_sheet_columns_are_whitelisted():
    forbidden = {"confidence", "explanation", "ai_version", "expected_result", "validated"}
    assert not (forbidden & harness.SHEET_ALLOWED)
    assert "result" in harness.SHEET_ALLOWED and "gt_ambiguity" in harness.SHEET_ALLOWED


# ── GT corrections sidecar, DOI aliases, paper aggregate, document identity ──

def _corrections(tmp_path, rows):
    p = tmp_path / "x_corrections.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["row_id", "action", "field", "value", "evidence", "date", "by"])
        w.writeheader()
        for r in rows:
            w.writerow({"evidence": "e", "date": "2026-09-02", "by": "t", **r})
    return p


def test_gt_corrections_set_drop_and_flag(tmp_path):
    raws = [{"row_id": "a", "original_url": "https://doi.org/10.9999/wrong", "original_title": "Wrong paper",
             "provenance": "external:x"},
            {"row_id": "b", "provenance": "external:x"},
            {"row_id": "c", "provenance": "external:x"}]
    p = _corrections(tmp_path, [
        {"row_id": "a", "action": "set", "field": "original_url", "value": "https://doi.org/10.1234/right"},
        {"row_id": "a", "action": "set", "field": "original_title", "value": "Right paper"},
        {"row_id": "b", "action": "drop"},
        {"row_id": "c", "action": "flag", "field": "gt_ambiguity", "value": "boundary"},
    ])
    out, n = harness.apply_gt_corrections(raws, p)
    assert n == 4 and [r["row_id"] for r in out] == ["a", "c"]
    a = out[0]
    assert a["original_title"] == "Right paper"
    # both spellings of the DOI stay in step, so _gt_row sees the correction
    assert a["original_doi_norm"] == "10.1234/right"
    # provenance is stamped but still not pipeline-authored, so the guard passes
    assert a["provenance"] == "external:x;corrected:2026-09-02"
    assert not a["provenance"].startswith("pipeline:")
    assert out[1]["gt_ambiguity"] == "boundary"
    # untouched rows and a missing sidecar are both no-ops
    assert harness.apply_gt_corrections(raws, tmp_path / "absent.csv") == (raws, 0)


def test_canonical_doi_follows_one_hop_and_passes_unknowns_through():
    # the three aliases this pilot proved, from benchmarking/doi_aliases.json
    assert matching.canonical_doi("10.2307/329894") == "10.1111/j.1540-4781.1992.tb02573.x"
    assert matching.canonical_doi("https://doi.org/10.31235/OSF.IO/93eyz") == "10.1016/j.ssresearch.2015.12.008"
    assert matching.canonical_doi("10.7554/eLife.10012") == "10.1101/gr.126516.111"
    assert matching.canonical_doi("10.1234/never.seen") == "10.1234/never.seen"
    assert matching.canonical_doi("") == ""
    # a canonical DOI is its own canonical form: no chains
    assert matching.canonical_doi("10.1101/gr.126516.111") == "10.1101/gr.126516.111"


def test_alias_lets_a_registered_report_match_the_study_it_protocols():
    gt = [_gt(row_id="g1", original_url="https://doi.org/10.7554/eLife.10012",
              original_title="Registered report: Fusobacterium nucleatum infection is prevalent")]
    ext = [{"original_url": "https://doi.org/10.1101/gr.126516.111",
            "original_title": "Fusobacterium nucleatum infection is prevalent in human colorectal carcinoma"}]
    o = matching.assign(gt, ext)
    assert [(m.gt_index, m.ext_index) for m in o.matches] == [(0, 0)]
    assert o.matches[0].method == "doi" and "same work under two DOIs" in o.matches[0].reason


def test_paper_aggregate():
    agg = harness.paper_aggregate
    assert agg(["success"]) == "success"
    assert agg(["success", "success"]) == "success"
    # a paper whose effects went both ways is what "inconclusive" means
    assert agg(["success", "success", "failure"]) == "inconclusive"
    assert agg(["failure", "success", "success", "success", "success"]) == "inconclusive"
    assert agg(["success", "inconclusive"]) == "success"
    assert agg(["success", "inconclusive", "inconclusive"]) == "inconclusive"
    assert agg(["reversal", "success"]) == "inconclusive"
    assert agg(["inconclusive"]) == "inconclusive"
    assert agg([]) == "" and agg(["", ""]) == ""


def test_document_identity_flags_the_wrong_paper_but_not_a_real_one(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "abstract.md").write_text("# Working hours and carbon dioxide emissions\n\nWe replicate...")
    (real / "body.md").write_text("# Working hours and carbon dioxide emissions\n\n" + "prose. " * 500)
    data = {"replication_metadata": {"title": "Working Hours and Carbon Dioxide Emissions in the United States"}}
    assert harness._document_identity(real, data) == ""

    wrong = tmp_path / "wrong"
    wrong.mkdir()
    (wrong / "abstract.md").write_text("# PERFORMANCE HETEROGENEITY UNDER UNCERTAINTY: SIX EMPIRICAL STUDIES\n\nThis thesis explores...")
    (wrong / "body.md").write_text("This thesis explores the strategy process. " * 200)
    why = harness._document_identity(wrong, {}, gt_title="Friends or strangers? A replication and extension of Beckman")
    assert "does not match" not in why and "expected" in why

    # with nothing to compare against, never guess
    assert harness._document_identity(wrong, {}) == ""


def test_score_pair_accepts_an_aliased_doi():
    gt = harness._gt_row({"row_id": "g", "original_url": "https://doi.org/10.2307/329894",
                          "replication_url": "https://doi.org/10.1/rep", "result": "success",
                          "provenance": "external:flora"},
                         source="external:flora", granularity="paper", has_reversal_class=False)
    ext = {"original_url": "https://doi.org/10.1111/j.1540-4781.1992.tb02573.x", "result": "success"}
    m = matching.Match(0, 0, "doi", "same_effect", "high", 0.0, "")
    rec = harness.score_pair(gt, ext, m, {"tier": "grobid", "model": "m"})
    assert rec["doi_ok"] is True and rec["doi_alias_used"] is True
