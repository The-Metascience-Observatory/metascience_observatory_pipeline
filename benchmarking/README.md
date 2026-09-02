# Benchmarking the extraction pipeline

How the Metascience Observatory measures its extraction pipeline (stage 8) and,
separately, its discovery stages (1 to 5). Everything here was rebuilt on
2026-09-02 after an audit found the February 2026 numbers unusable; that audit
and the superseded artifacts are in `archive/legacy_feb2026/README.md`.

Contents

- [Layout](#layout)
- [Ground-truth tiers and provenance](#ground-truth-tiers-and-provenance)
- [Building gold: sampling, coding, adjudication](#building-gold)
- [Splits, the test ledger, and pre-registered gates](#splits-ledger-gates)
- [Run protocol](#run-protocol)
- [What `evaluate` measures](#what-evaluate-measures)
- [Matching](#matching)
- [Discovery recall](#discovery-recall)
- [Screening-model eval (2026-08-09)](#screening-model-eval)
- [Known limitations](#known-limitations)
- [History of superseded numbers](#history)

## Layout

```
benchmarking/
  harness.py          the evaluator + gold-set toolchain (all subcommands)
  matching.py         DOI / LLM-judge / Hungarian matcher used by harness.py
  test_harness.py     pytest: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest benchmarking/test_harness.py -q
  codebook.md         coder codebook, versioned (codebook_v1); derived from prompts/prompt_shared_core.md
  recall_harness.py   discovery-recall funnel against external answer keys
  gold/               gold_rows.csv, gold_negatives.csv, manifest.json (built by harness.py build-gold)
  gold/coding/        sampling frame (UNBLINDED, never shown to coders), coder sheets, adjudication queue
  silver/             external label sets with a provenance column (see below)
  results/            one dated folder per evaluation + test_ledger.jsonl
  recall_eval/        dated discovery-recall baselines
  cache/match/        LLM-matcher decision cache (gitignored; safe to delete)
  archive/legacy_feb2026/   quarantined Feb-2026 apparatus and numbers (never scored)
  ground_truth_data_filtered_PDFs/   frozen legacy corpus (gitignored) holding the v6/v8 tag outputs
prompts/prompt_match.md + prompts/version_match.txt   the matcher judge's system prompt
```

`HARNESS_VERSION` in `harness.py` is recorded in every `metrics.json`: two runs are
only comparable at the same harness version, so bump it when scoring semantics change.
`python -m mo_pipeline.version` prints it alongside the pipeline and prompt versions.

Paths are exposed in `mo_pipeline/config.py` (`BENCHMARKING_DIR`, `BENCH_GOLD_DIR`,
`BENCH_SILVER_DIR`, `BENCH_RESULTS_DIR`, `BENCH_CACHE_DIR`, `MATCH_PROMPT_FILE`).

## Ground-truth tiers and provenance

Every ground-truth (GT) row carries a `provenance` value. `harness.py evaluate`
refuses to score any row whose provenance starts with `pipeline:` (rows authored by
the system under test); `--allow-dirty-gt` overrides this for forensic re-scoring
and stamps the report `CONTAMINATED`.

| Tier | Where | Provenance values | What it can score |
|---|---|---|---|
| gold | `gold/gold_rows.csv`, `gold/gold_negatives.csv` | `human:adjudicated` | everything: 4-value `result` (incl. reversal), `replication_type`, original DOI, `citation_sentence`, statistics, paper-level precision on negatives |
| silver | `silver/flora.csv` (531 rows, 524 papers, paper-level, authors' self-characterization, no reversal class), `silver/fred_v2_4_2.csv` (293 DOI-bearing rows, statistics filled, `replication_type` uniformly "direct or close"), `silver/main_gt_fred_api.csv` (134 rows, Feb-2026 FReD API import), `silver/main_gt_human.csv` (33 rows coded by Dan Elton / forrt.org) | `external:flora`, `external:fred_v242`, `external:fred_api`, `human:dan_elton`, `human:forrt_lk`, `human:forrt_cd` | 3-value `result` (pipeline `reversal` collapsed to `failure`, flagged), original DOI, statistics (FReD only) |
| quarantine | `archive/legacy_feb2026/ground_truth_enhanced_V6DERIVED.csv` | 36 rows `pipeline:v6`; human rows carry `v6_filled_cells` | nothing |

Silver extras: `silver/main_gt_label_flips.csv` lists 8 rows whose `result` changed
between the Feb-6 and Feb-19 files with no rationale (adjudicate before use);
`silver/main_gt_dropped.csv` records the one self-replication row removed. The
importers are `harness.py import-flora` / `import-fred` (from the two xlsx files at
the repo root); `archive/tag_provenance.py` is the one-off that split the Feb file.

Discipline, subdiscipline and confidence are **not scored** (user decision).
Discipline is used only to stratify sampling and to break results down by group
(`psych`, `biomed`, `socsci`, `lang_edu`, `other`).

### Diversity: why gold has to be built

Paper-level effective numbers (exp of Shannon entropy), computed 2026-09-02:

| Set | Papers | Disciplines distinct / effective | Effective journals | 2020+ share | Psychology share |
|---|---|---|---|---|---|
| Feb-2026 main GT | 145 | 14 / 7.0 | 56 | 71% | ~80% psychology-adjacent |
| FLoRa | 524 | 19 / 7.3 | 224 | 40% | 42% |
| FReD v2.4.2 | 157 | 10 / 4.0 | 4.5 | 80% | 62% |
| production DB | 4,815 | 24 / 5.9 | 615 | 39% | 45% |

Medical fields (21% of the production DB), neuroscience (9%) and biology (6%) have
almost no human-labelled coverage in any external set: FLoRa has 39 / 14 / 22
papers there, FReD 6 / 1 / 0. Gold therefore oversamples biomedicine (see the
sampling frame below).

## Building gold

`codebook.md` defines every coded field; it is derived section-by-section from
`prompts/prompt_shared_core.md`. A prompt edit that changes a definition bumps
`codebook_version` and re-adjudicates that field.

1. `python benchmarking/harness.py sample --gold-version 1` writes
   `gold/coding/frame_gold_v1_UNBLINDED.csv` and the doi run `data/doi_runs/gold_v1/`
   (seed 20260902). Sources, in the order they fill corpus gaps:

   | source | rule |
   |---|---|
   | `flora` | 15/15/15 papers by FLoRa result, at least half non-psychology |
   | `fred_v242` | 9/8/8 by result, converted papers preferred, at most 6 rows per paper |
   | `main_gt_human` | all papers of the 33 human-coded Feb rows (`prior_exposure=yes`) |
   | `biomed_initiative` | RP:CB, Brazilian Reproducibility Initiative, RSESR papers from the DB |
   | `medical_slice` | 50 pipeline-extracted `medical fields` papers stratified by subdiscipline |
   | `prod_slice` | 5 discipline groups x 4 replication types, 3 papers per cell, plus enough papers with a pipeline inconclusive/reversal row to reach 20 |
   | `replication_wiki` | 40 economics papers whose close replications were identified by ReplicationWiki |
   | `curate_science` | 20 statistics-rich Curate Science papers |
   | `reversal_stratum` | 20 DB papers carrying a `result=reversal` row + 10 whose text says "opposite direction" without the label |
   | `negative_random` / `negative_adversarial` | 30 high-confidence screened negatives + 30 reproducibility / reanalysis / meta-analysis / commentary papers |

2. Download and convert the run (see Run protocol), then
   `harness.py coding-sheet --gold-version 1 --coder <id>` writes a blinded sheet
   (only `row_id, replication_doi, paper_folder, external_row_id, original_hint`
   plus empty coded columns; a whitelist assertion guarantees no pipeline value can
   leak in). Coders never open a paper's `<tag>/` subfolders.
3. Coder A = Dan. Coder B = a second human on a 50-paper core if available,
   otherwise an AI coder of a different model family run against the codebook via
   `discover/screening_backend.py` (never the model under test), disclosed per row
   in `coder_b_id`. AI proposals never become gold without human adjudication.
4. `harness.py agreement --gold-version 1 --coder-a dan_elton --coder-b <id>` prints
   result / replication_type kappas, DOI exact agreement, N-within-5%, and writes
   `gold/coding/adjudication_queue_gold_v1.csv` (every disagreement + a seeded 20%
   of agreements). Pre-registered ladder: result kappa >= 0.70 proceed; 0.55 to 0.70
   refine the codebook and re-code 20 papers; < 0.55 stop. replication_type >= 0.60;
   original DOI >= 0.95.
5. `harness.py adjudicate --gold-version 1 --adjudicator dan_elton` walks the
   queue with the paper's Discussion excerpt; records `adjudicated_value`,
   `adjudicator_id`, `adjudication_note`, `gt_ambiguity`.
6. `harness.py build-gold --gold-version 1 --coder-a dan_elton --coder-b <id>`
   writes `gold/gold_rows.csv` (coder A values as `a_<field>`, coder B as
   `b_<field>`, adjudicated value as `<field>`, provenance `human:adjudicated`),
   `gold_negatives.csv`, and `manifest.json` with sha256 of every gold and silver
   file, the codebook hash, seed, salt, and the dev/test DOI lists. Gold grows by
   version (`gold_v2` after the later sources) so benchmarking starts before coding
   finishes.

Precision budget (Wald, 95%): overall accuracy at n~500 rows is +/-3.5pp;
inconclusive recall at +/-5pp needs ~290 inconclusive rows (reachable only on
pooled gold + silver 3-value scoring); paper-level FPR on 60 negatives is +/-5.5pp;
the minimum detectable prompt-to-prompt delta on ~500 paired rows is ~5pp
(~7pp at 280). Gates are therefore noise-band based, not "+2pp".

## Splits, ledger, gates

Papers are split by a deterministic hash of the DOI with the manifest salt (60%
test / 40% dev). Silver sets are dev-only, except FReD statistics rows, which follow
the same hash. `evaluate --split test` requires `--release`, refuses uncommitted
`prompts/`, refuses `--allow-dirty-gt`, and appends to `results/test_ledger.jsonl`;
the report prints how many times the current prompt version has been evaluated on
the test split so test-set overuse is visible.

Release gate (test split): result kappa >= 0.70; paper-level precision on negatives
>= 0.95; original DOI accuracy >= 0.85; entry-level precision and recall >= 0.80;
retest result agreement >= 0.85; matcher precision >= 0.95 on the audit set.
Dev-side prompt acceptance: mean-of-two-runs improvement >= max(3pp, noise band),
no per-class recall drop > 5pp, no new paper-level false positives, no statistic
fabricated on a negative paper.

## Run protocol

```
# 0. nothing else holding the claude_cli mutex, drive headroom
./dev.sh status && df -h /media/dan/500Gb

# 1. fetch + convert the run's papers (also needed for silver:flora -> run flora_gt)
python -m mo_pipeline.discover.download_all_confirmed --doi-csv data/doi_runs/gold_v1/dois.csv --workers 4
pdf4llm batch /media/dan/500Gb/metascience_observatory_pdfs/inbox -o /media/dan/500Gb/metascience_observatory_pdfs/papers --mode full-grobid --workers 4 --movepdf --resume
python -m mo_pipeline.corpus adopt-structured --execute && python -m mo_pipeline.corpus render-markdown --execute && python -m mo_pipeline.corpus scan
python benchmarking/harness.py status --run gold_v1

# 2. two independent extraction runs, prompts committed, tag = {set}_{promptver}_{model}_r{n}
python benchmarking/harness.py run --run gold_v1 --tag gold1_p86_sonnet_r1 --model sonnet --workers 4
python benchmarking/harness.py run --run gold_v1 --tag gold1_p86_sonnet_r2 --model sonnet --workers 4
#    `run` always passes --dontcheck (every FLoRa/FReD DOI is already in the production
#    DB, so extract.py would otherwise skip all of them) and fails if any paper was
#    skipped as "Already in dataset". --force-tier xml|html|grobid|pdf for the tier study.

# 3. score
python benchmarking/harness.py evaluate --gt gold --run gold_v1 --tags gold1_p86_sonnet_r1 --split dev
python benchmarking/harness.py retest   --run gold_v1 --tag-a gold1_p86_sonnet_r1 --tag-b gold1_p86_sonnet_r2
python benchmarking/harness.py evaluate --gt silver:flora --run flora_gt --tags flora_gt
python benchmarking/harness.py match-audit --results benchmarking/results/<dir>      # then --score
python benchmarking/harness.py evaluate --gt gold --run gold_v1 --tags gold1_p86_sonnet_r1 --split test --release
```

Each extraction now writes `<paper>/<tag>/provenance.json` (model id, prompt
version and sha256 of the prompt files, primary input tier and file, forced tier,
CLI version, git commit, dontcheck flag, timestamp); `evaluate` reads it and breaks
results down by tier. `citation_sentence` is now in the collated CSV as well.

## What `evaluate` measures

Outputs go to `results/<date>_<tags>_<gt>_<split>/`: `metrics.json` (provenance
block first, then metrics, 95% CIs, breakdowns, matcher stats), `report.md`,
`matched_rows.csv`, `disagreements.csv`, `unmatched.csv`, `extra_rows.csv`,
`per_paper.csv`, `confusion_result.png`, `match_log.jsonl`.

- Coverage funnel: papers not converted / not extracted under the tags / bad JSON /
  pipeline said no replications / **wrong document on disk** / scored. The last is a
  paper that reported no replications and whose folder does not hold the article it
  is filed under (a citing thesis, a publisher advert): stage 6 fetched the wrong
  file, so it is a coverage gap, not an extractor false negative. Checked only for a
  negative extraction, so a real paper's positive result is never overridden.
- Entry level (effect-level GT): TP = matched rows, FN = unmatched GT rows, FP =
  unmatched extracted rows; precision, recall, F1. For paper-level GT (FLoRa) extra
  rows are counted but not penalized. Granularity misses and judge-flagged
  wrong-original errors are reported separately.
- Paper level: TP/FN on GT papers, TN/FP on gold negatives, precision and FPR.
- `result`, paper-level GT: FLoRa labels the whole paper while the pipeline emits one
  row per effect, so the report shows the strict number (the matched row) as the
  headline and, beside it, the same rows **aggregated per paper** by
  `harness.paper_aggregate` (one distinct label carries; success and failure together
  are inconclusive; success plus inconclusive stays success while successes are not
  outnumbered). A **clear slice** excludes rows the corrections sidecar flags as a
  convention boundary.
- `result`: scored on the DB column's values. 3-value everywhere (pipeline
  `reversal` collapsed to `failure` only for GT sources without the class, flagged
  `collapse_applied`); 4-value strict on gold and human rows, with reversal recall
  and precision. Accuracy, Cohen's kappa (unweighted), macro-F1, per-class
  precision/recall/F1, confusion matrix.
- `replication_type`: 4-class accuracy, adjacent-or-exact accuracy on
  direct < close experiment < close extension < conceptual, kappa; 2-class
  (direct-or-close vs conceptual) for FReD rows.
- Original study: DOI exact after normalization, share left empty, title (ratio
  >= 0.75), authors (last-name Jaccard >= 0.7), year, journal; `citation_sentence`
  present rate and whether the GT original's author surname + year appear in it.
- Statistics: denominator = both present. N exact / within 5% / within 20%; effect
  size same type then |delta| <= .01 / .05 / .10 (type mismatches listed, not
  scored); p-value |delta| <= 1e-4 with the same comparator / <= 1e-4 / same side of
  .05. GT-present/extractor-missing and extractor-present/GT-missing counts per field.
- Uncertainty: paper-level cluster bootstrap (2,000 resamples, seed 0) on the
  headline numbers; Wilson intervals where a bootstrap is not possible.
- Breakdowns by source, provenance, discipline group, GT replication type, input
  tier, year bucket, split, `gt_ambiguity`, match method.
- `retest` reports, for two tags on the same papers: same-row-count rate, matched
  rows, result agreement and kappa, type agreement, DOI agreement, disagreement
  pairs. Headline numbers are reported as the mean of r1/r2 with the noise band.

### Ground-truth corrections

An external set is imported verbatim and stays that way, but some of its rows are
demonstrably wrong about which study a paper replicates, checked against the paper's
own text. Those live in `silver/<name>_corrections.csv` (`row_id, action, field,
value, evidence, date, by`; `action` is `set` | `drop` | `flag`), applied by
`harness.apply_gt_corrections` at load time, before normalization. Every row carries
the quote that justifies it. Corrected rows keep their external provenance with
`;corrected:<date>` appended, so the pipeline-provenance guard still sees them for
what they are, and `report.md` states how many corrections were applied.
`flora_corrections.csv` currently fixes four mislinked originals (three of them
another paper by the same authors), drops one duplicate row, and flags four rows
where FLoRa's "authors' own characterization" and the codebook's "partial support is
inconclusive" genuinely disagree.

### DOI aliases

One study can carry several DOIs: a preprint and its published version, a JSTOR
registration beside the publisher's own, or a Registered Report standing in for the
study it protocols. `matching.canonical_doi` maps each to a canonical DOI using the
curated `doi_aliases.json`, and both the matcher and `doi_ok` compare canonical
forms. Unknown DOIs pass through untouched. Add an entry only with a checked
`source`: a wrong alias silently merges two different studies.

## Matching

`matching.assign` runs per replication paper:

1. normalized original-DOI equality -> cost ~0 (description similarity breaks ties
   among rows sharing a DOI);
2. for GT rows with no DOI hit, or an ambiguous one, the Haiku judge
   (`prompts/prompt_match.md`, via `discover/screening_backend.py`:
   `claude -p --model haiku --tools ""`, or `--provider openrouter`) sees the GT
   entry and all extracted rows of the paper with result labels withheld and returns
   `{match, relation, confidence, reason}` with relation in `same_effect`,
   `subanalysis_of_gt`, `same_original_other_effect`, `different_original`, `none`.
   Only the first two count as matches (cost 0.1 / 0.4 / 0.7 by confidence). Every
   decision is cached under `cache/match/<sha256(prompt version, model, GT fields,
   candidate fields)>.json` and appended to `match_log.jsonl`; if the judge is
   unavailable the Feb-2026 fuzzy rule (2 of title/authors/year) is the fallback;
3. `scipy.optimize.linear_sum_assignment` makes the assignment one-to-one.

First live run (2026-09-02, FLoRa pilot, 50 papers, `claude -p --model haiku`):
29 of 33 matches came from DOI equality; the judge was called 7 times for the
residual and returned 2 `same_effect` (a URL-encoded DOI variant; a retitled
original), 2 `subanalysis_of_gt` (paper-level FLoRa rows mapped to the constituent
study the pipeline extracted), and 3 `different_original` with the competing
original named in the reason (e.g. the pipeline replicated Mazar et al. 2008 where
FLoRa's row names a different study). The old string matcher could only report the
last three as "no match". Results: `results/2026-09-02_flora_pilot_silver-flora_all/`.

`harness.py match-audit --results <dir>` writes a blinded sheet of a seeded 10% of
judge decisions plus every low/medium-confidence one; after a human fills
`human_verdict`, `--score` reports matcher precision on matches and the correct
rate on non-matches. The judge must reach >= 0.95 precision on a 50-paper audit
before its matches are trusted for a release evaluation.

## Discovery recall

`python benchmarking/recall_harness.py --json benchmarking/recall_eval/baseline_<date>.json`
walks FLoRa, FReD and the production DB through the stage files
(candidates_raw -> dedup -> filtered -> classified -> confirmed -> direct) and
records git commit, `data/keywords.json` hash and each stage file's mtime.
`--compare OLD NEW` prints per-stage deltas. Regenerate whenever search or
prefilter changes; the production-DB sets are circular and labelled as such.

Baselines: `baseline_2026_08_09_pre_prefilter_fix.json` (before commit `bad7d7f`)
and `baseline_2026_09_02.json`. FLoRa confirmed 14.7% -> 41.0% (prefilter fix
recovered +26pp; search still never surfaces 52%); FReD unchanged at 13.2%. The
`direct` stage file dates from 2026-02-05 and is stale in both.

## Screening-model eval

Recorded here because `mo_pipeline/config.py` cites it. On 2026-08-09 the stage-4
screening backends were compared on 60 known FLoRa replications, 60 Haiku
negatives and 8 adversarial non-replications: ling-2.6-flash 100% recall, 0/8 false
positives, 0 hallucinated flags, $1.28 per 100k; haiku-4.5 100% recall but $169 per
100k via OpenRouter and >= 18% of its own high-confidence negatives demonstrably
wrong; gpt-5.6-luna 93% recall at $20 per 100k. No per-row artifact of that run
survives; treat these as a code-comment record, not a benchmark result.

## Known limitations

- The extractor is an agentic `claude` CLI run with no temperature or seed control;
  `retest` is the only noise estimate. On the legacy v8 vs v8_1 tags (14 papers)
  result agreement was 78.6% (kappa 0.67).
- FLoRa labels are the replication authors' self-characterization and its
  field/discipline tags were assigned by Sonnet; FLoRa has no reversal class and
  no statistics. FReD's replication_type is uniformly "direct or close".
- The Haiku judge is itself a model; its precision is audited but not zero-error.
- Dan has prior exposure to pipeline output on the Feb-2026 human papers
  (`prior_exposure=yes`); an AI coder B on part of gold is disclosed per row.
- The XML rendition tier is small (catalog rescan 2026-09-02); the tier-paired
  study can only use papers that have both `_from_xml.md` and `body.md`.
- Production-DB derived recall is circular; only FLoRa/FReD recall is honest.
- Claude CLI version drift is recorded in provenance but not controlled.

## History

Superseded (see `archive/legacy_feb2026/README.md`): V6 85.0% / V8 79.3% result
accuracy on a 189-row GT of which 36 rows and 862 statistical cells were written by
V6; matching many-to-one; no CIs; models differed between runs (v6 sonnet-4-5, v8 sonnet-4-6, the July FLoRa pilot sonnet-5).

Corrected legacy re-score with this harness (deterministic matcher,
`--allow-dirty-gt`, 300-resample bootstrap), for the record only:

| tag | model | scored papers | entry P / R | result 3-value acc | kappa | DOI exact | pipeline:v6 rows acc | FReD-API rows acc |
|---|---|---|---|---|---|---|---|---|
| v6_feb2026 | claude-sonnet-4-5 | 139 | 61.9% / 85.2% | 85.1% [79.7, 90.8] | 0.749 | 69.5% | 100.0% (35) | 80.2% (106) |
| v8_feb2026 | claude-sonnet-4-6 | 141 | 60.8% / 85.2% | 80.7% [73.9, 85.7] | 0.678 | 91.0% | 89.7% (29) | 79.1% (110) |

FLoRa pilot re-scored (Haiku judge, `results/2026-09-02_flora_pilot_silver-flora_all/`):
33 of 37 extracted papers matched, 3-value accuracy 72.7%, kappa 0.568, 3 of the 4
unmatched rows are judge-flagged wrong-original errors; the pilot ran on claude-sonnet-5 for all 37 papers (its debug logs recorded claude-haiku-4-5 for 34 of them because extract.py took the first key of the CLI's modelUsage, which lists an internal Haiku helper of about 15 output tokens before the working model; attribution fixed 2026-09-02), a third model, so it is not comparable to v6/v8.

Entry-level precision of ~61% (99 to 104 extra rows against 189 GT rows) is the
number the Feb reports never computed; on human rows the two prompt versions are
within noise of each other, and V8's real gain is original-DOI recovery
(69.5% -> 91.0%).
