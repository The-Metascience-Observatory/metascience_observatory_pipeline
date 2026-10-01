# Prompt changelog

The version in `prompts/version.txt` is stamped onto every extracted row as
`ai_version` (with a `-base` or `-core` suffix for the stat-free renderings) and
into each run's `provenance.json`. Comparing two benchmark runs is only
meaningful if you can say what differed between their prompt versions, so every
change to a file in `prompts/` gets an entry here.

**Bump `version.txt` whenever you edit a prompt file, and add a section below
with the new hashes.** `python -m mo_pipeline.version` compares the files on
disk against the hashes recorded for the current version and fails if they have
drifted, which is how an unversioned prompt edit gets caught.

Hashes are sha256 of the file as stored, not of the rendered prompt: rendering
also substitutes the discipline ontology, which lives in the website repo and
changes on its own schedule.

## Prompt 8.10 (2026-10-01)

`prompt_full_xml.md` is retired to `prompts/archive/`, and its `"xml"` entries are
gone from `config.PROMPT_FILES` and `extract._TAGS_FOR_LEVEL`. No prompt text
changed: the five remaining files hash exactly as in 8.9, so every rendering
(`full`, `base`, `core`, `pdf_only`, `html`) is byte-identical to 8.9, and rows from
the two versions are comparable. The bump is needed because the file set changed:
the recorded 8.9 hashes include `prompt_full_xml.md`, so without one the drift
check would flag the removed file.

Nothing has selected that prompt since 2026-09-02 (`6e1cf63`), when the XML
auto-detect was replaced by the tier ladder. An XML-only folder now gets
`prompt_full.md` and a raw-markup note in its user message. The prompt was a JATS
guide (`<sec>`, `<table-wrap>`, `<pub-id>`), but on 2026-10-01 none of the 51
folders that reach the raw-markup rung held JATS. 40 were Elsevier
`<xocs:rawtext>` OCR text, 10 were metadata-plus-abstract husks, and 1 was
structured Elsevier markup. The file was last edited 2026-07-13, so it also
lacked everything added to `prompt_full.md` since then, including the `mode:core`
blocks. Historical
`_result_xml.json` files are still recognized by the skip check, collate and the
benchmark harness.

| prompt file | sha256 |
|---|---|
| prompt_core.md | 2d706b482c3d32c3d2dcf991c605aff56278f126bdfa125e92eff3e1b226542b |
| prompt_full.md | c8e41ffa773e71e84a9b1e78c389a7f88fa39d865c78407caefde95693e151ea |
| prompt_full_html.md | 16f3d1c13006d856b418caafb9be388261931a94f6ca32177f18493144db9968 |
| prompt_full_pdf_only.md | af6414299e2b4bb458598f871a63f31ef11156c3217022f092f2b05991758db9 |
| prompt_shared_core.md | bea2fba2524789eef7359ebe2e451081f12ab1028c8427586eeb5f69e2290248 |

## Housekeeping 2026-10-01 (no version bump)

`prompt_screen.md` was deleted along with `extract.py --screen`, which stage 4
(classify) superseded. It was never part of the extraction prompt or its hashes,
so `ai_version` is unchanged.

## Prompt 8.9 (2026-09-28)

Two holes in the taxonomy, found by the 2026-09-18 gold-coding audit
(`benchmarking/results/audit_2026_09_18/`). Both change definitions, so rows coded
under 8.8 are not comparable with 8.9 on these two points; the benchmark codebook
moved to `codebook_v2` in step. Only `prompt_shared_core.md` changed.

- **Unit of coding is now "one row per claim of the original".** The old text said
  an entry is NOT each dependent variable within a study, and then, in Edge Cases,
  one row per effect (compassion, empathy, Theory of Mind) — which are three
  dependent variables. Two independent AI coders split the same 206 papers into 367
  and 598 entries, and the line between "a DV" and "an effect" is where they parted.
  A *claim* is now a distinct finding the ORIGINAL authors reported. Several
  measures, tests or mediator paths of one claim are one entry; several
  timepoints/endpoints of one claim are one entry, classified by the original's
  stated primary timepoint, else `inconclusive` if they differ. Controls get no row.
- **Replication of an original null claim.** `success` was defined as a significant
  effect in the same direction, so a faithful replication of an original *null*
  finding read as `failure`. New subsection: where the original claimed no effect,
  a replication that also finds none (and the authors read as consistent) is
  `success`, a significant effect is `failure`, and an under-powered replication is
  `inconclusive`. Negative/positive controls are not claims and get no row. The
  Registered-Report tiebreaker is qualified to apply only when the original claimed
  an effect.

