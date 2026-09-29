# Legacy benchmark artifacts (February to August 2026): superseded

Everything in this folder is quarantined. None of these numbers may be quoted as
pipeline accuracy, used as a scoring target, or compared against new runs. This
note explains why, for a reader who did not see the September 2026 audit.

## The ground-truth file was contaminated by the pipeline it was meant to score

`ground_truth_enhanced_V6DERIVED.csv` (189 rows) was built by `improve_ground_truth.py`,
which merged the V6 pipeline's own output into the human ground truth. The file now
carries a `provenance` column, added during the audit: 36 rows are `pipeline:v6`
(rows authored by V6), and each human row carries a `v6_filled_cells` count of the
statistical cells that were empty in the human file and were filled from V6 output
(862 cells in total). Every row still says `validated=yes`, because the script copied
the human row's fields wholesale, validation flag included. That is the bug: pipeline
output was stamped as validated human truth.

The consequence is circular scoring. V6 scores 100% (35/35) on the rows it wrote itself;
V8 scores 87.9% on them. Restricted to human-coded rows only, V6 scores 81.2%
[95% CI 74.2 to 87.5] and V8 77.1% [70.2 to 84.0], overlapping intervals. The published
"V8 regressed" story, and every statistical-accuracy number in `v8_feb2026_metrics.json`,
therefore measures V6-vs-V8 agreement, not accuracy against a human standard.

## The evaluator inflated recall and never measured precision

`evaluate_enhanced.py` (still at `benchmarking/` until the new harness reaches parity
with it) matched ground-truth rows to extracted rows many-to-one: 11 extracted rows were
reused across 25 ground-truth rows. The reported entry recall of 86 to 87% is 79% under
one-to-one matching. Precision was never computed at all: `extra_entries` was hardcoded
to 0, so spurious extractions were invisible.

## The model comparisons were not like-for-like

v6 ran on `claude-sonnet-4-5-20250929` and v8 on `claude-sonnet-4-6`, so the V6/V8
difference confounds prompt changes with a model change. `flora_pilot/` (72.7% agreement,
kappa 0.579, n=33) ran on claude-sonnet-5 for all 37 papers (its debug logs recorded claude-haiku-4-5 for 34 of them because extract.py took the first key of the CLI's modelUsage, which lists an internal Haiku helper of about 15 output tokens before the working model; attribution fixed 2026-09-02). No test-retest run
was ever done, so the noise floor of a single run is unknown; the only estimate is that
v8 and v8_1 agreed on 84.6% of the 13 rows they shared.

## The reports do not describe the files next to them

`v6_performance_report_enhanced.txt` and the now-deleted
`v8_performance_report_enhanced.txt` were byte-identical. `V6_PERFORMANCE_SUMMARY.md`
describes a 226-row ground truth, while the file it refers to had 189 rows.

## What each remaining file is

- `ground_truth_data_filtered.csv` (168 rows) is the human file: 134 rows are a FReD API
  import and 33 were coded by Dan Elton / forrt.org. Its cleaned split now lives in
  `benchmarking/silver/` as `main_gt_fred_api.csv`, `main_gt_human.csv`,
  `main_gt_label_flips.csv`, and `main_gt_dropped.csv`.
- `ground_truth_data_filtered_backup.csv` is the February 6 version of that file. Eight
  `result` labels changed between it and the February 19 file with no recorded
  rationale; those eight rows are the flips file above.
- `ground_truth_data_filtered_small.csv` (98 rows, February 9) is an earlier, smaller
  snapshot of the same human file.
- `ground_truth_corrections_sonnet_test_02_2026.csv` holds 2 adjudicated rows;
  `validate_ground_truth.py` is the tool that produced it.
- `evaluation_results/` and `flora_pilot/` are the raw outputs of the old evaluators.
- `improve_ground_truth.py`, `analyze_v6_errors.py`, `analyze_misidentifications.py`,
  `enhancement_report.txt`, `v6_errors_detailed.json`, and the `*_metrics.json`,
  `*_confusion_matrix.png`, `*_pipeline_evaluation.md`, and
  `*_performance_report_enhanced.txt` files are the scripts and outputs of that cycle,
  kept for the record only.

## Rules going forward

Nothing here may be used as a scoring target. The new harness refuses any ground-truth
row whose `provenance` starts with `pipeline:`. The replacement protocol (blinded human
coding against `benchmarking/codebook.md`, pre-registered agreement thresholds,
one-to-one matching with both precision and recall, a fixed model per comparison, and
test-retest runs) is documented in `benchmarking/README.md`.
