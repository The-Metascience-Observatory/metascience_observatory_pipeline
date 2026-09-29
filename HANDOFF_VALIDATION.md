# Handoff: benchmarking audit, validation runs, and pipeline fixes

Session of 2026-09-02 to 2026-09-04 (written 2026-09-28). Covers four things:
an audit of how extraction was benchmarked, a rebuilt benchmark harness, three
production extraction runs with hand validation, and a pipeline bug audit with
three fixes applied. State as of writing is recorded at the end.

**Read first:** `benchmarking/README.md` (the protocol) and
`benchmarking/results/core_trial100_NOTES.md` (the validation write-up).

---

## 1. The Feb-2026 benchmark numbers are unusable

Every accuracy claim the Observatory published for V6/V8 came from
`ground_truth_enhanced.csv`, which `improve_ground_truth.py` built by merging the
**V6 pipeline's own output** into the human ground truth: 36 rows and 862
statistical cells were written by V6, yet every row still said `validated=yes`.

| | V6 | V8 |
|---|---|---|
| on the V6-authored rows | **100.0%** | 87.9% |
| on human-coded rows only | 81.2% [74.2, 87.5] | 77.1% [70.2, 84.0] |
| as published | 85.3% | 79.3% |

Other defects: matching was many-to-one (reported 87% entry recall is 79%
one-to-one), precision was never computed, no confidence intervals, no
test-retest, and each headline number came from a different model. Ground truth
was ~80% psychology and single-coded, while the production DB is 36% biomedical.

Everything from that era is quarantined in `benchmarking/archive/legacy_feb2026/`
with a README. The public page
`../metascience_observatory_website/content/docs/v6_pipeline_evaluation.md` still
repeats the "hand-validated ground truth" claim and should be corrected.

## 2. The rebuilt benchmark

- `benchmarking/harness.py` — one evaluator: refuses ground truth whose
  `provenance` starts with `pipeline:`; one-to-one Hungarian matching; precision
  and recall at entry and paper level; 4-value `result` (reversal is first-class);
  cluster-bootstrap CIs; a provenance block in every `metrics.json`; a test-split
  release gate with a ledger; and the gold-set toolchain (`sample`,
  `coding-sheet`, `agreement`, `adjudicate`, `build-gold`, `run`, `retest`,
  `match-audit`).
- `benchmarking/matching.py` — DOI equality, then a Haiku judge
  (`prompts/prompt_match.md`) on the Claude CLI for the residual, cached by
  content hash.
- `benchmarking/codebook.md`, `silver/` (FLoRa, FReD v2.4.2, split Feb-2026 GT),
  `gold/coding/` (a 352-paper gold_v1 sampling frame and a 413-row blinded coding
  sheet awaiting a human coder).
- `extract.py` now writes `<tag>/provenance.json` on every run.

## 3. Validation runs

### 100-paper trial (core / single-shot, stat-free)
Tag `core_v88_sonnet_trial100`, seeded random sample of the converted backlog.
88 extracted, 12 skipped as already in the DB, 0 failed; $19.77; 9 min at 10
workers. All statistical columns blank as intended; `citation_sentence` present
75/75 and traceable to the paper text 74/75.

**Hand audit of 10 rows** (stratified toward risky cases, each paper read in full):

| | |
|---|---|
| replication result labelled correctly | **10/10** (including the one `reversal`) |
| original study identified correctly | **8/10** |
| blank `original_url` justified | 7/7 (books, chapters, posters, an 1891 monograph) |

Both misses are one paper, Buss 1989 (37 cultures), a theory test rather than a
replication; the extractor invented an original. Other defects found: one
explanation quoted a t-statistic from the adjacent sentence (same df, so only a
locality check can catch it — matters for `--level full`); `replication_type`
skews a notch too "close".

### Production runs (base / agentic, stat-free, Sonnet, 5 workers)

| tag | papers | extracted | with reps | rows | cost | runtime |
|---|---|---|---|---|---|---|
| `base_v88_sonnet_200` | 200 | 200 | 84 | 174 | $90 | 3h 59m |
| `base_v88_sonnet_400` | 400 | 399 | 147 | 261 | $174 | 5h 02m |

