# Unified Metascience Observatory replication pipeline

One directory for the replication pipeline: search → classify → download → convert → extract (+ collate), plus a corpus catalog and a web dashboard to run and monitor every stage.

The final data ingestion into the website's database, including metadata backfills and normalization, etc is **not** part
of this pipeline — it is run manually using [metascience_observatory_website/data_ingestor/data_ingestor.py](https://github.com/The-Metascience-Observatory/metascience-observatory-website/blob/main/data_ingestor/data_ingestor.py). This pipeline produces a .csv file that can be imported using data_ingestor.py.

## Acknowledgment

If you use this code in your research, please acknowledge the Metascience
Observatory and include a link to this repository.

## Layout

| Path | What |
|---|---|
| `mo_pipeline/config.py` | **The** unified config — every path and tunable. `python -m mo_pipeline.config` self-checks. |
| `mo_pipeline/discover/` | Stages 1–5: search, deduplicate, prefilter, classify, download (direct/close/conceptual, skipping already-published papers). |
| `mo_pipeline/extract/` | Stage 7: `extract.py` — agentic LLM extraction + collation; `extract_core.py` — single-shot core-fields extractor (no statistics). |
| `mo_pipeline/corpus/` | Corpus catalog (`corpus.sqlite`), renditions, inbox→papers adoption. |
| `mo_pipeline/shared/` | DOI/title metadata fetchers used by extract. |
| `prompts/` | Extraction prompts + `version.txt` (bump on any prompt edit). |
| `server/` | FastAPI orchestrator (port 8090). |
| `dashboard/` | Next.js dashboard (port 3010). |
| `data/`, `progress/` | Candidate CSVs + search/classify checkpoints (gitignored). |

Big data (PDFs, the paper corpus) lives on the internal data drive at
`/media/dan/data/metascience_observatory_pdfs/` (moved 2026-09-05 off the failing
USB drive `/media/dan/500Gb`) — see the corpus layout below.

## Setup

```bash
pip install --break-system-packages --user -e .   # editable install of mo_pipeline
# fetchpdf (../fetchpdf_public), fetchpdf_grey (../fetchpdf-grey) and pdf4llm are separate editable installs
```

## Run a stage from the CLI

```bash
python -m mo_pipeline.discover.classify_candidates --workers 20
python -m mo_pipeline.discover.download_all_confirmed --limit 100 --legalonly   # inbox/{doi}/: XML|HTML + PDF + gated markdown
python -m mo_pipeline.discover.download_all_confirmed --backfill-structured --backfill-pdf --doi-csv data/confirmed_replications.csv
pdf4llm batch /media/.../inbox -o /media/.../papers --mode full-grobid --workers 4 --movepdf --resume
python -m mo_pipeline.extract.extract /media/.../papers --batch --level full --tag sonnet_v8_5
python -m mo_pipeline.extract.extract_core /media/.../papers --tag core_v1   # core fields only, one model call per paper
python -m mo_pipeline.extract.extract /media/.../papers --batch --level base --usecodex --model gpt-5.6-terra --workers 10 --tag terra_base --include-list data/selected_papers.txt
# then ingest the collated CSV manually, from the website repo:
#   cd ../metascience_observatory_website/data_ingestor
#   python data_ingestor.py collated_results_sonnet_v8_5.csv --no-gui
```

Env overrides for safe testing: `MO_DATA_DIR`, `MO_PROGRESS_DIR`, `MO_MEDIA_ROOT`,
`MO_WEBSITE_DATA_DIR` (redirect the production DB to a scratch copy).

The agentic extractor supports `--usecodex` with an explicit Codex model ID.
It runs `codex exec` in each paper folder, records the CLI and model in provenance,
and keeps the existing validation, metadata enrichment, and collation steps.
`--level base` omits statistics from both the prompt and saved rows. Codex runs
do not invoke the Claude-specific second-pass reviewer; validation warnings remain
in the run log. Existing results under the same tag are skipped on resume.

## The corpus catalog

The paper corpus lives at `MEDIA_ROOT/papers/`, one folder per DOI (slashes as
`--`). Each folder keeps the per-paper layout: PDF + `abstract.md`/`body.md`/
`references.json` + `replication_check.json` (screening) + one `{tag}/` subfolder
per extraction run + a `paper.json` passport. Findings and processing status are
queryable via `corpus.sqlite`:

```bash
python -m mo_pipeline.corpus scan          # rebuild catalog from papers/
python -m mo_pipeline.corpus stats         # totals by status + with/without replications
python -m mo_pipeline.corpus include-list --status converted --not-extracted-tag sonnet_v9
python -m mo_pipeline.corpus mark-ingested collated.csv --db-version replications_database_X.csv
```

Drive layout: `papers/` (corpus) · `inbox/` (one folder per downloaded record awaiting conversion) ·
`special/` (pre-pipeline corpora) · `legacy/` (migration dup-losers, reviewable) ·
`corpus.sqlite`. State (downloaded/converted/screened/extracted/ingested) is derived
from files present; location no longer encodes findings.

## The dashboard

```bash
./dev.sh up        # FastAPI :8090 + Next.js :3010  →  http://localhost:3010
./dev.sh status    # services + any running pipeline stages
./dev.sh down      # stops services only — running stages keep going (they're detached)
```

The dashboard shows every stage's live progress (from checkpoints + the catalog),
runs/stops stages with mutex guards (one claude-CLI stage at a time; convert needs
≥20 GB free RAM), and browses the corpus. Stages run as detached subprocesses under
`~/.local/state/mo_pipeline/`, so they survive API restarts.

See [CLAUDE.md](CLAUDE.md) for invariants and the taxonomy source of truth.

## License

The original code, prompts, and documentation are licensed under the
[MIT License](LICENSE). The acknowledgment request above is a courtesy request,
not an additional license condition.

Research datasets and third-party material, including paper PDFs and extracted
paper text, are outside the scope of this software license. Their respective
rights and license terms apply.
