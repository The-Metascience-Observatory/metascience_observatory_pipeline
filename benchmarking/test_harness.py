"""Benchmark harness regression tests (no CLI, no drive). Run:
    cd mo_pipeline && python -m pytest benchmarking/test_harness.py -q
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH.parent))
sys.path.insert(1, str(BENCH))

import harness  # noqa: E402
import gold_build
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


def test_partial_effect_gt_marks_precision_a_lower_bound():
    """No external set codes every effect in a paper, so its extra rows are not FPs."""
    def one(effects_complete):
        gt = harness._gt_row({"row_id": "g", "original_url": "https://doi.org/10.1/orig",
                              "replication_url": "https://doi.org/10.1/rep", "result": "success",
                              "provenance": "human:coder"},
                             source="human:coder", granularity="effect", has_reversal_class=False,
                             effects_complete=effects_complete)
        m = matching.Match(0, 0, "doi", "same_effect", "high", 0.0, "")
        return harness.score_pair(gt, {"original_url": "https://doi.org/10.1/orig", "result": "success"},
                                  m, {"tier": "grobid", "model": "m"})

    entry = {"tp": 1, "fn": 0, "fp": 3, "precision": 0.25, "recall": 1.0}
    partial = harness.summarize([one(False)], harness.Counter(), entry, {})
    complete = harness.summarize([one(True)], harness.Counter(), entry, {})
    assert partial["entry"]["precision_is_lower_bound"] is True
    assert complete["entry"]["precision_is_lower_bound"] is False
    # the caller's entry counter is copied, never mutated (bootstrap resamples share it)
    assert "precision_is_lower_bound" not in entry
    # every external silver set is partial; only gold claims completeness
    assert not any(s.get("effects_complete") for s in harness.SILVER_SPECS.values())


def test_stat_free_arm_says_so_instead_of_printing_a_grid_of_na():
    """A base/core arm emits no statistics by design; a table of n/a reads as a regression."""
    stats = {f: {"gt_has": 7, "ext_has": 0, "both_present": 0, "gt_has_ext_missing": 7,
                 "ext_has_gt_missing": 0, "type_mismatch_unscored": 0,
                 "tier1": None, "tier2": None, "tier3": None} for f in harness.NUMERIC_STATS}
    m = {"entry": {"tp": 1, "fn": 0, "fp": 0, "precision": 1.0, "recall": 1.0, "f1": 1.0},
         "paper_level": {}, "funnel": {}, "result_3class": {"n": 0}, "result_3class_paper": {"n": 0},
         "result_3class_clear": {"n": 0}, "boundary_rows_excluded": 0, "result_4class": {"n": 0},
         "result_all_rows_raw": {"n": 0}, "collapse_applied_n": 0,
         "replication_type": {"n": 0, "n_4class": 0, "n_2class": 3, "accuracy_4class": None,
                              "adjacent_accuracy": None, "kappa_4class": None,
                              "accuracy_2class_direct_or_close": 1.0},
         "original_doi": {"n": 1, "accuracy": 1.0, "ext_missing_rate": 0.0},
         "bibliographic": {"n": 1, "title_ok": 1.0, "authors_ok": 1.0, "year_ok": 1.0, "journal_ok": 1.0},
         "citation_sentence": {"present_rate": 1.0, "author_year_ok_rate": 1.0},
         "statistics": stats}
    prov = {"run": "r", "gt": {"spec": "silver:fred_v242", "files": {}}, "tags": ["t"], "split": "all",
            "model_ids": {}, "ai_versions_in_results": {}, "prompt_version_now": "8.8",
            "git_dirty_prompts": False, "git_commit": "0"*10, "claude_cli_version": "x",
            "harness_version": harness.HARNESS_VERSION, "timestamp_utc": "now", "matcher": {},
            "tier_counts": {}, "prompt_levels": {"base": 25}}

    base = harness.render_report(m, {}, prov, {}, {})
    assert "ran stat-free" in base and "| field | GT has |" not in base
    assert "the ground truth carries: original_n 7" in base
    # the 2-class caveat rides along on the same report
    assert "Only the 2-class number exists here" in base

    prov["prompt_levels"] = {"full": 25}
    full = harness.render_report(m, {}, prov, {}, {})
    assert "| field | GT has |" in full and "ran stat-free" not in full


def test_coding_sheet_can_omit_the_statistical_columns(tmp_path, monkeypatch):
    """--no-stats leaves 9 coded fields, and blinding still holds."""
    frame = [{"replication_doi": "10.1/a", "paper_folder": "10.1--a", "source": "flora", "stratum": "flora:success",
              "in_catalog": "True", "converted": "True", "discipline_group": "psych", "expected_result": "success",
              "expected_type": "", "external_row_ids": "", "note": ""}]
    coding = tmp_path / "coding"
    coding.mkdir()
    harness.write_csv(coding / "frame_gold_v9_UNBLINDED.csv", frame)
    monkeypatch.setattr(harness, "CODING_DIR", coding)
    papers = tmp_path / "papers"
    (papers / "10.1--a").mkdir(parents=True)
    (papers / "10.1--a" / "body.md").write_text("prose")
    monkeypatch.setattr(harness.config, "PAPERS_DIR", papers)

    args = argparse.Namespace(gold_version=9, coder="ai_a", force=True, no_stats=True)
    gold_build.cmd_coding_sheet(args)
    cols = harness.read_csv(coding / "sheet_gold_v9_ai_a.csv")[0].keys()
    assert not (set(cols) - harness.SHEET_ALLOWED), "blinding whitelist must still hold"
    assert not (set(cols) & set(harness.STAT_FIELDS))
    assert "result" in cols and "citation_sentence" in cols

    args.no_stats = False
    gold_build.cmd_coding_sheet(args)
    assert set(harness.STAT_FIELDS) <= set(harness.read_csv(coding / "sheet_gold_v9_ai_a.csv")[0])


def test_provenance_says_ai_consensus_where_no_human_ruled():
    """A row two models agreed on is evidence; only an adjudicated row is 'human:'."""
    adj = {("r1", "result"): {"adjudicated_value": "failure", "adjudicator_id": "dan_elton"},
           ("r2", "result"): {"adjudicated_value": "", "adjudicator_id": ""}}
    assert harness.row_provenance("r1", adj, "ling26", "luna56") == "human:adjudicated"
    assert harness.row_provenance("r2", adj, "ling26", "luna56") == "ai_consensus:ling26+luna56"
    assert harness.row_provenance("r3", adj, "ling26", "luna56") == "ai_consensus:ling26+luna56"
    assert harness.row_provenance("r3", adj, "dan_elton", None) == "single_coder:dan_elton"
    # and none of them trips the pipeline-authored guard
    for rid in ("r1", "r2"):
        assert not harness.row_provenance(rid, adj, "ling26", "luna56").startswith("pipeline:")


def test_coding_sheet_skips_a_paper_that_is_not_on_the_drive(tmp_path, monkeypatch, capsys):
    """Listing an absent paper only earns blank rows someone has to explain later."""
    frame = [{"replication_doi": "10.1/a", "paper_folder": "10.1--a", "source": "flora", "stratum": "s",
              "in_catalog": "True", "converted": "True", "discipline_group": "psych",
              "expected_result": "success", "expected_type": "", "external_row_ids": "", "note": ""},
             {"replication_doi": "10.1/gone", "paper_folder": "10.1--gone", "source": "flora", "stratum": "s",
              "in_catalog": "False", "converted": "False", "discipline_group": "psych",
              "expected_result": "success", "expected_type": "", "external_row_ids": "", "note": ""}]
    coding = tmp_path / "coding"; coding.mkdir()
    harness.write_csv(coding / "frame_gold_v9_UNBLINDED.csv", frame)
    monkeypatch.setattr(harness, "CODING_DIR", coding)
    papers = tmp_path / "papers"
    (papers / "10.1--a").mkdir(parents=True)
    (papers / "10.1--a" / "body.md").write_text("prose")
    monkeypatch.setattr(harness.config, "PAPERS_DIR", papers)

    gold_build.cmd_coding_sheet(argparse.Namespace(gold_version=9, coder="ai_a", force=True, no_stats=True))
    rows = harness.read_csv(coding / "sheet_gold_v9_ai_a.csv")
    assert [r["paper_folder"] for r in rows] == ["10.1--a"]
    assert "skipping 1 paper" in capsys.readouterr().out


def test_added_entries_pair_by_content_not_by_synthetic_id():
    """A synthetic row_id means nothing across sheets: without content pairing the
    comparison silently shrinks to the anchored subset (13 of 32 rows in the pilot)."""
    A = {"p#new1": {"paper_folder": "p", "original_url": "https://doi.org/10.1/x",
                    "original_title": "Ego depletion", "description": "the depletion effect",
                    "result": "failure"},
         "p#new2": {"paper_folder": "p", "original_url": "", "original_title": "Money priming",
                    "description": "priming with money reduces helping", "result": "success"},
         "q#new3": {"paper_folder": "q", "original_url": "https://doi.org/10.1/x",
                    "original_title": "Ego depletion", "description": "same title, other paper",
                    "result": "success"}}
    # B lists them in a different order, and its ids are numbered differently
    B = {"p#new7": {"paper_folder": "p", "original_url": "", "original_title": "Money priming effects",
                    "description": "priming with money reduces helping behaviour", "result": "success"},
         "p#new9": {"paper_folder": "p", "original_url": "https://doi.org/10.1/X",
                    "original_title": "Ego Depletion", "description": "the depletion effect", "result": "failure"}}

    pairs = dict(harness.pair_extra_entries(A, B))
    assert pairs["p#new1"] == "p#new9", "the same effect description establishes the candidate"
    assert pairs["p#new2"] == "p#new7", "otherwise title/description similarity pairs them"
    # a same-titled entry in a DIFFERENT paper must never pair across papers
    assert "q#new3" not in pairs

    # nothing plausible to pair with -> left one-sided for the adjudicator
    assert harness.pair_extra_entries({"z#new1": {"paper_folder": "z", "original_url": "",
                                                  "original_title": "Something else entirely",
                                                  "description": "unrelated"}}, B) == []


def test_same_original_doi_does_not_pair_different_effects():
    base = dict(paper_folder="p", original_url="https://doi.org/10.1/x", original_title="Same paper")
    assert harness.pair_extra_entries({"a": dict(base, description="compassion toward suffering strangers")},
                                      {"b": dict(base, description="numerical working memory accuracy")}) == []
    assert harness.pair_extra_entries({"a": dict(base, description="")}, {"b": dict(base, description="")}) == []


def test_matcher_abstains_on_ambiguous_or_conflicting_study_identity():
    row = dict(paper_folder="p", description="Study 1 tests numerical working memory accuracy")
    assert harness.pair_extra_entries({"a": row}, {"b": row, "c": row}) == []
    assert harness.pair_extra_entries({"a": row}, {"b": dict(row, description=row['description'].replace('1', '2'))}) == []


def test_matching_is_independent_of_labels_and_dois():
    a = dict(paper_folder="p", description="compassion toward suffering strangers", result="success", replication_type="direct")
    b = dict(a, result="failure", replication_type="conceptual", original_url="https://doi.org/10.1/wrong")
    assert harness.pair_extra_entries({"a": a}, {"b": b}) == [("a", "b")]


def test_sheet_ids_cannot_collide_across_coders(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, 'CODING_DIR', tmp_path)
    for coder in ('a', 'b'):
        harness.write_csv(tmp_path / f'sheet_gold_v1_{coder}.csv', [dict(row_id='same-anchor', paper_folder='p', description='x')])
    assert gold_build._read_sheet(1, 'a')[0]['row_id'] != gold_build._read_sheet(1, 'b')[0]['row_id']


def test_paper_level_ground_truth_is_refused_for_scoring(tmp_path, monkeypatch):
    """FLoRa gives one verdict per paper, so it cannot say which effect was right."""
    silver = tmp_path / "silver"
    silver.mkdir()
    rows = [{"replication_doi": "10.1/a", "original_url": "https://doi.org/10.1/o", "result": "success",
             "description": "d", "provenance": "external:flora", "original_title": "T"}]
    harness.write_csv(silver / "flora.csv", rows)
    monkeypatch.setattr(harness, "SILVER_DIR", silver)
    monkeypatch.setattr(harness, "BENCH_DIR", tmp_path)   # info["files"] paths are relative to it

    with pytest.raises(SystemExit) as e:
        harness.load_ground_truth("silver:flora")
    msg = str(e.value)
    assert "paper-level ground truth" in msg and "--allow-paper-level-gt" in msg

    # the escape hatch works, and flags the run so the number cannot be misread
    gt, _, info = harness.load_ground_truth("silver:flora", allow_paper_level=True)
    assert len(gt) == 1 and info["paper_level_gt"] == 1

    # an effect-level silver set is unaffected
    harness.write_csv(silver / "fred_v2_4_2.csv", rows[:1])
    monkeypatch.setitem(harness.SILVER_SPECS, "fred_v242",
                        dict(file="fred_v2_4_2.csv", granularity="effect",
                             has_reversal_class=False, effects_complete=False))
    gt2, _, info2 = harness.load_ground_truth("silver:fred_v242")
    assert len(gt2) == 1 and "paper_level_gt" not in info2


def test_discipline_group_returns_unknown_not_empty_for_a_blank_input():
    """The sampler's DB fallback keys on this: 'unknown' is a truthy string, so any
    'is it missing?' test must name it explicitly or the fallback never fires."""
    assert harness.discipline_group("") == "unknown"
    assert harness.discipline_group(None) == "unknown"
    assert harness.discipline_group("economics") == "socsci"
    assert harness.discipline_group("psychology") == "psych"
    assert harness.discipline_group("basket weaving") == "other"


def test_claim_framing_does_not_stop_two_descriptions_of_one_claim_pairing():
    """One coder writes 'The original study claimed that X', the other writes 'X'."""
    bare = {"description": "Bilinguals show smaller sequential congruency effects than monolinguals"}
    framed = {"description": "The original study claimed that bilinguals show smaller sequential "
                             "congruency effects than monolinguals"}
    author = {"description": "Grundy et al. (2017) reported that bilinguals show smaller sequential "
                             "congruency effects than monolinguals"}
    assert harness.effect_similarity(bare, framed) >= 0.9
    assert harness.effect_similarity(bare, author) >= 0.9
    # the study-number guard still reads the frame: different studies never pair
    s1 = {"description": "Study 1 tested the original claim that preferences form five dimensions"}
    s3 = {"description": "Study 3 tested the original claim that preferences form five dimensions"}
    assert harness.effect_similarity(s1, s3) == 0.0
    # and genuinely different claims stay apart
    other = {"description": "The original study claimed that monolinguals and bilinguals did not "
                            "differ in conventional flanker effects"}
    assert harness.effect_similarity(framed, other) < 0.65


def test_four_class_uses_reversal_capable_sources_not_source_names():
    base = dict(collapse_applied=False, granularity="effect", gt_result="reversal", ext_result_raw="reversal",
                gt_ambiguity="", ext_result="reversal", ext_result_paper="")
    recs = [dict(base, row_id="a", source="prod_slice", has_reversal_class=True, result_ok=True),
            dict(base, row_id="b", source="external:fred_v242", has_reversal_class=False, result_ok=True)]
    gold_like = [r for r in recs if r.get("has_reversal_class")]
    assert [r["row_id"] for r in gold_like] == ["a"]
    import inspect
    assert 'r["source"].startswith("gold")' not in inspect.getsource(harness)


def test_split_all_refuses_ground_truth_with_test_rows(tmp_path, monkeypatch):
    p = tmp_path / "gt.csv"
    harness.write_csv(p, [dict(row_id="r1", replication_url="10.1/x", original_url="10.1/o", result="success",
                               description="d", provenance="human:x", split="test")])
    args = argparse.Namespace(gt=str(p), split="all", allow_dirty_gt=False, allow_paper_level_gt=False,
                              tags="t", run=None, papers_dir=str(tmp_path), release=False, gold_dir=None)
    with pytest.raises(SystemExit) as e:
        harness.cmd_evaluate(args)
    assert "test-split rows" in str(e.value)


def test_two_coder_build_is_refused_even_with_an_empty_b_sheet(tmp_path, monkeypatch):
    monkeypatch.setattr(gold_build, "_frame", lambda gv: [])
    monkeypatch.setattr(gold_build, "_read_sheet", lambda gv, c: [])
    args = argparse.Namespace(gold_version=1, coder_a="a", coder_b="b")
    with pytest.raises(SystemExit) as e:
        gold_build.cmd_build_gold(args)
    assert "two-coder gold export" in str(e.value)
