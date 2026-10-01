# Base-extractor benchmark audit — 18 September 2026

## Decision

**Do not publish a base-extractor accuracy claim yet.** All four audit tasks are
complete, but they uncovered defects in the reference-data workflow and source
coverage. The resulting agreement statistics are diagnostics, not extractor
accuracy and not evidence that the gold set is ready.

No website data, original coding sheets, existing adjudication decisions,
extraction prompts or codebook definitions were changed. FLoRa paper-level
verdicts were not used as ground-truth labels. Papers originally sampled from
FLoRa can still be independently coded; their sampling source is not their truth.

## 1. Rerun integrity

The saved Ling sheet contained 494 labelled entries although its last successful
run log reported 392 entries. The old writer retained unmatched previous values
and appended new entries. There are 158 labelled rows with a trailing unmatched
marker across 70 papers. These are stale candidates, not 158 proven distinct
model errors.

The old writer also discarded all positive entries when a paper had only a
negative-stratum anchor. Luna's log reported 597 entries, while only 574 labelled
entries survived in its sheet. Nine papers had missing enumeration payloads.
Some Ling papers had legitimate blank outcomes, so simply comparing the number
of nonblank outcomes with the log cannot recover all responses.

Repairs:

- A successful rerun replaces that paper's generated rows and clears anchor
  values before refilling. Unattempted/failed papers remain untouched and their
  run status is recorded separately.
- Positive entries found in negative-stratum papers are retained.
- Per-paper replies and run manifests are saved. The later retry also retains
  raw attempts, input hashes and failure metadata. Truncated JSON cannot pass
  merely because it parses. Explicit unsupported outcome blanks are allowed,
  as the existing codebook requires.
- A run with any failures now exits nonzero. The ordinary agreement reader
  refuses sheets containing labelled trailing-unmatched rows.

Recovery uses a conservative rule: discard trailing-unmatched rows and accept a
historical paper only when the remaining labelled count exactly equals its latest
success log. The writer always appended that marker to unmatched rows. A new
matched row might retain an earlier marker when no new note was provided, so
count mismatches require fresh coding rather than guessing which rows to keep.
Historical zero-entry logs do **not** establish a negative paper label unless an
explicit label was saved. Historical reconstructed responses have no raw-response
or input-hash provenance and are labelled accordingly.

Fresh coding covered **19 distinct papers**: 13 Ling calls and nine Luna calls,
plus retries on six of those Ling papers. It did not expand the frame.

Final clean diagnostic data:

- Ling: 204 papers represented, 367 labelled entries and 11 unscored entries;
  193 papers reconstructed and 11 recovered from fresh responses.
- Luna: all 206 papers represented, 598 labelled entries; 197 reconstructed and
  nine freshly coded.
- Two Ling records remain quarantined: `10.7554/elife.11566` (protocol; fresh
  reply incomplete) and `10.1007/s40279-025-02201-w` (missing study tables in the
  primary rendition; contradictory empty enumeration). Neither is silently
  treated as a successful zero-entry coding. Both are excluded from outcome
  agreement for independently observed source reasons.

The original files and code are preserved in `snapshot/`, with
[SHA-256 manifest](snapshot_sha256.json). The separate `repaired_sheet_*` files
are intermediate rerun sheets: untouched papers can still contain old rows.
Use [clean Ling](clean_ling26.csv) and [clean Luna](clean_luna56.csv) for this
audit, not those intermediate sheets.

## 2. Effect matching

The old matcher treated a shared original DOI as decisive and compared worksheet
anchors directly. Neither identifies an individual study or effect. Synthetic
row IDs could also collide across coder sheets.

Harness 1.4 gives rows coder-specific content IDs and compares **all** coded
entries by effect description. The candidate matcher requires a unique mutual
best match, minimum similarity 0.65 and margin 0.08. Explicit conflicting study
numbers and blank descriptions do not match. Outcome, type, original DOI and
worksheet anchors do not contribute to the score. These thresholds are
conservative audit heuristics, not a validated semantic matcher.

Unmatched entries mean **unresolved identity**, not proven omissions. Source
review rejected one automatic pairing that combined newspaper factor structure
with readership prediction, and set aside another with unspecified clinical
endpoints/timepoints. Nine specific pairs were checked against source passages;
that review was performed by AI, not a human adjudicator.

The old two-coder `build-gold` joined worksheet IDs, missed B-only entries and
could ignore composite pair adjudications. It now fails closed before writing
gold. A human-reconciled effect-identity export is still needed; silently
producing incomplete “human-adjudicated” gold would be worse than blocking it.

## 3. Frame, extraction and discipline reconciliation

The frame contains **206 papers**:

- 199 have successful `base_88_gold1` extraction results.
- Six were not attempted: their readable files are only in the legacy GT corpus,
  outside the extraction run's primary corpus root.
- One attempted paper failed: `10.31234/osf.io/esu9z`. Its 362-character body is a
  revision notice, not usable paper text. This is an input-document failure.

The six unattempted DOIs are `10.1002/ejsp.2748`,
`10.1177/01461672211052120`, `10.1177/0956797617747090`, `10.1257/mac.6.3.1`,
`10.1111/j.1540-6261.2012.01744.x`, and `10.1177/1091142114537893`.

