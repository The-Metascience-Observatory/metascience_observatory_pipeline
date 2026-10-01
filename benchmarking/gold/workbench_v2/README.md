# Ground-truth improvement workbench

This is a development reference set awaiting human adjudication, **not scored gold**.
The original coding sheets, production database and existing gold release are unchanged.

## Improvements made

- All 206 papers have source-linked review packets and discipline metadata.
- Both coders' 1,052 rows are retained as candidates, including unlabelled rows.
  These are not 1,052 unique effects. No match is inferred from a shared DOI.
- FLoRa expected verdicts and imported original hints are omitted from the packets.
  This does not undo earlier exposure to hints or make this frame a blinded holdout.
- Six registered protocols are flagged for exclusion from outcome scoring:
  eLife 11566, 11999, 09976, 11414, 10860 and newly identified 04363.
  Keep them for eligibility evaluation if useful; they are not outcome failures.
- The sports paper's image tables have been visually transcribed into 25 separate
  study proposals: 7 successes, 9 informative failures, 7 practical failures and
  2 inconclusive results. Original references and PDF page/table evidence are retained.
  Replication type and precise contrast/timepoint still need review. A negative
  estimate does not automatically justify changing an author's failure to reversal.
- The scarcity authors' public study-summary table was recovered. It identifies
  20 attempted studies, but does not establish outcomes for the 19 reported studies.
  Do not score a paper-level summary as an exhaustive effect list.
- The OSF revision-notice input remains excluded from outcome scoring pending recovery
  of the actual paper. Twenty-three papers still lack a subdiscipline.

## Start reviewing

`registry.csv` links every paper to a source/candidate packet and a separate decision file.
Prioritize the sports paper, the six protocols, the scarcity source gap, and the
endpoint/type disagreements flagged by the prior source review. Do not interpret
the older sports “exclude_outcome” note as permanent: the tables are now recovered,
but human review is still pending.

1. Read the full source, tables and relevant supplements before accepting coder proposals.
2. Decide whether the document contains completed eligible replications. Record why.
3. Enumerate distinct findings using original reference, replication study, outcome,
   contrast and timepoint. Shared original DOI alone is insufficient. Use explicit
   “not applicable” with justification where a dimension genuinely does not apply.
4. Give each final finding a stable `effect_id`. Resolve **every** coder candidate to
   one or more final IDs, or reject it with a source-based reason. Add findings missed
   by both coders. Do not use agreement as proof of completeness.
5. Record evidence locations and source hashes, then assign result/type using the
   authoritative codebook (`codebook_v2`, prompt 8.9). The null-result and
   mixed-endpoint rules were agreed on 2026-09-28: one entry per *claim of the
   original*; timepoints/endpoints of one claim are one entry, decided by the
   original's primary timepoint or else inconclusive; controls get no entry; an
   original null claim that the replication also finds null is success. Do not
   invent further rules; flag a new boundary case as `unresolved` instead.
6. Only an actual human reviewer should fill `reviewer`, `reviewer_kind: human`,
   `reviewed_at`, `sources_checked` and `enumeration_complete`.

Decision fields are in each JSON template. `candidate_resolutions` maps candidate IDs
to objects such as `{"action":"map", "effect_ids":["study1:outcome1"], "reason":"..."}`
or `{"action":"reject", "effect_ids":[], "reason":"..."}`.
The `effects` array contains the final identity fields, labels, evidence and any
`unresolved` issues. Source transcription files are proposals, never approvals.

## Reproducibility and limits

From the repository root:

```sh
python benchmarking/prepare_gold_sources.py
python benchmarking/ground_truth_workbench.py build
python benchmarking/ground_truth_workbench.py status
```

Build regenerates machine packets but preserves all existing decision files.
Packet/source changes invalidate review checks. The manifest hashes the input frame,
clean coder sheets, source review and recovered sports records.
Status checks workflow completeness, not scientific correctness or human identity.
There is deliberately **no gold exporter** here: passing these checks is not permission
to publish accuracy. The older two-coder builder remains disabled.

All 206 papers have already been inspected or run through the pipeline. Use them for
development. Reserve the next approximately 50 papers as a genuinely new, discipline-
stratified holdout, selected before inspecting extractor answers. Obtain complete
sources and independent human decisions before spending extraction budget on it.

Public sources: [scarcity author table](https://osf.io/download/hgres/),
[scarcity repository](https://osf.io/a2e96/), and
[sports correction](https://d-nb.info/1383888531/34).
The correction concerns author names/affiliations, not the outcome table.
Downloaded originals and their hashes are recorded in `source_recovery.json`.
