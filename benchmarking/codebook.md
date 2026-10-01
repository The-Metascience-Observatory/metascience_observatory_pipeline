# Gold-set coding codebook

codebook_version: codebook_v2
date: 2026-09-28
derived_from: prompts/prompt_shared_core.md (prompt version 8.9)

**Changes in v2** (from the 2026-09-18 gold-coding audit; see `prompts/CHANGELOG.md`, 8.9):
the unit of coding is one row per *claim of the original*, resolving v1's
contradiction between "not each dependent variable" and "one row per effect";
timepoints/endpoints of one claim are one row; controls get no row; and a
replication of an original *null* claim has its own rule. Entries coded under v1
must be re-coded on these points before they are compared with v2 entries.

## Purpose

Human coders use this codebook to label replication papers so that the extraction
pipeline's output can be scored against them. The definitions here are taken
section by section from `prompts/prompt_shared_core.md`, so ground truth and the
extraction prompt share the same meaning of every label; where the prompt is
ambiguous, the codebook is ambiguous in the same way, on purpose. Coders never see
pipeline output for the papers they code. Every prompt edit that changes a
definition used here bumps `codebook_version` and triggers re-adjudication of the
affected field across the gold set.

## Unit of coding: what counts as a separate replication entry

One coded row is one replication entry. These are the prompt's rules, restated for
coders.

- A separate entry is each distinct replication study or experiment with its own
  participants or its own independent design.
- **The unit is a claim of the original.** A claim is a distinct finding the
  ORIGINAL authors reported as a result -- not a statistical test, measure or
  analysis path the replication happens to run. Decide by reading how the original
  presented its findings. Code one row per claim, per replication study that tests
  it.
- Several measures, statistical tests, mediator paths or scenarios/vignettes of the
  **same claim** within one study are ONE entry. If a single study tests whether A
  mediates X -> Y and whether B mediates X -> Y using the same participants, that
  is one entry, not two. Example: a paper replicates a mediation study testing 3
  mediator pathways with the same 300 participants; code 1 entry, not 3. If results
  differ across scenarios, the entry is inconclusive (see Result classification).
- Several **timepoints or endpoints of the same claim** are ONE entry. Classify it
  by the original's stated primary timepoint/endpoint if it named one; if not, and
  the results differ across timepoints, the entry is inconclusive. Example: an
  intervention improved abstinence at 3 months but not at 6 months -> one row.
- **Controls are not claims.** Negative and positive control conditions (a control
  cell line or contrast expected to show nothing) get no row.
- Multiple original papers replicated (Case A): one row per distinct original
  study. A paper that replicates Smith (2010), Jones (2015), and Brown (2018)
  yields 3 rows.
- Multiple studies or experiments from the same original paper (Case B): one row
  per study or experiment replicated. A paper that replicates Studies 1, 3, and 5
  from Smith (2010) yields 3 rows, all with the same `original_url`, each with a
  different `description` (what that specific study tested), its own `result`
  based on that study's outcome, and its own statistics.
