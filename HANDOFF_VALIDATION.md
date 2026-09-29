# Handoff: benchmarking audit, validation runs, and pipeline fixes

Session of 2026-09-02 → 2026-09-04; this document updated 2026-09-28.
Branch `stat-free-extraction-and-benchmark-fixes` (not pushed).

**Contents**

1. [TL;DR](#1-tldr)
2. [State right now](#2-state-right-now)
3. [The Feb-2026 benchmark audit](#3-the-feb-2026-benchmark-audit)
4. [The rebuilt benchmark](#4-the-rebuilt-benchmark)
5. [Ground-truth diversity](#5-ground-truth-diversity)
6. [Validation runs](#6-validation-runs)
7. [Pipeline audit: fixed](#7-pipeline-audit-fixed)
8. [Pipeline audit: open, ranked](#8-pipeline-audit-open-ranked)
9. [Disproved: do not re-raise](#9-disproved-do-not-re-raise)
10. [Operational gotchas learned](#10-operational-gotchas-learned)
11. [Next steps](#11-next-steps)
12. [File index](#12-file-index)

Other references: `benchmarking/README.md` (protocol),
`benchmarking/results/core_trial100_NOTES.md` (trial and hand audit), and the
plan file `~/.claude/plans/do-a-thoughrough-revuew-groovy-thimble.md` (full audit
findings with line numbers).

---

## 1. TL;DR

- **The published V6/V8 accuracy numbers can't be trusted.** Their ground truth
  was partly written by V6 itself. They are quarantined.
- **A new harness replaces the old evaluators.** It enforces a provenance guard,
  matches one-to-one using a DOI check plus a Haiku judge, and reports precision,
  recall and bootstrap confidence intervals.
- **Validation looks good.** The 100-paper trial was hand-audited: result labels
  were correct 10/10 and the original study was correct 8/10. 600 papers were
  then extracted in base mode (599 succeeded, 435 replication rows, about $265)
  and **ingested on 2026-09-04**.
- **About 20 pipeline bugs were found.** I committed fixes for three
  (`15e59e0`, `11a5df0`), peer sessions fixed four, four suspected ones turned
  out not to be bugs, and the rest are ranked in §8.
- **The catalog has not been rescanned since 2026-09-02.** Until it is, the
  catalog fix in `11a5df0` has no effect on the stored rows.

## 2. State right now

| Item | State |
|---|---|
| Corpus | `/media/dan/data/metascience_observatory_pdfs`. It moved there on 2026-09-05 from the failing USB drive `/media/dan/500Gb`, which is now read-only salvage. |
| Catalog `corpus.sqlite` | **Stale.** Dated 2026-09-02 with 11,163 rows. It lists 0 papers with `base_v88_sonnet_*` tags and 42 papers whose `latest_tag` is still a centrality run. Both of those clear with `python -m mo_pipeline.corpus scan`. |
| Base-run CSVs | Ingested as `replications_database_2026_09_04_174646.csv` (200 run) and `..._184008.csv` (400 run). |
| `mark-ingested` | **Not run** for those two CSVs. The catalog has 0 papers at `ingested` status. |
| My fixes | Committed in `15e59e0` and `11a5df0`, plus this doc in `428d5c9`. |
| Other sessions' uncommitted work | `mo_pipeline/config.py` (the drive move) and `benchmarking/harness.py` (versions 1.2 to 1.4). I deliberately left both alone. |
| Gold set | Other sessions advanced it: the AI coder sheets `sheet_gold_v1_ling26.csv` and `sheet_gold_v1_luna56.csv` and `adjudication_queue_gold_v1.csv` now exist. The human sheet `sheet_gold_v1_dan_elton.csv` still needs coding. |

## 3. The Feb-2026 benchmark audit

`improve_ground_truth.py` built `ground_truth_enhanced.csv` by merging **V6's own
output** into the human ground truth:

- It added 36 rows and filled 862 statistical cells from V6.
- Every row still carried `validated=yes` and a human `validated_person`.

Rescoring by provenance:

| Rows | V6 | V8 |
|---|---|---|
| V6-authored | **100.0%** (35/35) | 87.9% |
| Human-coded only | 81.2% [74.2, 87.5] | 77.1% [70.2, 84.0] |
| As published | 85.3% | 79.3% |

Other defects in the old benchmark:

- **Matching was many-to-one.** Eleven extracted rows were reused across 25
  ground-truth rows, so the reported 87% recall is 79% under one-to-one matching.
- **Precision was never computed.** `extra_entries` was hardcoded to 0.
- **The ground truth had no negative papers,** so the claim of "zero false
  positives" could never have been tested.
- **No confidence intervals and no test-retest.** The only rerun data,
  v8 vs v8_1 on 13 shared rows, agreed 84.6% — as large as the v6-to-v8 gap
  being reported.
- **Models were confounded with prompt versions:** V6 ran on sonnet-4-5, V8 on
  sonnet-4-6, and the July FLoRa pilot on sonnet-5. (The pilot's debug logs said
  Haiku, but that is a CLI attribution artifact, now fixed via `primary_model`.)
- **Artifacts were broken or wrong.** `run_benchmark.py` pointed at a
  nonexistent script, the v8 report was a byte-copy of the v6 report, and the V6
  summary described a 226-row ground truth when the file had 189 rows.

All of it now lives in `benchmarking/archive/legacy_feb2026/`, with a README.
`archive/legacy_feb2026/rescored_2026_09_02/` holds the corrected re-scores.
**The public page `../metascience_observatory_website/content/docs/v6_pipeline_evaluation.md`
still claims "hand-validated ground truth" and needs correcting.**

## 4. The rebuilt benchmark

- **`benchmarking/harness.py`** is the single evaluator. It:
  - refuses any ground-truth row whose `provenance` starts with `pipeline:`;
  - matches rows one-to-one with the Hungarian algorithm;
  - reports precision and recall at both entry and paper level;
  - scores all four `result` values, with an explicit rule for collapsing
    reversal into failure on external sets that lack a reversal class;
  - scores `replication_type`, `citation_sentence`, and statistics against
    tolerance tiers;
  - computes paper-level cluster-bootstrap confidence intervals;
  - writes a provenance block (models, prompt hashes, commit, CLI version) into
    every `metrics.json`;
  - requires `--release` to score the test split, and records each such run in
    `results/test_ledger.jsonl`.
- **Its subcommands** are `import-flora`, `import-fred`, `sample`,
  `coding-sheet`, `agreement`, `adjudicate`, `build-gold`, `status`, `run`
  (which always passes `--dontcheck`), `evaluate`, `retest` and `match-audit`.
- **`benchmarking/matching.py`** matches in two steps: exact DOI equality
  first, then a Haiku judge (`prompts/prompt_match.md` on the Claude CLI)
  for the rest. The judge withholds result labels, caches decisions by content
  hash, and logs them to `match_log.jsonl`. On the FLoRa pilot it made 7 calls
  and identified 3 wrong-original errors that the string matcher could not see.
- **`benchmarking/codebook.md`** is the coder codebook, derived from
  `prompts/prompt_shared_core.md` and versioned as `codebook_v1`.
- **`benchmarking/recall_harness.py`** measures discovery recall; it now has
  `--compare` and a provenance block. The baseline
  `recall_eval/baseline_2026_09_02.json` shows the prefilter fix raised FLoRa
  confirmed recall from 14.7% to 41.0%, while search still never surfaces 52%
  of FLoRa papers.
- **`extract.py` writes `<tag>/provenance.json`** on every run, and gained
  `--force-tier` for tier studies. `citation_sentence` is now collated.

## 5. Ground-truth diversity

| Set | Papers | Effective disciplines | Effective journals | Psychology share |
|---|---|---|---|---|
| Feb-2026 main GT | 145 | 7.0 | 56 | ~80% |
| FLoRa | 524 | 7.3 | 224 | 42% |
| FReD v2.4.2 | 157 | 4.0 | 4.5 | 62% |
| Production DB | 4,815 | 5.9 | 615 | 45% |

Biomedicine makes up 36% of the production DB (medical 21%, neuroscience 9%,
biology 6%) but has almost no human-labelled coverage in any external set. The
gold_v1 sampling frame (352 papers) therefore oversamples it: 106 of its papers
are biomedical. The frame also includes reversal and negative-paper strata.
FReD is the only external set that carries statistics.

## 6. Validation runs

### 100-paper trial: core (single-shot, stat-free)

- **Tag:** `core_v88_sonnet_trial100`
- **List:** `benchmarking/results/core_trial100_include.txt`, a seeded random
  sample of the converted backlog
- **Outcome:** 88 extracted, 12 skipped as already in the DB, 0 failed
- **Cost and time:** $19.77, 9 minutes at 10 workers, no usage-limit pauses

**Checks that passed:**

- All statistical columns were blank.
- `citation_sentence` was present on 75/75 rows and traceable to the source
  text on 74/75.
- Self-reported confidence was spread across levels (high 25, medium 46, low 4).
  The old extractor had reported "high" on 98% of rows.

**Hand audit of 10 rows.** Each paper was read in full. Coders first determined
the original study and the outcome independently, then compared with the
pipeline.

| Check | Result |
|---|---|
| Result label correct | **10/10**, including the run's only `reversal` |
| Original study identified correctly | **8/10**. Both misses are the same paper (Buss 1989, "37 cultures"), which is a theory test rather than a replication, so the extractor invented an original. |
| Blank `original_url` justified | 7/7 (books, chapters, posters, an 1891 monograph) |

**Defects the audit found:**

- **One misattributed statistic.** An explanation quoted t(157) = −9.33 where
  the correct value was t(157) = −7.27. Both values appear in the paper, in
  adjacent sentences with the same degrees of freedom, so only a check of where
  the number sits in the text can catch this. It matters for `--level full`.
- **`replication_type` leans one step too "close".** Three of the ten rows
  should arguably be conceptual.
- **The existing screener disagrees with the extractor.**
  `check_if_replication_study.py` disagreed on 11 of 32 papers, always in the
  same direction, and the extractor was right in 3 of the 4 disagreements that
  were audited.

### Production runs: base (agentic, stat-free), Sonnet, 5 workers

| Tag | Papers | Extracted | With reps | Rows | Cost | Runtime | Limit pauses |
|---|---|---|---|---|---|---|---|
| `base_v88_sonnet_200` | 200 | 200 | 84 | 174 | $90 | 3h59m | 50 papers |
| `base_v88_sonnet_400` | 400 | 399 | 147 | 261 | $174 | 5h02m | 302 papers |

- **Selection:** the converted backlog, minus screened negatives, minus papers
  already in the DB, minus the trial 100. Each candidate was verified on disk to
  have usable full text. The lists are `base200_final_include.txt` and
  `base400b_final_include.txt`.
- **Output:** all 435 rows carry `ai_version 8.8-base`; all statistics are blank;
  `citation_sentence` and `description` are present on 435/435; `original_url`
  is present on 408/435.
- **Result mix:** 262 success, 85 failure, 79 inconclusive, 9 reversal.
- **Failure:** `10.1086/653487`, a silent CLI exit that also failed on retry.
  It can be rerun on its own.
- **Usage limits:** both runs hit the session limit, and the usage guard
  (pause, probe every 30 minutes, retry) recovered them. That is why they ran 4–5×
  longer than my throughput estimate: the estimate did not account for the quota.
- **Rates:** about $0.44 per paper and a median of about 60 seconds per paper,
  against $0.22 per paper for single-shot core.
- **Handling:** the collated CSVs were md5-verified when copied to the website
  data directory, then ingested.

## 7. Pipeline audit: fixed

**By me (committed):**

| Commit | Fix | Measured effect |
|---|---|---|
| `d8dcd5e`* + `15e59e0` | **Stage 5 exclusion of already-published papers was silently off.** It read a deleted 2026-01-28 snapshot behind a bare `exists()` check. It now uses `config.latest_replications_db()`, resolved at run time from `version_history.txt`, warns loudly on a miss, and is included in `python -m mo_pipeline.config`. | 26 of 511 strong-direct candidates are now excluded. |
| `15e59e0` | **The ancestry/SNP close-extension guard could never fire.** Its patterns contained uppercase literals but were matched against lowercased text. They now use `re.IGNORECASE`. | Reclassifies 2 papers, both correct (retests in a Japanese and a Chinese sample). |
| `11a5df0` | **The catalog chose the wrong "latest" run.** It fell back to alphabetical folder order for 670 of 1,647 multi-tag papers (so `sonnetv5` beat `sonnetv6`), and an unanchored glob read centrality output as an extraction, blanking the verdict on 42 papers. Now: result files are anchored to the folder stem, the version is read from the first stamped entry, ties break on `provenance.json` timestamp and then mtime, and a verdict is only overwritten by another verdict. | Needs a `corpus scan` to take effect. |

\* A peer session swept my `config.py` half of this fix into its own commit.

Tests: `mo_pipeline/discover/test_filter_direct_replications.py` (15 new) and 7
new cases in `mo_pipeline/corpus/test_models.py`.

**By peer sessions, found or verified here:**

- `4c84da8`: core-extractor batch detection (five loose PDFs at the `papers/`
  root made it treat the whole corpus as one paper), and `include-list` now
  emits folder names rather than absolute paths.
- `8bae163`: `_reference_matches` required GROBID's parsed year, so it never
  matched on most of the backlog. It now matches against the full entry text.
- `c676d6a`: `get_backend(provider="claude_cli")` was being handed an
  OpenRouter model name. The run then silently classified nothing and exited 0.
- `primary_model`: debug logs now attribute a run to the model that did the
  work, rather than to the CLI's Haiku helper.

## 8. Pipeline audit: open, ranked

| # | Issue | Where | Notes / fix |
|---|---|---|---|
| 1 | **Stage 4 marks every paper on the drive as "replication, high confidence".** The manifest query has no `WHERE`, and `_auto_row` hardcodes `high`. | `discover/build_processed_manifest.py:38`, `classify_candidates.py:181,292` | 4,238 rows, 36% of `confirmed_replications.csv`, including 2,282 known non-replications. Almost no wasted downloads (the papers are already on disk), but the "confirmed" set can't be trusted. Fix: restrict to `contains_replications = 1`. Decide whether to regenerate the CSV. |
| 2 | **Two fetch paths run without the Elsevier key.** `fetchpdf` binds the key when it is imported, and only stage 6 imports `fetchpdf_grey` first. | `corpus/backfill.py:109`, `discover/filter_direct_replications.py:24` | Elsevier supplies 428 of 551 corpus XMLs. Fix: a single `shared/fetch.py` shim, plus a subprocess test for each entry point. |
| 3 | **A stopped or killed stage reports `finished`.** The exit sentinel is written inside the process that receives the signal. | `server/app/runner.py:119` | The dashboard shows a stopped 6-hour run in green. Fix: have `stop()` write the sentinel, or trap the signal. |
| 4 | **Search records a failed query as completed.** A 429 returns `[]` and is checkpointed; `--max-per-query` is not part of the checkpoint key; the S2 delay is not applied between queries or on retry. | `search_for_replication_studies.py:384-400, 389` | Failed queries are never retried. |
| 5 | **255 classifications put the replication type in the `confidence` column.** Stage 5 gates on `confidence == "high"`, so these rows are unreachable. | `classify_candidates.py:363-370` | Validate enum values on write. |
| 6 | **A killed extraction can leave a bare `result.json`,** which a later failed run silently adopts as its output. | `extract.py:1315` | Delete any stale `result.json` before invoking the agent. |
| 7 | **Stage 6 and stage 7 share no mutex,** and there is no filesystem lock. Convert can move PDFs out from under an active download. | `server/app/registry.py:336-346` | |
| 8 | **Probing the inbox takes minutes, so `server/tests` hangs.** The tests also write pid files into live state because `MO_STATE_DIR` is not isolated. | `registry.py:127-151`, no `conftest.py` | Phantom `pytest_*` stages appear in `dev.sh status`. |
| 9 | **`load_version_number` parses the version as a float.** "8.10" becomes 8.1 and "9.0" becomes 9. | `extract.py:437` | Latent until a version like 8.10 or 9.0 ships. |
| 10 | **`DELETE /doi-runs/{slug}` accepts path traversal.** | `main.py:301`, `doi_runs.delete_run` | **Latent:** a `meta.json` guard blocks it today. CORS allows `*`, so harden it anyway. |
| 11 | **`code_fingerprint` omits `corpus/render.py`,** which produces the primary input tier. | `version.py` | The render file is owned by another session. |
| 12 | **Classification enums, the dedup logic and `aux/` need work.** Dedup is not union-find (duplicate screening calls), and `discover/aux/` is dead with hardcoded paths. | various | Housekeeping. |
| 13 | **The two QC scripts are unbenchmarked.** They are unwired, use a divergent four-class taxonomy (violating invariant 3), and showed a 34% false-negative rate on the trial. The sanity-check prompt leads the model. | `extract/check_if_replication_study.py`, `sanity_check_if_original_study_is_right.py` | Do not gate on either until they are benchmarked against gold. |
| 14 | **Documentation drift.** `CLAUDE.md` gives `fetchpdf_grey`'s location as `../fetchpdf`; it is actually `../fetchpdf-grey`. The stage-6 flag list is incomplete. | `CLAUDE.md:15`, `README.md:32` | |

## 9. Disproved: do not re-raise

- **"The Elsevier key is lost in stage 6."** It isn't: stage 6 imports the grey
  wrapper first, and `source_counts.json` shows `elsevier_tdm: 44` hits.
- **"`source_counts.json` is malformed."** It isn't. It is nested under a
  `source_counts` key.
- **"Re-classifying drops earlier verdicts."** Zero identities were lost against
  either backup.
- **"`normalize_doi_url` mis-skips papers."** Zero extra matches under a looser
  normalisation, by two independent methods. The only residual risk is one row
  whose folder is not on the drive.
- **"The FLoRa pilot ran on Haiku."** It ran on sonnet-5. The debug logs
  recorded the CLI's Haiku helper, which the peer's `primary_model` fix
  addressed.

## 10. Operational gotchas learned

- **pytest:** always run it as
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest …`. Without that, a hydra
  plugin crashes it on Python 3.12.
- **`_husk_reason` returns `""` for a healthy folder, not `None`.** My first
  400-paper launch selected 0 papers because I tested `is None`.
- **Benchmark extractions need `--dontcheck`,** because every FLoRa and FReD DOI
  is already in the production DB. Production runs should leave the default
  skip on.
- **Hitting the usage limit costs time, not work.** The guard pauses and
  resumes, so budget wall-clock time for it, not just throughput.
- **Several sessions edit this repo at once.** Check `ListAgents`, diff before
  editing, and don't commit other sessions' hunks unless you mean to. One
  session swept my `config.py` change into its commit.
- **Import order decides whether the Elsevier key is present.** `fetchpdf` binds
  `ELSEVIER_TDM_API_KEY` at import time, so `fetchpdf_grey` must be imported
  first.

## 11. Next steps

1. `python -m mo_pipeline.corpus scan`, so `11a5df0` takes effect: the 42
   centrality papers get real verdicts and the 670 papers get the correct
   latest run.
2. `python -m mo_pipeline.corpus mark-ingested <csv> --db-version …` for both
   ingested base CSVs.
3. Fix open items 1 and 2 (the confirmed-set integrity and the Elsevier import
   order), then 3 and 6.
4. Rerun the one failed paper, `10.1086/653487`, under `base_v88_sonnet_400`.
5. Gold set: code `sheet_gold_v1_dan_elton.csv`, work through the adjudication
   queue, run `harness.py build-gold`, and publish a first real benchmark.
6. Correct the public V6 evaluation page.
7. Before trusting `--level full` statistics, rerun
   `core_trial100_include.txt` in full mode, count how many numeric fields are
   populated, then build a check that each number comes from the passage about
   that specific result, tuned on labelled rows.
8. Decide whether to run the rest of the backlog: about 2,480 papers remain
   eligible under the same selection, at roughly $0.44 per paper in base mode.

## 12. File index

| Path | What it is |
|---|---|
| `benchmarking/harness.py`, `matching.py`, `test_harness.py` | Evaluator, matcher, and tests |
| `benchmarking/codebook.md`, `README.md` | Codebook and protocol |
| `benchmarking/silver/*.csv` | External label sets, each with a `provenance` column |
| `benchmarking/gold/coding/` | Sampling frame, coder sheets, adjudication queue |
| `benchmarking/archive/legacy_feb2026/` | Quarantined Feb-2026 material and the corrected re-scores |
| `benchmarking/results/core_trial100_*` | Trial list, log, notes, and statistic suspects |
| `benchmarking/results/base{200,400b}_final_include.txt`, `base{200,400b}.log` | Production-run lists and logs |
| `benchmarking/recall_eval/baseline_2026_09_02.json` | Discovery-recall baseline |
| `prompts/prompt_match.md`, `version_match.txt` | Matcher judge prompt |
| `papers/<doi>/{core_v88_sonnet_trial100,base_v88_sonnet_200,base_v88_sonnet_400}/` | Per-paper results, with `debug_log.json` and `provenance.json` |
| `papers/collated_results_base_v88_sonnet_{200,400}.csv` | Collated outputs, also copied into the website data directory |
