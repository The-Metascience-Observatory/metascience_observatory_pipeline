# Core-extractor trial, 100 papers (2026-09-03)

Stage 8b (`mo_pipeline/extract/extract_core.py`), stat-free single-shot, Sonnet on
the `claude_cli` backend, `--workers 10`, tag `core_v88_sonnet_trial100`.

Sample: seeded random 100 (`random.Random(20260903)`) from the 5,116 catalog papers
with status `converted` or `screened`, i.e. readable full text never extracted.
List: `core_trial100_include.txt`. Log: `core_trial100.log`.

## Outcome

| | |
|---|---|
| extracted / skipped (already in DB) / failed | 88 / 12 / 0 |
| wall clock | 9m 15s at 10 workers, mean 36.5 s per paper |
| cost | $19.77, about $0.22 per paper |
| rate-limit pauses | none |
| papers with replications | 36 of 88 (41%) |
| replication rows | 75 |
| input tier | grobid 87, xml 1 |
| reference source | references.json 72, PDF tail pages 16, none 0 |
| truncated inputs | 1 |

Labels: success 54, failure 13, inconclusive 7, reversal 1. Types: close extension
33, close experiment 21, conceptual 15, direct 6. Self-reported confidence: medium
46, high 25, low 4 — far better spread than the old full extractor, which said
`high` on 98% of rows.

## Quality checks

- All 14 statistical columns are blank on every row, as the mode requires.
- `ai_version` is `8.8-core` on all 75 rows; all 88 provenance sidecars record
  `prompt_level: core`, `prompt_version: 8.8`, `code_fingerprint 88bbdc28f708c8b8`.
- `citation_sentence` present on 75/75 and traceable to the paper text on 74/75.
- `original_url` filled on 68/75; the 7 blanks are the prompt's "do not fabricate"
  rule, not silent failures.
- Multi-effect papers split correctly: the Buss 1989 cross-cultural paper yielded 5
  rows with per-effect results rather than one aggregate row.

## Two things to know before scaling

1. **The CLI silently downgraded one call to Haiku.** `10.3389--fpsyg.2014.00378`
   ran on `claude-haiku-4-5` despite `--model sonnet` (18 output tokens, 1.5 s).
   Its answer was still correct (the paper is a meta-analysis, correctly excluded),
   but at 4,600 papers this will happen dozens of times. The provenance sidecar is
   what makes it visible; check `model_id` across a run before trusting it.
2. **59% of the backlog costs a call to return "no replications".** Hit rate was
   45% on `converted` papers and 34% on `screened` ones (the screened set includes
   screened-negatives, because `include-list --status screened` does not filter on
   outcome). Passing `--exclude-no-replications` cuts the backlog from 5,116 to
   3,811 papers and drops mostly known negatives.

## Extrapolation to the full backlog

4,639 papers (5,116 minus the 477 already in the production DB) at the observed
rate: about **7 hours** at 10 workers and about **$1,040**. Excluding screened
negatives: about 3,811 papers, roughly 5.5 hours and $850.

## Known bug hit during setup

Pointing `extract_core.py` at the corpus root runs it as a single paper: `main()`
sets `single = paper_artifacts(path)["has_fulltext"]`, and `papers/` root holds 5
loose PDFs, so the batch branch is never reached and `--include-list` is ignored.
Worked around with a symlink farm of the sampled folders (safe because
`extract_paper_core` resolves symlinks before writing). Reported to the session
that owns the file; the fix is an explicit `--batch` flag.

Also: `corpus include-list -o` writes absolute paths, but both extractors match on
the bare folder name, so its output needs `basename` before use as an include list.

---

# Hand audit of 10 rows (2026-09-03)

Ten rows across eight papers, stratified to over-sample the risky cases: the run's
only `reversal`, both `low`-confidence rows, both rows where no original DOI was
found, plus failures, an inconclusive and successes. Each was verified by reading
the paper's Results and Discussion, resolving the cited original against the
paper's own bibliography, and only then comparing with the pipeline's claim.

## Verdicts

| | result |
|---|---|
| replication outcome correctly labelled | **10/10** |
| original study correctly identified | **8/10** |
| blank `original_url` justified | 7/7 across the whole run |

Every result label matched what the replication authors themselves report,
including the run's single `reversal`, which is carried by a significant effect in
the opposite direction that the authors describe as "went in the opposite
direction than that of previous studies".

**Both original-identification failures are the same paper**, Buss (1989) "Sex
differences in human mate preferences: 37 cultures"
(`10.1017--s0140525x00023992`). It is a cross-cultural theory test, not a
replication of any named study: the 18-item instrument descends from Hill (1945)
via McGinnis (1958) and Hudson & Henze (1969), and the predictions come from
Trivers (1972) and Dickemann (1981). The extractor named Buss & Barnes (1986),
which the paper cites for a different instrument, and reused one sentence from
Buss's Author's Response to commentaries as the `citation_sentence` for both rows.
Having no effect-specific citation to point at is the tell. That paper's own
`replication_check.json` says `contains_replications: false` with high confidence,
and on this paper the screener is right and the extractor is wrong. Its five rows
are the run's clearest false positives, though their result labels are still
faithful to what Buss reports.