| prompt file | sha256 |
|---|---|
| prompt_core.md | 2d706b482c3d32c3d2dcf991c605aff56278f126bdfa125e92eff3e1b226542b |
| prompt_full.md | c8e41ffa773e71e84a9b1e78c389a7f88fa39d865c78407caefde95693e151ea |
| prompt_full_html.md | 16f3d1c13006d856b418caafb9be388261931a94f6ca32177f18493144db9968 |
| prompt_full_pdf_only.md | af6414299e2b4bb458598f871a63f31ef11156c3217022f092f2b05991758db9 |
| prompt_full_xml.md | 6b3253f6281e17fb1a6f74bd073b9f586d2e68a7ef1f05d0ace0dc73f6f7051c |
| prompt_shared_core.md | bea2fba2524789eef7359ebe2e451081f12ab1028c8427586eeb5f69e2290248 |

## Prompt 8.8 (2026-09-02)

Fixes from the audit of the `base_87_r1` FLoRa pilot (30 papers).

- **Output channel is now independent of the statistics axis.** `--level base`
  had inherited the single-shot instruction "Reply with the JSON object only",
  so the agentic run answered inline and never wrote `result.json`: 2 of 30
  papers in the pilot were lost that way. Mode markers carry two axes now —
  statistics (`full` / `core`) and output channel (`write` / `reply`) — and
  `extract._TAGS_FOR_LEVEL` maps each level to its pair. A test asserts every
  agentic level is told to write `result.json`.
- **Named-but-uncited original** (`prompt_full.md`, Pass 2). When a paper
  replicates a named study, programme or model that it never cites, do not
  substitute an adjacent citation by the same group (a commentary, review or
  policy piece). Record the programme name in `original_title`, leave
  `original_url` empty for downstream resolution, and lower confidence. One
  pilot paper had a same-group commentary recorded as the original.
- **`confidence_notes` removed** (3 sites in `prompt_full.md`). The prompt told
  the agent to write to a field that has never existed in the output schema, so
  anything written there was silently discarded. Replaced with `explanation`.

Not a prompt change, shipped alongside: review-round guardrails, an explicit
`--model` for the reviewer, a subtitle-aware DOI title gate, and a husk gate —
all in `mo_pipeline/extract/`.

| prompt file | sha256 |
|---|---|
| prompt_core.md | 2d706b482c3d32c3d2dcf991c605aff56278f126bdfa125e92eff3e1b226542b |
| prompt_full.md | c8e41ffa773e71e84a9b1e78c389a7f88fa39d865c78407caefde95693e151ea |
| prompt_full_html.md | 16f3d1c13006d856b418caafb9be388261931a94f6ca32177f18493144db9968 |
| prompt_full_pdf_only.md | af6414299e2b4bb458598f871a63f31ef11156c3217022f092f2b05991758db9 |
| prompt_full_xml.md | 6b3253f6281e17fb1a6f74bd073b9f586d2e68a7ef1f05d0ace0dc73f6f7051c |
| prompt_shared_core.md | bac318f8a72baa3dda077f74f5770d4026bb6717277dd49f574557f42124245d |

## Prompt 8.7 (2026-09-02)

Introduced the statistics-free rendering, so one shared core serves both the
full extractor and the two stat-free modes.

- Statistics-only passages in `prompt_shared_core.md` and `prompt_full.md`
  wrapped in `<!-- mode:full -->` blocks, with stat-free siblings in
  `<!-- mode:core -->` blocks; `extract._render_mode` keeps one side per level.
- New `prompts/prompt_core.md` for the single-shot `extract_core.py`.
- `ai_version` gains a level suffix: `8.7-base`, `8.7-core`. Full-mode rows stay
  unsuffixed.
- The rendered **full** prompt was byte-identical to 8.6, verified by test, so
  full-mode results carry across the bump unchanged.

Hashes not recorded: 8.7 existed only as an uncommitted working tree and was
superseded the same day.

## Prompt 8.6 and earlier

Predate this changelog. **8.5 is the last prompt version committed to git**
(commit `6774905`); 8.6 was an uncommitted working-tree state. Runs tagged
`flora_pilot` carry `ai_version` 8.5, and their `provenance.json` records the
prompt hashes, which remains the only reliable identification of what they ran.

# Matcher prompt changelog

`prompts/prompt_match.md`, versioned by `prompts/version_match.txt`. The version
is part of the benchmark matcher's cache key, so a bump re-judges affected pairs
rather than reusing stale decisions.

## Matcher 1.1 (2026-09-02)

- Two DOIs can identify the same work: a preprint or postprint of the published
  article, a second registration (JSTOR beside the publisher's own), or a
  **Registered Report** standing in for the study it set out to replicate. The
  judge previously called all of those "different originals".
- Added: a ground-truth entry can simply be wrong about which study was
  replicated, most often naming another paper by the same authors. Judge from
  the described effect and the row's citation sentence, not the metadata alone.

## Matcher 1.0 (2026-09-02)

Initial version, with the rebuilt benchmark harness.