The latest production CSV is `replications_database_2026_09_04_184008.csv`:
8,892 rows, **zero missing disciplines**, but **217 rows missing subdisciplines**.
Only 184 of the 206 frame papers occur there. The other 22 cannot be joined to
that CSV; their existing OpenAlex classifications and source-reviewed corrections
remain separately attributed. No database classifications were written back.

All **206 papers have a discipline in [the reconciled frame](frame_reconciled.csv)**.
There are still **23 papers without a subdiscipline**; these remain blank rather
than being invented. The six uncertain mappings were reviewed against source
text, with evidence in [the classification review](discipline_source_review.csv).
Subdiscipline choices are ontology-constrained AI classifications, not human truth.

Improved paper-level discipline counts:

- Psychology: 98
- Biology: 29
- Economics: 18
- Medical fields: 17
- Neuroscience: 17
- Education: 11
- Linguistics: 5
- Business & management: 3
- Political science: 3
- Environmental science: 2
- Human-computer interaction: 1
- Sports and exercise science: 1
- Sociology: 1

These describe frame composition, not discipline-specific accuracy. On multi-row
database matches, the mode is used and conflicts are recorded separately.
[Inventory](paper_inventory.csv), [database classification conflicts](database_classification_conflicts.csv),
and [all database rows missing subdiscipline](database_missing_subdiscipline.csv)
make the denominators and omissions inspectable. The original active frame is
preserved; the corrected version is the separate reconciled CSV above.

## 4. Clean agreement and targeted source review

[Full metrics](agreement_metrics.json) distinguish candidates from source-checked
pairs. Five protocol papers and three input/coverage problems were excluded from
outcome comparison. This is a targeted eligibility check, not an exhaustive
screen of every paper.

After those exclusions, there are 361 labelled Ling entries and 545 Luna entries.
Only **54 candidate pairs across 34 papers** survive the conservative matcher and
the two scope corrections: **15.0% of Ling entries and 9.9% of Luna entries**.
There remain 307 and 491 unmatched entries respectively.

For those 54 selected candidates only:

- Outcome agreement: 48/54 (88.9%), kappa 0.824.
- Type agreement: 40/54 (74.1%), kappa 0.382.
- Original DOI agreement when at least one DOI is supplied: 26/31 (83.9%).

**The outcome kappa does not pass a publication gate.** It describes a small,
selected set with 45 identities not source-verified here. Changing the similarity
threshold from 0.55 to 0.75 changes the candidate count from 85 to 33. This
coverage problem is more important than a seemingly reassuring headline kappa.

The deliberately difficult source-verified subset has nine pairs from nine
papers: outcome agreement 4/9 and type agreement 4/9. This is a disagreement-rich
diagnostic selection, not an unbiased estimate of either coder's accuracy.
The two coders report equal entry counts for 111/204 jointly represented papers;
equal counts alone do not establish that the effects are the same.

The [20-paper source review](source_review_20.md) documents each decision with a
local source passage, line number and file hash. Main findings:

- **Eligibility:** five eLife registered protocols describe planned experiments,
  not completed replication outcomes. Reported original results can be mistaken
  for replication results. “Teleological generics” also needs a human decision
  about original theory tests versus conceptual replications of prior findings.
- **Inputs:** the sports paper's primary rendition has table captions but no
  study-level table bodies. The scarcity audit reports 19 study results, with
  many details and references in an unavailable local SI. These cannot support
  exhaustive coding from the current single-shot input. The OSF document is a
  revision notice.
- **Codebook:** replication of an original null finding needs an explicit rule.
  Non-significance neither proves equivalence nor automatically means a failed
  replication. Mixed endpoints and follow-up times also need consistent units.
- **Interpretation:** several failure/inconclusive disagreements concern an
  acknowledged procedural confound, uncertain ordinal outcome or mixed evidence.
  They are not all mismatches or generic model mistakes.
- **Type:** added controls, population generalization and changed procedures
  produce genuine direct/close distinctions. Shared original DOI must not merge
  the experiments in the authority-placement or TOMM40 papers.

## Next release gate

Build the better reference set before spending the remaining budget on another
large extractor run. Have a human reconcile eligibility and explicit effect
identities in these 20 reviewed cases, resolve the null-result and endpoint rules,
and repair the missing tables/SI inputs. Then create a frozen, human-adjudicated
reference with separate development and held-out papers, including diverse
disciplines and multi-effect papers. Complete the six unattempted extractions
against that frozen version and score the base extractor with paper-level
uncertainty intervals. Any changed coding definitions require a codebook version
and consistent recoding; none were changed during this audit.

No new website evaluation page was published and no human gold was fabricated.

## Reproduction and checks

From the repository root, run:

```bash
python benchmarking/audit_coding.py
python benchmarking/audit_inventory.py
python benchmarking/audit_source_review.py
python benchmarking/audit_agreement.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest benchmarking/test_ai_coder.py benchmarking/test_harness.py benchmarking/test_audit_coding.py -q
```

The four scripts are local-only and do not make model calls. Reproduction uses
the saved raw replies, snapshots and current source files. Snapshot hashes and
source-passage hashes provide drift checks; the production CSV hash is recorded
in `inventory_summary.json`. Original coding CSVs matched their snapshots after
the work. **49 regression tests passed**, including rerun idempotence, retaining
positive entries on negative anchors, schema/truncation handling, effect identity,
cross-coder ID separation, conservative recovery and fail-closed gold export.