- Multiple distinct claims from the same original study (for example the
  original's reported effects on compassion, empathy, and Theory of Mind): one row
  per claim, same `original_url`, different `description`, potentially different
  `result`. These are three rows because the original reported three findings, not
  because three variables were measured.
- A single row for a multi-study paper is correct only when the paper reports one
  aggregate result without breaking the studies down individually (rare). If
  individual results are reported, code separate rows.
- Within-paper replications are excluded. If Study 2 replicates Study 1 of the
  same paper, even with separate participants, it is not a qualifying entry. The
  original must be a separately published work.
- Self-replications are included. Authors replicating their own previously
  published work count.
- Code only replication experiments. New, original experiments in the same paper
  are not entries.
- Conceptual replications are included, with `replication_type` = conceptual.

## Fields to code

Fill the fields below, in this order, for every entry. Leave a field blank when
the paper does not support a value; never guess.

| Field | Allowed values / format | Rule |
|-------|-------------------------|------|
| `result` | success, failure, inconclusive, reversal | See Result classification. |
| `replication_type` | direct, close experiment, close extension, conceptual | See Replication type. |
| `original_url` | `https://doi.org/...` or blank | DOI URL of the original study. Blank if not found. Never fabricate or guess a DOI. |
| `original_title` | free text | Title of the original study. Always fill this, even when the DOI is blank. |
| `original_authors` | `Last, First; Last, First` | Semicolon-separated, one `Last, First` per author, as full names where the paper gives them. |
| `original_year` | four-digit year | Publication year of the original study. |
| `original_journal` | free text | Full journal or venue name of the original study. |
| `description` | one sentence | The ORIGINAL study's claimed effect that was being replicated, NOT what the replication found. "Exposure to elderly-related words causes participants to walk more slowly" (the original claim), not "Priming did not affect walking speed" (the replication result). In multi-study rows, say which study and what it tested. |
| `citation_sentence` | exact quote | The exact sentence(s) from the paper where the authors identify which prior study they are replicating. Must contain the author name(s) and year as cited in the text. |
| `original_n` | integer or blank | Number of subjects actually analyzed in the original experiment (analytical N). Prefer the N used in the statistical test over the N recruited, since participants may be excluded. If degrees of freedom are reported (for example df = 24 for a correlation), use df + 1 rather than a larger recruited number. |
| `original_es` | number or blank | Effect size in the original experiment, for the specific effect being replicated. Paired with `original_es_type`: fill both or leave both blank. |
| `original_es_type` | d, r, etasq, g, OR, or the label the paper uses; blank | Type of effect size: d (Cohen's d), r (Pearson's r), etasq (eta-squared), g (Hedges' g), OR (odds ratio), or other. Paired with `original_es`. |
| `original_es_95_CI` | two numbers, lower and upper; or blank | 95% confidence interval for the original effect size, as exactly two floats. Blank if not reported. |
| `original_p_value` | number or blank | p-value from the original experiment. For an inequality such as "p < .001" record the bound (0.001). |
| `original_p_value_type` | `<`, `=`, `>`, or blank | How the p-value is reported. These are the only allowed values; never `>=`, `<=`, "approximately", or any other symbol. |
| `original_p_value_tails` | one-sided, two-sided, or blank | Blank if not specified. |
| `replication_n` | integer or blank | Analytical N of the replication experiment. Same analytical-N and df + 1 rules as `original_n`. |
| `replication_es` | number or blank | Effect size in the replication experiment. Paired with `replication_es_type`: fill both or neither. |
| `replication_es_type` | same codes as `original_es_type`; blank | Paired with `replication_es`. |
| `replication_es_95_CI` | two numbers, lower and upper; or blank | 95% CI for the replication effect size, as exactly two floats. |
| `replication_p_value` | number or blank | p-value from the replication experiment; bound value for inequalities. |
| `replication_p_value_type` | `<`, `=`, `>`, or blank | Only these three symbols. |
| `replication_p_value_tails` | one-sided, two-sided, or blank | Blank if not specified. |
| `gt_ambiguity` | clear, ambiguous | Coder-only. Would a careful second coder plausibly choose a different `result` label for this entry? If yes, ambiguous. |
| `external_row_id` | id or blank | Coder-only. For papers anchored to a FLoRa/FReD row: the external row this coded row corresponds to. Blank otherwise. |
| `notes` | free text | Coder-only. Anything a second coder or adjudicator should know: why a value is blank, competing readings, a broken blind, page references. |

Rules that apply across the statistical fields: report only values explicitly and
clearly stated in the paper for the particular experiment being coded; do not
calculate, derive, convert, estimate, or infer a value, and do not look statistics
up outside the paper. A blank is always better than a wrong value.

Coders do NOT code `discipline`, `subdiscipline`, or `confidence`; they are out of
scope for scoring. `original_volume`, `original_issue`, `original_pages`, and
`explanation` are likewise not coded.

## Result classification

Classify based on what the replication authors report. The Discussion or
Conclusion section is the primary source for classification; always read it
before classifying.

### Step 1: Check for the authors' explicit statement

Prioritize the authors' own words. Look for phrases like:

- "successfully replicated", "confirmed", "consistent with the original" -> success
- "failed to replicate", "did not replicate", "no support for" -> failure
- "partially replicated", "mixed results", "some support", "limited support" -> inconclusive
- "opposite direction", "reversed", "contrary to the original" -> reversal

If the authors make an explicit statement, use it. Only override it if the
statement clearly contradicts the reported statistics.

### Step 2: If no explicit statement, evaluate the evidence

success: all of these should be true.

- Statistically significant effect in the same direction.
- Authors discuss the finding as confirming the original.
- If multi-experiment: all or nearly all experiments show the effect.

failure: any of these.

- No significant effect found AND authors frame it as a failure.
- Confidence interval includes zero or the null AND authors do not claim support.
- All experiments show null results.

inconclusive: use this when the result is genuinely ambiguous. Do not force a clear
success or failure when the evidence is mixed. Any of these:

- Qualified support: effect in the expected direction but not significant, AND
  authors use hedging language ("trending", "marginal", "approaching
  significance") rather than calling it a clear failure.
- Conditional success: effect found only in a subset of conditions or moderator
  levels within the same experiment.
- Mediated or indirect only: the original tested a direct effect A -> B; this
  replication finds only an indirect path A -> C -> B.
- Mixed signals within one experiment: significant on one measure but not another.
- Significant but questionable: statistically significant effect, but the authors
  express substantial validity concerns or caveats that undermine confidence in it.
- Authors explicitly hedge: "some evidence", "limited support", "partially
  consistent", "weak support" for THIS SPECIFIC experiment's outcome.

reversal: statistically significant effect in the OPPOSITE direction from the
original.

Reversal is extremely rare. Code reversal only when ALL of these are true for this
specific experiment:

1. Statistically significant effect in the OPPOSITE direction.
2. The replication authors themselves describe it as a reversal or opposite
   finding for this experiment.
3. The reversed effect is clear and unambiguous.

If only some conditions within the experiment show opposite effects, code
inconclusive, not reversal. When in doubt between reversal and anything else,
choose the other category.

Reversal is nonetheless a legitimate, first-class value that the gold set must
contain so that reversal recall can be measured; do not avoid it when the authors
use it and the three conditions above hold.

### Step 3: Tiebreaker rules (for a single experiment)

- Doubt between failure and inconclusive -> inconclusive (if there is genuine
  ambiguity, reflect it).
- Doubt between success and inconclusive -> use the authors' explicit statement
  for THIS experiment.
- Authors explicitly claim "we replicated [this experiment]" plus a significant
  effect in the same direction -> success (even if the effect is weaker than the
  original).
- Significant effect but authors express substantial validity concerns about THIS
  experiment -> inconclusive.
- Registered Report with a clear null result, where the original claimed an
  effect -> failure. (Where the original claimed no effect, see below.)

### When the original claimed NO effect

The definitions above assume the original found an effect. When the original
authors reported the *absence* of an effect as a finding in its own right, judge
agreement with that claim instead:

- Replication also finds no effect, and the authors treat it as consistent with
  the original -> success.
- Replication finds a significant effect where the original claimed none -> failure.
- Authors say the replication was too underpowered or imprecise to tell ->
  inconclusive.

A non-significant result is not proof of no effect, so rely on the authors' own
reading, not the p-value alone. This applies only to genuine null *claims*; a
control condition expected to show nothing is not a claim and gets no row.

### Common pitfalls

1. Do not over-rely on p-values. A single p < .05 does not automatically mean
   success; consider the full pattern of evidence and the authors' discussion.
2. Partial support = inconclusive, not success. An aggregate characterization such
   as "2 out of 4 experiments replicated" would be inconclusive. However, code
   separate rows for each of the 4 experiments with their individual results.
3. Mediated effects are not direct replication success. If the original found
   A -> B directly but the replication only finds A -> C -> B, that is inconclusive.
4. Multi-experiment papers: create separate rows. One row per study replicated,
   each classified on that study's own outcome. Do not collapse multiple studies
   into one row because they come from the same original paper or test related
   hypotheses.
5. Read the Discussion section. The authors' own interpretation matters more than
   your independent read of the statistics.
6. Multi-scenario papers: if the same effect is tested across multiple scenarios
   or vignettes and results differ across them, code inconclusive. Do not report
   based on a single scenario when several were tested.

## Replication type

Classify each entry as one of four categories. They form a spectrum from most to
least methodologically similar to the original.

direct: the experimental procedure was repeated as closely as possible, following
the original study's specifications. Same manipulation, same measures, same
general procedure. Minor unavoidable differences (different participants,
different lab, different time period) do not disqualify. If the authors call it a
"direct replication" or "exact replication", use this.

- Examples: pre-registered replication following the original protocol, same
  paradigm with a larger sample, Multi-Lab or Many Labs replication.
- Key signal: the goal is to reproduce the original result using the same methods.

close experiment: the same general paradigm and methodology as the original, but
with deliberate methodological changes that do not fundamentally alter what is
being tested. The core manipulation and measures are recognizably the same, but
the researchers made intentional modifications for practical, methodological, or
improvement reasons. The goal is still to test the same specific effect using the
same general approach, in the same general context.

- Examples: updated stimulus materials within the same paradigm, improved
  experimental controls, a computerized version of a paper-based task, added
  manipulation checks, a pre-registered version with minor procedural refinements,
  a different but equivalent measure of the same construct.
- Key signal: changes were made to HOW the study is run (methodology) while
  keeping the same context, setting, and population.

close extension: the core hypothesis and general paradigm are preserved, but the
authors test whether the effect holds in an expanded setting or a different
context. The goal shifts from "can we reproduce this result?" to "does this effect
generalize beyond the original conditions?"

- Examples: translated to a different language or culture, conducted online
  instead of in person, tested on a different population (children instead of
  adults, a clinical sample instead of healthy participants, a different country),
  modified stimuli used to test generalizability, the paradigm applied to a new
  domain, a field study of a lab finding.
- Key signal: changes were made to WHERE, WHO, or WHEN (context), testing whether
  the effect generalizes.
- Note: if both the methodology AND the context or setting changed, code close
  extension (the broader category). A study that tweaks the procedure AND tests a
  new population is a close extension, not a close experiment.

conceptual: the same theoretical claim or effect is tested using a fundamentally
different experimental procedure: a different manipulation, different measures, or
a different paradigm altogether. The authors test whether the same conclusion
holds under different methodological conditions.

- Examples: the same hypothesis tested with a completely different design, a
  different manipulation used to produce the same predicted outcome, the same
  construct measured with an entirely different methodology.
- Key signal: someone unfamiliar with the studies might not immediately recognize
  that they are testing the same thing.

Tiebreakers:

- The authors' own characterization wins when available ("we conducted a direct
  replication" -> direct).
- Doubt between direct and close -> direct (minor variations are normal in any
  replication).
- Doubt between close experiment and close extension -> if both methodology AND
  context changed, close extension; if only methodology changed, close
  experiment; if only context changed, close extension.
- Doubt between close extension and conceptual -> close extension, if the core
  paradigm is recognizably the same.

## Statistics

The statistical fields are lower priority than the identification and
classification fields. Most papers will not report all of them; that is expected.
Only fill a statistical field if you are confident the value is correct.

- Extract effect sizes and p-values for the specific effect being replicated, not
  for the overall study or other analyses.
- If the paper reports the original study's statistics (common in replication
  papers), extract those. If not, leave the original fields blank.
- If a p-value is reported as an inequality (for example "p < .001"), record the
  bound value (0.001) in the p-value field and set the type to `<`.
- If a p-value is reported as an exact value (for example "p = .03"), record the
  value (0.03) and set the type to `=`.
- If a p-value is reported as "ns" or "not significant" with no number, leave the
  p-value fields blank.
- Effect size types must match what the paper reports. Do not convert between
  types; downstream processing handles conversions.
- For confidence intervals, always use the 95% CI if several are reported.
- Sample sizes are analytical N (the N in the statistical test); if only degrees
  of freedom are given, use df + 1.
- `es` and `es_type` are filled together or left blank together; a bare number
  with no type cannot be interpreted.
- Do not guess, estimate, calculate, derive, or infer values, and do not search
  the web for them. If there is any doubt, leave the field blank.

## Edge cases

- Multi-study papers: code only the replication experiments, not the original
  experiments.
- Self-replications: include; authors replicating their own prior work counts.
- Conceptual replications: include, with `replication_type` = conceptual.
- Multiple originals: one row per effect per original study.
- Multi-effect replications from the same original: if a paper replicates several
  distinct effects from the same original study (for example effects on
  compassion, empathy, and Theory of Mind), one row per effect with the same
  `original_url` but a different `description` and potentially a different
  `result`.
- Within-paper replications: exclude. Study 2 replicating Study 1 in the same
  paper, even with separate participants, does not qualify. The original must be
  a separately published work.
- Missing DOI: leave `original_url` blank; title, journal, and year will be used
  to resolve it downstream. Always extract the title.
- Missing statistics: many papers will not report every statistical field. Leave
  unreported fields blank. Extract only what is explicitly and clearly stated for
  the particular experiment being coded; do not calculate or infer values.

## Negative papers

Some papers in the gold set are deliberately chosen to contain no qualifying
replication entry (`gold_negatives`). A paper is negative when, after applying the
unit-of-coding rules above, it yields zero entries. Code it as a single row with
all entry fields blank and `why_negative` filled.

Adversarial genres to expect, each of which looks like a replication but is not:

- Within-paper-only replication: the only "replication" is a later study
  replicating an earlier study inside the same paper.
- Reanalysis or reproducibility check of the same data: no new participants; the
  authors re-run or re-code an existing dataset.
- Meta-analysis or review of replications: the paper aggregates others'
  replications rather than reporting its own replication experiment.
- Commentary, methods, or theory paper: discusses replication or replicability
  without running a replication.
- "Replicates" in the biological or statistical sense: technical replicates,
  biological replicates, replicate samples or plates, or replicated designs, with
  no prior published finding being tested.

`why_negative` (free text, one short sentence): which genre above applies, or
another reason the paper has no qualifying entry. Set `gt_ambiguity` here too if a
second coder might plausibly find an entry.

## Procedure

1. Open the paper's folder in the corpus (`papers/{doi}/`). Read `body.md` or the
   `{stem}_from_xml.md` rendition if present; otherwise open the PDF.
2. Read the abstract, introduction, results, and discussion. Do not classify from
   the abstract alone; the discussion is the primary source for `result`.
3. Enumerate the entries using the unit-of-coding rules: which original studies,
   which experiments, which distinct effects. Decide whether the paper is negative.
4. Fill the sheet row(s). The sheet arrives with one row per paper, carrying a
   `row_id` (and an `external_row_id` for papers anchored to a FLoRa/FReD row).
   Fill that row for the first entry; for each additional entry add a new row that
   copies the paper's identifying columns and leaves `row_id` blank.
5. Never open any `<tag>/` subfolder inside the paper directory. Those hold
   pipeline output; opening one breaks blinding. If it happens, record it in
   `notes` for that paper and carry on.
6. Mark `gt_ambiguity` for every row, and use `notes` for anything the adjudicator
   will need.
7. Save the sheet in place, as CSV, without renaming it.
8. Commit nothing to git yourself; the harness owner collects sheets.

The sheet is produced by
`python benchmarking/harness.py coding-sheet --gold-version N --coder <id>` and is
consumed by `harness.py agreement` (inter-coder statistics) and
`harness.py build-gold` (the adjudicated gold set).

## Agreement thresholds (pre-registered)

Computed by `harness.py agreement` on the double-coded papers before any gold set
is built.

- `result`: Cohen's kappa >= 0.70, proceed. 0.55 to 0.70, refine this codebook,
  bump `codebook_version`, and re-code 20 papers. Below 0.55, stop; the labels are
  not reliable enough to score against.
- `replication_type`: Cohen's kappa >= 0.60.
- Original DOI (`original_url`): exact agreement >= 0.95.
- Every disagreement, plus a seeded random 20% of agreements, is adjudicated
  before the gold set is built.
