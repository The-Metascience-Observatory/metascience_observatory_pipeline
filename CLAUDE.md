# mo_pipeline — orientation for Claude Code agents

Unified Metascience Observatory replication pipeline: search → dedup → prefilter →
classify → download → convert → extract → ingest, plus a corpus catalog and a
FastAPI+Next.js dashboard. Consolidated from three former directories (see the
`DEPRECATED.md` pointers in `pull_replication_studies/`, `claude_code_replications/`,
`metascience_observatory_website/data_ingestor/`). Read [README.md](README.md) first.

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
   `papers/` via `mo_pipeline.corpus scan`. The filesystem (files present per folder) is
   the source of truth for status; `paper.json` holds only what a scan can't derive
   (source_batch, ingested record).
5. **Per-paper folder layout is kept exactly.** PDF moved into `{doi}/` at conversion;
   each extraction run writes a `{tag}/` subfolder. Do not flatten or restructure.
6. **Stages run detached** (`server/app/runner.py`): `~/.local/state/mo_pipeline/`. They
   survive API restarts; `dev.sh down` never kills them. Mutex groups: `claude_cli`
   (classify+extract share Max rate limits), `heavy_ram` (dedup+convert), `self` (per-stage
   singleton, NOT cross-blocking).

## Taxonomy (authoritative in `prompts/prompt_shared_core.md`)

Four replication types: **direct**, **close experiment**, **close extension**, **conceptual**.
Four result categories: **success**, **failure**, **inconclusive**, **reversal**.
Do not duplicate these definitions elsewhere.

## Gotchas

- `--level base` is broken (nonexistent prompt); normal extraction needs `--level full`.
  Dead `base`/`mid` PROMPT_FILES entries were removed in the unified config.
- Classify's checkpoint is guarded: `classify_progress.json` must match
  `candidates_filtered.csv` row count (13829). Regenerating prefilter output invalidates
  it — the dashboard pre-flags this as `stale`.
- The external drive is ~98% full and slow (USB). Prefer catalog queries over walking
  `papers/`. Corpus scans take minutes.
- `pdf4llm` and `fetch_pdf_from_doi` are separate installed packages, not consolidated.

## Where things are

Discover stages `mo_pipeline/discover/`; extract `mo_pipeline/extract/extract.py`; ingest
`mo_pipeline/ingest/data_ingestor.py`; corpus `mo_pipeline/corpus/` (models, catalog,
migrate_drive); orchestrator `server/app/` (registry = the 9 stages + probes); dashboard
`dashboard/src/app/`.
