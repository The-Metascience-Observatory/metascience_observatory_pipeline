# mo_pipeline — orientation for Claude Code agents

Unified Metascience Observatory replication pipeline: search → dedup → prefilter →
classify → download → convert → extract → ingest, plus a corpus catalog and a
FastAPI+Next.js dashboard. Consolidated from three former directories (see the
`DEPRECATED.md` pointers in `pull_replication_studies/`, `claude_code_replications/`,
`metascience_observatory_website/data_ingestor/`). Read [README.md](README.md) first.

## Running it

- Install once: `pip install --break-system-packages --user -e .` (system Python;
  `fetch_pdf_from_doi` + `pdf4llm` are separate installed packages, not consolidated).
- Dashboard: `./dev.sh up` starts the FastAPI API (:8090) + Next.js dashboard (:3010) →
  http://localhost:3010. `./dev.sh down` stops the two services but **never** the
  detached pipeline stages. `./dev.sh status` lists both plus any running stages.
- Stages also run standalone, e.g. `python -m mo_pipeline.discover.classify_candidates`,
  `python -m mo_pipeline.extract.extract <papers_dir> --batch --level full --tag <tag>`.

## Invariants that matter

1. **`mo_pipeline/config.py` is the single source of truth for paths.** Never hardcode
   a path in a stage script — import it from config. Env overrides: `MO_DATA_DIR`,
   `MO_PROGRESS_DIR`, `MO_MEDIA_ROOT`, `MO_WEBSITE_DATA_DIR`. `python -m mo_pipeline.config`
   self-checks every path.
2. **Ingest writes to the WEBSITE data dir.** `data_ingestor.py`'s `DATA_DIR` resolves
   to `config.WEBSITE_DATA_DIR` (metascience_observatory_website/data). The production
   `replications_database_*.csv`, `version_history.txt`, and growth PNGs land there —
   the website serves them. Test with `MO_WEBSITE_DATA_DIR` pointed at a scratch copy.
3. **Shared extraction content lives in `prompts/prompt_shared_core.md` only** (schema,
   taxonomy, field reference, discipline list). Mode files (`prompt_full*.md`) hold only
   the read-the-paper workflow. Bump `prompts/version.txt` on any prompt edit.
4. **The corpus catalog is an index, never truth.** `corpus.sqlite` is rebuildable from
   `papers/` via `python -m mo_pipeline.corpus scan`. The filesystem (files present per
   folder) is the source of truth for status; `paper.json` holds only what a scan can't
   derive (source_batch, ingested record). Folder↔DOI encoding: `/`↔`--`, decoded by
   replacing **every** `--` (handles multi-slash OSF/journal DOIs — do not regress this).
5. **Per-paper folder layout is kept exactly.** PDF moved into `{doi}/` at conversion;
   each extraction run writes a `{tag}/` subfolder. Do not flatten or restructure.
6. **Stages run detached** (`server/app/runner.py`): state under `~/.local/state/mo_pipeline/`.
   They survive API restarts; `dev.sh down` never kills them. Mutex groups: `claude_cli`
   (classify+extract share Max rate limits), `heavy_ram` (dedup+convert), `self` (per-stage
   singleton, NOT cross-blocking).
7. **Search keywords are code defaults + a runtime overlay.** The curated lists live in
   `discover/search_for_replication_studies.py`; the dashboard edits `data/keywords.json`
   (via `discover/keywords.py`), which overrides them per-list at import. Edit terms via the
   dashboard **Keywords** page or that JSON — do not expect source edits to be the only path.

## The corpus (on `/media/dan/500Gb/metascience_observatory_pdfs/`)

Reorganized into a flat layout (the old ad-hoc `ingested/` tree is gone):
`papers/` (one folder per DOI — the corpus), `inbox/` (downloaded PDFs awaiting
conversion), `special/` (pre-pipeline corpora), `legacy/` (migration dup-losers +
malformed-name leftovers, reviewable), `corpus.sqlite`, and `migration_{plan,log}.csv`.
Status is derived from files present: downloaded → converted → screened → extracted →
ingested. Corpus CLI (`python -m mo_pipeline.corpus <cmd>`): `scan`, `stats`, `coverage`,
`include-list` (feeds `extract --include-list`), `mark-ingested` (post-stage-9 stamp),
`inventory`/`migrate`/`sweep` (one-time reorg, already done).

## Dashboard

`dashboard/src/app/`: `/` (pipeline overview + run/stop + system strip), `/stages/[id]`
(probe detail, run form, log tail), `/corpus` (catalog stats + browse), `/keywords`
(view/edit the four search-keyword lists). Frontend proxies `/api/*` to the FastAPI app
(`server/app/main.py`); the 9-stage registry + probes are in `server/app/registry.py`.

## Taxonomy (authoritative in `prompts/prompt_shared_core.md`)

Four replication types: **direct**, **close experiment**, **close extension**, **conceptual**.
Four result categories: **success**, **failure**, **inconclusive**, **reversal**.
Do not duplicate these definitions elsewhere.

## Gotchas

- `--level base` is broken (nonexistent prompt); normal extraction needs `--level full`.
  Dead `base`/`mid` PROMPT_FILES entries were removed in the unified config.
- Classify's checkpoint is guarded: `classify_progress.json`'s stored `input_row_count`
  must match the current `candidates_filtered.csv` row count. Regenerating prefilter output
  (e.g. after adding search terms) invalidates it — the dashboard pre-flags this as `stale`;
  fix by deleting the progress file to reclassify from scratch.
- Re-running search is incremental and safe: `search_progress.json` keys completed queries
  by `<api>:<query>`, so only new terms fire; `candidates_raw.csv` dedups.
- The external drive is ~98% full and slow (USB). Prefer catalog queries over walking
  `papers/`. Corpus scans take minutes; `catalog.connect()` uses `busy_timeout` so a long
  scan and the dashboard's `/corpus` reads don't deadlock.

## Where things are

Discover stages `mo_pipeline/discover/` (+ `keywords.py` overlay, `aux/` alt-discovery);
extract `mo_pipeline/extract/extract.py`; ingest `mo_pipeline/ingest/data_ingestor.py`;
corpus `mo_pipeline/corpus/` (models, catalog, migrate_drive, backfill); shared metadata
fetchers `mo_pipeline/shared/`; orchestrator `server/app/`; dashboard `dashboard/src/app/`.
`mo_pipeline/label_centrality/` is an emerging pilot (claude-vs-human agreement on whether
a database row's claim is central to its original paper; rubric in
`prompts/prompt_centrality.md`) — not yet part of the main 9-stage flow.
