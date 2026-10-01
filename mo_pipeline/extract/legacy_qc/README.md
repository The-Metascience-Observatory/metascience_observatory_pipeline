# Legacy QC scripts (quarantined 2026-10-01)

These scripts are not part of the eight-stage pipeline: no stage, the dashboard, or
`extract.py` calls them. They are kept for reference, not for use.

**Do not gate, filter, or label anything on their output.** They have not been
benchmarked against human ground truth.

- `check_if_replication_study.py`: agentic screener asking "does this paper contain
  replications?"
  - It defines its own four-type taxonomy in an inline prompt: technical, direct,
    close, conceptual.
  - The authoritative taxonomy in `prompts/prompt_shared_core.md` (invariant 3) is
    different: direct, close experiment, close extension, conceptual.
  - On the 2026-09 core trial it disagreed with the extractor on 11 of the 32
    overlapping papers, always in the same direction. The extractor was right in 3
    of the 4 disagreements that were audited (`benchmarking/results/core_trial100_NOTES.md`).
- `sanity_check_if_original_study_is_right.py` and its wrapper
  `run_sanity_check_on_unvalidated.py`: a metadata-only check (titles and journals)
  of whether a replication plausibly targets its claimed original.
  - The prompt leads the model: a "replication" in the title is "strong evidence
    of plausibility" whatever the original is.
  - The check never reads the paper, so it cannot verify which original was
    replicated.

To bring either back, first load the taxonomy from `prompts/prompt_shared_core.md`
instead of an inline copy. Then benchmark it with `benchmarking/harness.py` against
human labels (for originals: FLoRa/FReD original DOIs) before relying on it.
