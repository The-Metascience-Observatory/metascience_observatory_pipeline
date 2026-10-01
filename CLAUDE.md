# mo_pipeline — orientation for coding agents

Unified Metascience Observatory replication pipeline: search → dedup → prefilter →
classify → download → convert → extract (+ collate), plus a corpus catalog and a
FastAPI+Next.js dashboard. The former discovery and extraction projects are
consolidated under `mo_pipeline/`; their old directories are not part of this repo.
**Ingestion into the website database is NOT a pipeline stage**: it is run manually
from `metascience_observatory_website/data_ingestor/` (its own repo — the canonical
home of `data_ingestor.py`; a former copy here was removed 2026-08-09).
Read [README.md](README.md) first.

## Repository and license

- Canonical remote: [The-Metascience-Observatory/mo_pipeline](https://github.com/The-Metascience-Observatory/mo_pipeline)
  (`origin` fetch/push: `https://github.com/The-Metascience-Observatory/mo_pipeline.git`).
- Original code, prompts, and documentation use the [MIT License](LICENSE).
  The README acknowledgment request is voluntary, not a license condition.
  Research datasets and third-party paper PDFs/text are outside this license.
- The external ingester lives in the
  [website repository](https://github.com/The-Metascience-Observatory/metascience-observatory-website/tree/main/data_ingestor).
  The local sibling directory remains `../metascience_observatory_website/`;
  do not change local paths to match the hyphenated GitHub repository name.

## Running it

- Install once: `pip install --break-system-packages --user -e .` (system Python;
  `fetchpdf` (`../fetchpdf_public`) + `fetchpdf_grey` (`../fetchpdf-grey`) + `pdf4llm` are
  separate editable installs, not consolidated). `pyproject.toml` declares no
  dependencies, so third-party ones are installed by hand: `pip install litdown`
  for the rendition ladder's second rung (without it `corpus/litdown_render.py`
  returns None and rendering falls back to fetchpdf alone, as before).
- Dashboard: `./dev.sh up` starts the FastAPI API (:8090) + Next.js dashboard (:3010) →
  http://localhost:3010. `./dev.sh down` stops the two services but **never** the
  detached pipeline stages. `./dev.sh status` lists both plus any running stages.
- Every stage also runs standalone from the CLI — see **Stage CLIs** below. The dashboard
  is a thin wrapper: `registry.py`'s argv builders shell out to the exact same commands.

## Stage CLIs

All 7 stages are CLI-runnable. There are no console-script entrypoints (`pyproject.toml`
defines no `[project.scripts]`), so it's always `python -m ...`; run from the repo root.
Stage 6 is the external `pdf4llm` binary, not part of this package. The argv builders in
`server/app/registry.py` are the source of truth for the flags the dashboard passes.

| # | Stage | Command |
|---|-------|---------|
| 1 | Search | `python -m mo_pipeline.discover.search_for_replication_studies [--max-per-query N] [--sources a,b]` |
| 2 | Dedup | `python -m mo_pipeline.discover.deduplicate_candidates` (no args) |
| 3 | Prefilter | `python -m mo_pipeline.discover.prefilter_candidates` |
| 4 | Classify | `python -m mo_pipeline.discover.classify_candidates [--workers N] [--limit N]` |
| 5 | Download | `python -m mo_pipeline.discover.download_all_confirmed [--doi-csv F] [--type T] [--include-published] [--limit N] [--workers N] [--legalonly] [--no-download-xml] [--no-to-markdown] [--download-si] [--refresh-si] [--max-si-mb N] [--delay S] [--backfill-structured] [--backfill-pdf] [--cookies F] [--no-cookies] [--cookies-only] [--cookies-max N]` |
| 6 | Convert | `pdf4llm batch <inbox> -o <papers> --mode full-grobid --workers 4 --movepdf --resume` |
| 7 | Extract | `python -m mo_pipeline.extract.extract <papers_dir> --batch --level full [--tag T] [--workers N] [--include-list F] [--model M] [--usecodex] [--dontcheck] [--collate-only]` |
| 7b | Extract (core, no statistics) | `python -m mo_pipeline.extract.extract_core <papers_dir> [--tag T] [--workers N] [--include-list F] [--provider claude_cli\|openrouter] [--model M] [--dontcheck] [--collate-only] [--show-prompt]` |

After stage 7's collate, ingest the collated CSV manually:
`cd ../metascience_observatory_website/data_ingestor && python data_ingestor.py <collated.csv> --no-gui`,
then stamp the corpus with `python -m mo_pipeline.corpus mark-ingested <collated.csv> --db-version ...`.

Running by hand **bypasses the runner's mutex groups** (see invariant 6) — a manual
launch will happily collide with a detached stage. Check `./dev.sh status` first.
Stage 6 needs GROBID answering at `localhost:8070`. Dashboard conversion preflight
checks available RAM, starts GROBID through `server/app/grobid.py`, and refuses
to launch if it cannot answer. For direct CLI conversion, start it first with
`./dev.sh grobid up`; `./dev.sh grobid status` and `./dev.sh grobid down` manage
the same service. The lifecycle helper uses host networking when creating the
container and leaves a running but unreachable container untouched. Its settings
are `MO_GROBID_URL`, `MO_GROBID_CONTAINER`, `MO_GROBID_IMAGE`, and
`MO_GROBID_START_TIMEOUT`. Adjacent CLIs outside the 7 stages: `python -m mo_pipeline.corpus
<cmd>`, `mo_pipeline/discover/doi_runs.py`, and the `label_centrality/` pilot modules.

## Invariants that matter

1. **`mo_pipeline/config.py` is the single source of truth for paths.** Never hardcode
   a path in a stage script — import it from config. Env overrides: `MO_DATA_DIR`,
   `MO_PROGRESS_DIR`, `MO_MEDIA_ROOT`, `MO_WEBSITE_DATA_DIR`. `python -m mo_pipeline.config`
   self-checks every path.
2. **The website data dir is read-only for this repo.** The production
   `replications_database_*.csv`, `version_history.txt`, and growth PNGs live in
   metascience_observatory_website/data and are written only by the manual ingest
   run from that repo's `data_ingestor/`. The pipeline just reads
   `config.WEBSITE_DATA_DIR` paths (ontology for extract, version_history for
   latest-DB lookups); `MO_WEBSITE_DATA_DIR` can redirect them for tests.
3. **Shared extraction content lives in `prompts/prompt_shared_core.md` only** (schema,
   taxonomy, field reference, discipline list). Mode files (`prompt_full*.md`,
   `prompt_core.md`) hold only the read-the-paper workflow. Bump `prompts/version.txt`
   on any prompt edit. Passages that differ between renderings are wrapped in
   `<!-- mode:TAG -->…<!-- /mode -->` blocks along **two independent axes**:
   statistics (`full` records them, `core` never does) and output channel
   (`write` = agentic, save `result.json`; `reply` = single-shot, answer inline).
   `extract._TAGS_FOR_LEVEL` maps each `--level` to its tag set and
   `_render_mode` keeps the matching blocks, so `--level base` is stat-free AND
   agentic. Conflating the two axes is what silently lost two papers in the
   2026-09-02 pilot: base rendered "reply with the JSON object only", so the
   agent answered inline and never wrote `result.json`. Never write a second
   copy of the taxonomy for a stat-free prompt.
4. **The corpus catalog is an index, never truth.** `corpus.sqlite` is rebuildable from
   `papers/` via `python -m mo_pipeline.corpus scan`. The filesystem (files present per
   folder) is the source of truth for status; `paper.json` holds only what a scan can't
   derive (source_batch, ingested record). Folder↔DOI encoding lives **only** in
   `corpus/models.py` (`doi_to_folder`/`folder_to_doi` — never inline a `--`↔`/`
   replace): `/`→`--` (decoded by replacing **every** `--`; handles multi-slash
   OSF/journal DOIs), `:`→`~`, NTFS-forbidden `< > " \ | ? *`→`~XX~` hex tokens,
   and hyphens that are part of a `--` run or adjacent to `/`→`~2d~` (a literal
   `--` occurs in real ASEE DOIs and must stay distinct from the slash encoding).
   Twins that must stay in sync: `fetchpdf.doi_to_safe_filename`, `pdf4llm/doi_codec.py`.
   Invariant check: `python -m mo_pipeline.corpus repair-names --check` verifies
   every folder's `doi.txt` round-trips to its name.
5. **Per-paper folder layout is kept exactly.** PDF moved into `{doi}/` at conversion;
   each extraction run writes a `{tag}/` subfolder. Do not flatten or restructure.
6. **Stages run detached** (`server/app/runner.py`): state under `~/.local/state/mo_pipeline/`.
   They survive API restarts; `dev.sh down` never kills them. Mutex groups: `claude_cli`
   (classify+extract; still enforced for Codex/core extraction), `heavy_ram`
   (dedup+convert), `inbox` (download+convert must not overlap while PDFs are
   written/moved), and `self` (per-stage singleton, NOT cross-blocking).
7. **Stage 5 fetches structured full text AND the PDF, one folder per record.**
   It queues confirmed replications of `config.DOWNLOAD_REPLICATION_TYPES`
   (direct, close, conceptual; `--type all` lifts it) minus DOIs already in the
   published database (`shared/production_db.published_dois`; `--include-published`
   keeps them). Explicit `--doi-csv` lists bypass the type and published-DOI
   filters. The old direct-only "filter_direct" stage was removed 2026-10-01.
   fetchpdf's `--get-xml-or-html` (on by default; `--no-download-xml` opts out)
   fills two goals per record — `{stem}.xml`, else publisher
   `{stem}.fulltext.html`, plus `{stem}.pdf` — written to `inbox/{stem}/`
   (fetchpdf `--make-subfolder`; `{stem}` is the same `doi_to_folder` name the
   paper will have in `papers/`). XML is the preferred LLM-extraction input; the
   PDF is still always attempted (figures, supplements, the rendered page).
   Goals are filled per goal from disk, so the same walk backfills:
   `--backfill-structured` fetches only the missing XML/HTML half and
   `--backfill-pdf` only the missing PDF half for records already on the drive
   (`papers/` and `inbox/`, same shape), re-downloading nothing. Stage 6 moves
   only the PDF into `papers/{doi}/` (pdf4llm globs `**/*.pdf` and names the
   output by PDF stem), so run `python -m mo_pipeline.corpus adopt-structured
   --execute` after a convert to bring the rest of the folder across. The library
   is `../fetchpdf_public` (PyPI `fetchpdf`); `import fetchpdf_grey`
   (`../fetchpdf-grey`) adds the Sci-Hub last resort and owns `disable_last_resorts`.
   Fetch through `mo_pipeline/shared/fetch.py`, never `from fetchpdf import` directly:
   fetchpdf reads the Elsevier key at first import, and only fetchpdf_grey loads it.
   Records downloaded flat before this layout: `corpus inbox-subfolders`.
   Institutional access (fetchpdf's cookie route, Wiley/T&F/SAGE/Springer/
   Royal Society/Hogrefe/INFORMS) is OFF until `get-cookies setup` has been run;
   after that every fetch tries it right after Unpaywall, reading the cookies
   live from the configured browser (`~/.config/fetchpdf/access.json`). Stage 5:
   `--cookies F` overrides the source, `--no-cookies` skips it, `--cookies-only`
   runs it alone (no grey, PDF-only) for DOIs whose chain already failed, and
   `--cookies-max` caps it (default 5000).
   Every run ends with fetchpdf's per-source cost table (calls/hits/seconds) and
   leaves `source_counts.json` at the inbox root — read it before cutting a source.
8. **Extraction reads a tier ladder, and a rendition must pass a prose gate.**
   Stage 7's default mode inventories the folder (`extract.paper_artifacts`) and
   names one PRIMARY: `{stem}_from_xml.md` > `{stem}_from_html.md` > `body.md`,
   with the PDF always last. Lower tiers stay available as *gated* fallbacks —
   image-tables, and a bibliography the higher tiers lack. Renditions are written
   by exactly one writer, `corpus.render.render_dirs` — called by stage 5 on the
   record folders it just filled (default; `--no-to-markdown` skips it) and by
   `python -m mo_pipeline.corpus render-markdown --execute` over the drive —
   and never by fetchpdf's own `--to-markdown`, which converts unconditionally
   while stage 7 trusts any rendition it finds. The conversion is a **two-rung
   ladder**: fetchpdf first (prose → Markdown, every table → canonical HTML so
   colspan/rowspan survive; Elsevier `ce:`/CALS documents included since
   2026-09-02), and where the prose gate refuses that output,
   `corpus/litdown_render.py` retries the XML with **litdown** and keeps its
   result if it passes the same gate. The second rung only ever runs on a file
   already rejected, so it cannot make a record worse. It unwraps PMC's
   `<pmc-articleset>` (litdown dispatches on the root element) and splices
   fetchpdf's canonical HTML tables over litdown's expanded grids, since
   fetchpdf's table walker is good on these files even where its prose walker
   husks. Every rendition's front matter names its `converter:` and its real
   `table_format:`. Measured on the 25 unrendered corpus XMLs (2026-09-03):
   fetchpdf alone cleared the gate on 11, the ladder on 23 — and litdown is the
   only engine tried that reads Elsevier at all (docling returns 2 characters,
   pandoc dumps bare metadata). `render.MIN_PROSE_LINE`
   (median prose-line length ≥ 60) refuses a rendition that looks like full text
   and is not; calibrated 2026-08-26 on the pre-fix Elsevier husks (354/355 cut,
   115/120 good kept) and kept as the guard against the next converter
   regression. A rejected rendition is not a loss — the paper simply stays on
   the GROBID tier, which is where it was before.
9. **Search keywords are code defaults + a runtime overlay.** The curated lists live in
   `discover/queries.py`; the dashboard edits `data/keywords.json` (via
   `discover/keywords.py`), which overrides them per-list. Which list feeds which source is
   `keywords.API_FANOUT`, the one definition the search and the yield stats both read. Edit
   terms via the dashboard **Keywords** page or that JSON — do not expect source edits to be
   the only path.
10. **Versions move independently; a git commit is not enough.** The pipeline code
   (`mo_pipeline.__version__`, which `pyproject.toml` reads — never restate it there),
   the extraction prompt (`prompts/version.txt`, stamped on every row as `ai_version`),
   the matcher prompt (`prompts/version_match.txt`, part of the judge's cache key) and
   the evaluator (`benchmarking/harness.py` `HARNESS_VERSION`, recorded in every
   `metrics.json`) each version separately. The centrality pilot also tracks
   `prompts/version_centrality.txt`. Because this repo is edited by several
   sessions at once and can sit uncommitted for a day, a run's HEAD commit may predate
   the code that ran — every extraction on 2026-09-02 recorded commit `6774905`, which
   contains none of that day's work. So `provenance.json` also carries `prompt_sha256`
   and `code_fingerprint` (`mo_pipeline/version.py`), which identify a dirty run by
   content. **Bump `prompts/version.txt` and add a `prompts/CHANGELOG.md` section on any
   prompt edit**; `python -m mo_pipeline.version` prints the manifest and exits 1 if the
   prompt files have drifted from the hashes recorded for the current version.

## The corpus (on `/media/dan/data/metascience_observatory_pdfs/`)

Reorganized into a flat layout (the old ad-hoc `ingested/` tree is gone):
`papers/` (one folder per DOI — the corpus), `inbox/` (one folder per downloaded
record awaiting conversion, same folder names), `special/` (pre-pipeline corpora), `legacy/` (migration dup-losers +
malformed-name leftovers, reviewable), `corpus.sqlite`, and `migration_{plan,log}.csv`.
Status is derived from files present: downloaded → converted → screened → extracted →
ingested. Corpus CLI (`python -m mo_pipeline.corpus <cmd>`): `scan`, `stats`, `coverage`,
`include-list` (feeds `extract --include-list`), `mark-ingested` (stamp after a
manual ingest run), `adopt-structured` (move what stage 6 left in `inbox/{doi}/`
into `papers/{doi}/` — dry-run unless `--execute`), `inbox-subfolders` (one-time:
move flat pre-layout inbox files into `inbox/{doi}/` — dry-run unless `--execute`),
`render-markdown` (write the missing `_from_xml.md`/`_from_html.md` for XML/HTML
already on the drive, gated on prose quality — dry-run unless `--execute`),
`repair-names`. (The one-time `inventory`/`migrate`/`sweep` reorg tools were removed
2026-10-01; they live in git history.) `converted` now
means "readable full text present" — abstract.md + body.md **or** a rendition —
so an XML-only paper is visible to `include-list --status converted`.

## Dashboard

`dashboard/src/app/`: `/` (pipeline overview + run/stop + system strip), `/stages/[id]`
(probe detail, run form, log tail), `/corpus` (catalog stats + browse), `/keywords`
(view/edit the four search-keyword lists), `/keywords/stats` (source yields), `/doi-runs` (run download→convert→extract→
collate on an explicit DOI list — paste/upload/point at a CSV, DOI column
auto-detected; runs live at `data/doi_runs/<slug>/`, extraction tag = slug; backed by
`discover/doi_runs.py` + `--doi-csv` on stage 5; the resulting collated CSV is ingested
manually). Frontend proxies `/api/*` to the FastAPI
app (`server/app/main.py`); the 7-stage registry + probes are in `server/app/registry.py`.

## Taxonomy (authoritative in `prompts/prompt_shared_core.md`)

Four replication types: **direct**, **close experiment**, **close extension**, **conceptual**.
Four result categories: **success**, **failure**, **inconclusive**, **reversal**.
Do not duplicate these definitions elsewhere.

## Gotchas

- Extract reads the XML by way of its markdown rendition, never the raw markup
  as full text (invariant 8) — with **one** exception, the ladder's last rung: a
  folder with no rendition, no `body.md` and no PDF gets the raw `.xml` as its
  PRIMARY (`extract.paper_artifacts`, tier `raw_xml`). Some documents defeat
  every converter (Wiley's `<component>` schema does), and 429 folders on
  2026-09-03 held markup and nothing else, so the alternative was no full text
  at all. Raw markup is ~3.7x the tokens of its rendition, so it is offered only
  when nothing else exists, and never under `--force-tier`. Stage 6 (`pdf4llm batch`) still converts PDFs only.
  The raw `{stem}.xml` stays addressable for exactly one job: **it holds the
  reference list and the rendition does not**. fetchpdf's converter walks JATS
  `<body>`, and a JATS bibliography lives in `<back><ref-list>` — so a
  `_from_xml.md` has no references at all. `extract._describe_artifacts` points
  the agent at `references.json`, then the raw XML's `<ref-list>`, then the PDF.
- Elsevier XML (428 of 551 corpus XMLs, `full-text-retrieval-response`) renders
  since fetchpdf 0.1.1 (2026-09-02): the converter knows `ce:section`/`ce:para`
  and converts CALS tables, and `ce:floats` (outside `ja:body`, where 94% of
  Elsevier tables live) are emitted at their `float-anchor`. Renditions written
  before that date for these files do not exist (the gate refused them), so
  `render-markdown --execute` fills them in; nothing needs `--overwrite`.
- **Stat-free extraction has two shapes.** `extract_core.py` (stage 7b) is single-shot:
  Python assembles abstract + full text + reference list (references.json →
  references.md → raw XML bibliography → PDF tail pages) and makes ONE no-tools call
  via `discover/screening_backend.py` (`claude_cli` default, `openrouter` optional), then
  reuses extract.py's validators, DOI enrichment and collate. It writes
  `{folder}_result_core.json` with `ai_version` `<version>-core`; the 14 statistical
  fields are stripped even if the model emits them, so the collated CSV keeps its
  shape with blank stat columns. `extract.py --level base` is the agentic extractor
  with the same stat-free prompt rendering (`ai_version` `<version>-base`, writes
  `_result.json`) — the control arm for benchmarking core against the agent. Normal
  extraction is `--level full`; `mid` is gone. Use one tag per mode: collate prefers a
  full result over a core one in the same tag dir. Consequences of blank statistics:
  the website's recomputed-outcome views treat such rows like every other stat-less
  row (excluded/inconclusive; the "reported" view uses `result`), and the ingestor's
  auto-dedup keys on `replication_n/es/es_type`, so re-ingesting a core row over a
  stat-bearing row goes to the manual duplicate review.
- **Agentic extraction supports Claude and Codex.** Claude is the default; use
  `--usecodex --model <codex-model-id>` for Codex, with `--level full` or `base`.
  Codex runs `codex exec` in the paper folder and retains validation, DOI enrichment,
  provenance, and collation, but skips the Claude-specific second-pass reviewer.
  The dashboard exposes `usecodex`, `level`, and `model`; single-shot core uses
  `--provider` instead.
- Every extraction writes `<paper>/<tag>/provenance.json` (model, prompt hashes, input
  tier, CLI version, commit). `--force-tier` exists for benchmark tier studies only.
- `benchmarking/archive/legacy_feb2026/` is quarantined: its ground truth was partly
  written by the V6 pipeline. Do not score against it or cite its numbers.
  `config.PROMPT_FILES["base"]` uses `prompt_full.md` with statistics stripped;
  `mid` is unsupported. `prompt_full_xml.md` is archived under `prompts/archive/`;
  normal XML extraction uses the shared tier-aware prompt.
- Classify's checkpoint is guarded: `classify_progress.json`'s stored `input_row_count`
  must match the current `candidates_filtered.csv` row count. Regenerating prefilter output
  (e.g. after adding search terms) invalidates it — the dashboard pre-flags this as `stale`;
  fix by deleting the progress file to reclassify from scratch.
- Re-running search is incremental and safe: `search_progress.json` keys completed queries
  by `<api>:<query>`, so only new terms fire; `candidates_raw.csv` dedups.
- The corpus moved on 2026-09-05 from the external USB drive `/media/dan/500Gb`
  (failing: unrecoverable read errors, pending sectors) to the internal NVMe
  `/media/dan/data` (xfs, 1.5TB free that day). **Treat the old drive as
  read-only salvage; never write to it or point `MO_MEDIA_ROOT` at it.** Check
  `df -h /media/dan/data` before any bulk pull (`--download-si` downloads files up
  to 300MB each; the XML backfill is ~75KB/paper). Prefer catalog queries over
  walking `papers/` (15k+ folders). Corpus scans take minutes; `catalog.connect()`
  uses `busy_timeout` so a long scan and the dashboard's `/corpus` reads don't deadlock.

## Where things are

Discover + download stages `mo_pipeline/discover/` (+ `keywords.py` overlay);
extract `mo_pipeline/extract/extract.py` (agentic, all fields) + `extract_core.py`
(single-shot, no statistics; tests in `extract/test_extract_core.py`); ingest (manual, external)
`../metascience_observatory_website/data_ingestor/data_ingestor.py`;
corpus `mo_pipeline/corpus/` (models, catalog, render, adopt); shared metadata
fetchers, the fetchpdf shim and the published-DB reader `mo_pipeline/shared/`; orchestrator `server/app/`; dashboard `dashboard/src/app/`.
`benchmarking/` is the extraction benchmark: `harness.py` (evaluate / retest / status /
run, and the CLI for everything), `gold_build.py` (the gold-set toolchain), `matching.py` (DOI -> Haiku judge -> one-to-one assignment),
`codebook.md`, `gold/`, `silver/`, `results/`, `recall_harness.py`; read
`benchmarking/README.md` first. Ground truth rows carry `provenance`; anything
`pipeline:*` is refused for scoring. Benchmark extractions **must** run with
`--dontcheck` (every FLoRa/FReD DOI is already in the production DB, so the default
skip would silently drop them); `harness.py run` does this for you.
`mo_pipeline/label_centrality/` is an emerging pilot (claude-vs-human agreement on whether
a database row's claim is central to its original paper; rubric in
`prompts/prompt_centrality.md`) — not yet part of the main 7-stage flow.

## Current benchmark workflow

Read [benchmarking/gold/workbench_v2/README.md](benchmarking/gold/workbench_v2/README.md)
before ground-truth work. This is a development reference set awaiting human
adjudication, not scored gold. Review from `adjudication/` views, which withhold
the designated system-under-test coder's candidates during blind enumeration.
Only an actual human reviewer may mark decisions as human-reviewed.
`benchmarking/ground_truth_workbench.py` provides `validate` and
`export --kind human|ai_adjudicator`; keep those exports separate. The old
two-coder gold builder is disabled. FLoRa paper-level verdicts are excluded from
effect-level extraction scoring. The harness also provides `originals` and
`originals-select` for original-study identification; consult its parser for flags.

## Checks after changes

Run focused checks for the component changed, from the repo root:

- Python: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest <test-file-or-directory> -q`.
  Tests live alongside modules in `mo_pipeline/`, under `server/tests/`, and in
  `benchmarking/test_*.py`. Use scratch data and the path overrides above when needed.
- Dashboard: `npm --prefix dashboard run typecheck`.
- Prompt edits: `python -m mo_pipeline.version` checks recorded prompt hashes;
  update the extraction prompt version and changelog as described above.
- Documentation: check referenced paths and CLI flags against source, then run
  `git diff --check`. Documentation-only edits do not require pipeline runs.