Selection excluded screened negatives, everything already in the DB, the trial
100, and folders without usable full text. Both runs hit the Claude session limit
(50 and 302 papers paused) and the usage guard recovered them; that is why they
ran 4-5x my throughput ETA. One failure: `10.1086/653487` (silent CLI exit).
Both collated CSVs were copied (md5-verified) to the website data dir and
**ingested on 2026-09-04** (`replications_database_2026_09_04_174646.csv` and
`..._184008.csv`).

## 4. Pipeline audit

Full findings with line numbers and measured counts are in the plan file
`~/.claude/plans/do-a-thoughrough-revuew-groovy-thimble.md`.

### Fixed in this session — UNCOMMITTED in the working tree

| fix | files | effect |
|---|---|---|
| **Stage 5 DB exclusion** was silently disabled: it read a deleted January snapshot behind a bare `exists()` | `mo_pipeline/config.py` (new `latest_replications_db()`), `discover/filter_direct_replications.py` | resolves the newest DB from `version_history.txt` at run time; a miss is now a loud warning; added to `python -m mo_pipeline.config`. 26 already-published papers now excluded. |
| **Dead ancestry/SNP regex**: uppercase patterns matched against lowercased text | `discover/filter_direct_replications.py` | compiled `IGNORECASE`; reclassifies 2 papers (Japanese and Chinese samples), both correct |
| **Catalog picked the wrong run**: `latest_tag` fell back to alphabetical order (670 papers); centrality output read as extraction (42 papers) | `mo_pipeline/corpus/models.py` | anchored result filenames to the folder stem; version read from first stamped entry; tie-break on `provenance.json` timestamp then mtime; verdict no longer overwritten by a verdictless result |

Tests: new `discover/test_filter_direct_replications.py` (15) and 7 new cases in
`corpus/test_models.py`; full suite 115 passed. Run with
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. **A `corpus scan` is needed** for the catalog
fix to take effect on stored rows. `filter_direct_replications.py` also carries
another session's LF normalisation and fetchpdf import fix; a commit will include
them.

### Fixed by other sessions during the audit
Model-slug leak in `screening_backend.get_backend` (`c676d6a`);
`_reference_matches` requiring GROBID's parsed year (`8bae163`); core-extractor
batch detection and `include-list` emitting absolute paths (`4c84da8`).

### Found, still open (highest first)
1. **Stage 4 stamps "replication, high confidence" on every paper on the drive**
   (`build_processed_manifest.py` has no WHERE clause): 36% of
   `confirmed_replications.csv`, including 2,282 known non-replications.
2. **Two fetch paths lack the Elsevier key** (`corpus/backfill.py`,
   `discover/filter_direct_replications.py`) because `fetchpdf` binds it at import
   and only stage 6 imports `fetchpdf_grey` first. Fix: one shim module plus a
   subprocess test.
3. **Stopped/killed runner stages report `finished`** (exit sentinel never written).
4. Search checkpoints mark HTTP failures as completed queries; `--max-per-query`
   not in the key.
5. 255 `classified.csv` rows have a type in the `confidence` column.
6. Stage 6 and stage 7 share no mutex; tests wrote pid files into live state.
7. `DELETE /doi-runs/{slug}` path traversal — **latent**, blocked today by a
   `meta.json` guard; harden anyway.
8. `load_version_number` parses `8.10` as `8.1`.
9. A killed extraction can leave a bare `result.json` that a later failed run
   silently adopts.
10. `check_if_replication_study.py` / `sanity_check_...py`: unwired, divergent
    taxonomy, 34% false-negative rate on the trial; do not gate on them.

Disproved (do not re-raise): Elsevier key lost in stage 6; malformed
`source_counts.json`; re-classify dropping prior verdicts; DOI normalisation
mis-skipping papers.

## 5. Environment changes since

- The corpus moved 2026-09-05 from the failing USB drive `/media/dan/500Gb`
  (unrecovered read errors observed during these runs) to
  `/media/dan/data/metascience_observatory_pdfs`. Treat the old drive as
  read-only salvage.
- Result folders from all three runs moved with the corpus under their tags.

## 6. Suggested next steps

1. Review and commit the three uncommitted fixes; then `python -m mo_pipeline.corpus scan`.
2. `mark-ingested` the two ingested CSVs if not already done.
3. Fix open items 1 and 2.
4. Code the gold_v1 sheet so a real, diverse, adjudicated benchmark exists.
5. Correct the public V6 evaluation page.
6. Before trusting `--level full` statistics, rerun `core_trial100_include.txt`
   under full mode and build the locality check for misattributed values.