## Defects found (none change a verdict)

1. **A misattributed statistic in an `explanation`.** For
   `10.1007--s10508-009-9559-6`, the explanation cites `t(157) = -9.33` for
   homosexual women's preference in female faces; that value belongs to the
   male-faces sentence, and the correct figure is `t(157) = -7.27`. Verified
   directly against `body.md`. In core mode this is only prose, because the
   statistical columns are stripped, but the same misattribution in
   `--level full` would land in `replication_es` / `replication_p_value`. This is
   the strongest argument for benchmarking full mode's statistics separately.
2. **`replication_type` skews one notch too "close".** Three of the ten were
   judged better as `conceptual` than the assigned `close extension` /
   `close experiment`. Consistent with the run-wide distribution, where
   `close extension` takes 33 of 75 rows and `conceptual` only 15.
3. **One `description` carries an untested claim.** For
   `10.1037--0022-3514.72.5.1202` the description asserts the effect is due to
   becoming a parent rather than age, but that paper's design confounds the two
   and it never tested the attribution; it repeats the original author's argument.
4. **One original is correct but underdetermined.** `10.1016--j.brat.2019.103448`
   replicates a literature, not a single study; the named original is one of four
   equally-cited candidates. Any of them would have been defensible.

## Reference lists are the weak input

GROBID's `references.json` is thin or unparsed across this corpus: median 8
entries per paper, 47 of 88 papers with fewer than 15 references, and 39 of 88
where no entry has a parsed year. Consequences:

- A mechanical audit of original-study identification against the bibliography is
  not possible, and any such number would be misleading. The first attempt scored
  47%, entirely because of parsing failures, on papers whose citation sentence
  names the original outright.
- `extract.py`'s `validate_citation_sentences` corroborates via
  `_reference_matches`, which requires a parsed `year` field. On the 39 papers
  with no parsed years that corroboration silently never fires.
- The extractor fell back to PDF tail pages for the reference list on 16 of 88
  papers, and still identified the original correctly in every audited case,
  so the fallback ladder is doing real work.

The usable grounding check is textual: `citation_sentence` is present on 75/75
rows and traceable to the paper's own text on 74/75.

## Extractor vs the on-disk screener

32 of the 88 extracted papers also carry a `replication_check.json` from
`check_if_replication_study.py`. They disagree on 11, always in the same
direction: the extractor found replications where the screener said none, with
high screener confidence. Never the reverse.

Four of those 11 fall in the audit sample, and the extractor wins three of them:
Rafal et al. 1989 (Experiment 1 says "to confirm" and the Discussion says
"confirm the previously reported observations"), Iida et al. 2008 (an explicit
within-dyad replication of Hobfoll & Lerman 1989), and the reversal paper. The
screener wins only on Buss 1989. So the disagreement is mostly the screener
missing replications embedded in otherwise-original papers, which matches the
earlier finding that `check_if_replication_study.py` is unbenchmarked and uses a
taxonomy that diverges from `prompt_shared_core.md`. Do not use it to gate this
extractor without benchmarking it first.

---

# Follow-ups (2026-09-03, later)

## The two blocking bugs are fixed and verified

Fixed by the session that owns `extract_core.py`, commit `4c84da8`:

- Batch detection now keys off structure (`looks_like_batch`, first subdirectory
  with readable full text) instead of whether any PDF sits in the directory, with
  explicit `--batch` / `--single` overrides. Verified: the corpus root classifies
  as batch even though `paper_artifacts` still reports `has_fulltext` there, and a
  paper folder with tag subdirs classifies as single. The symlink-farm workaround
  is no longer needed.
- `corpus include-list` now emits bare folder names, so its output can be fed
  straight to `--include-list`. Verified.

Also fixed upstream (`34ba6c8`): `_extract_last_names` only parsed "Smith, John"
while metadata enrichment rewrites authors as "John Smith", so
`validate_citation_sentences` could not find the author in its own citation
sentence and raised false mismatches, each costing a review round that can change
labels. That compounds with the year requirement in `_reference_matches` noted
above.

## The misquoted statistic is misattribution, not fabrication

Settled by enumerating every t-statistic in `10.1007--s10508-009-9559-6`. Both
values exist in the paper and share the same degrees of freedom:

    t(157) = -7.27   homosexual women preferring feminized FEMALE faces  <- the row's effect
    t(157) = -9.33   homosexual women preferring feminized MALE faces    <- what was reported

Adjacent sentences, same subject group, same df. Neither a presence-in-text check
nor a df-consistency check can catch this class; detecting it needs locality, i.e.
whether the value occurs within the span of text describing that specific effect.

Across the whole run only 3 of 75 rows quote a statistic at all (4 statistics
total), so core mode gives little signal on statistical extraction by design. Of
those 4, zero are fabricated: the two that failed a verbatim check are GROBID
mangling, notably `p < .001` converted to `p \\ .001`. Do not read a
presence-failure on a p-value as fabrication on GROBID-tier papers.

To actually test the statistical fields, re-run
`benchmarking/results/core_trial100_include.txt` under `--level full` with a new
tag. Identical inputs, already extracted stat-free, so the two runs pair directly.
Candidate rows: `core_trial100_stat_suspects.json`.
