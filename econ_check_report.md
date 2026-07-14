# Economics replication verification — the 36 missing papers

Ran the 36 curated economics replications that are **not** in the live database through
the full mo_pipeline (download → convert → extract) to check whether they're actually
replication papers. Everything was kept isolated: files under
`/media/dan/500Gb/metascience_observatory_pdfs/econ_check*` and a dedicated `econ_check`
extraction tag — **nothing touched `papers/` or the website database.**

## Bottom line

- **11 of 36 (31%) downloaded** — matches the known "~30% of econ replications are
  retrievable without RePEc/SSRN/NBER". The other 25 are behind paywalls with Sci-Hub
  mirrors down/403.
- **Of the 11 with full text, only 4 extracted as replications.** The other 7 came back
  "no replications".
- **The extractor is not the problem — the downloads are.** Detection tracks actual
  content perfectly: the 4 detected papers contain replication language (188 / 4 / 2 / 1
  "replicat" mentions); the 7 non-detected have **~zero** (0 mentions in 76–175 KB
  full-text bodies) despite curated titles that literally say "A replication of…". A
  175 KB full text of a supposed replication with zero replication language means the
  **DOI resolved to the wrong paper** (usually the *original* study, not the replication)
  — or, for one, the conversion failed (2.7 KB stub).
- **The 2 clean confirmations validate the curated data**: `10.1016/j.jue.2006.01.001`
  and `10.1016/j.worlddev.2021.105731` both extracted with the **matching original study**
  and **result = failure**, exactly as the curated CSV claims.

**So: the curated list looks legitimate (titles + the 2 clean matches confirm it), but you
cannot re-derive these via download+extract — the DOI→PDF step fetches the wrong paper too
often. For ingestion, trust the hand-validated CSV rows directly rather than the pipeline.**

## Per-paper results

| replication DOI | src | body | "replicat" | extractor | orig match | result |
|---|---|---|---|---|---|---|
| 10.1016/j.jue.2006.01.001 | XML | 56 KB | 2 | **replication ×1** | ✅ match | failure |
| 10.1016/j.worlddev.2021.105731 | XML | 224 KB | 4 | **replication ×1** | ✅ match | failure |
| 10.1016/j.jbvi.2022.e00303 | PDF | 275 KB | 188 | **replication ×4** | ✗ different | success |
| 10.5089/9781484306444.001 | PDF | 31 KB | 1 | **replication ×2** | ✗ different | failure |
| 10.1016/j.econlet.2015.12.016 | XML | 129 KB | 0 | no replications | — | — |
| 10.1016/s0047-2727(99)00036-5 | XML | 175 KB | 0 | no replications | — | — |
| 10.1017/s1755773911000257 | PDF | 77 KB | 0 | no replications | — | — |
| 10.3368/le.91.3.556 | PDF | 2.7 KB | 0 | no replications | — | — (conversion failed) |
| 10.3386/w11268 | PDF | 157 KB | 1 | no replications | — | — |
| 10.4337/9780857939708.00013 | PDF | 95 KB | 0 | no replications | — | — |
| 10.63356/ace.2025.004 | PDF | 49 KB | 0 | no replications | — | — |

"orig match" = did the extractor's original-study DOI match the curated `original_doi`.
The 2 XML matches are the trustworthy confirmations; the 2 "different" ones found real
replications but of a different original study than the CSV row (worth a manual look).

## Two distinct problems surfaced

1. **DOI→PDF resolution fetches the wrong paper** for several rows (the 175/95/129 KB
   bodies with zero replication language are almost certainly the *original* study, not
   the replication). Either the curated `replication_doi` is off, or the fetch cascade
   resolved to the wrong item. This is the main data-quality issue.
2. **One conversion genuinely failed** — `10.3368/le.91.3.556` produced a 2.7 KB stub
   (likely a scanned/image PDF GROBID couldn't parse).

## CSV sanity check (follow-up) — the list is NOT clean; do not bulk-ingest

I audited `economics_replication_papers_with_dois.csv` after the extraction run. It is a
**working candidate list, not a validated gold standard.** My earlier "ingest directly"
advice was wrong.

**Validation status (242 rows total):**
- `validated=yes`: **41** — of which only **18** by Dan Elton; the other **23** were
  *scraped* from replication.uni-goettingen.de (via archive.org), not personally vetted.
- `validated=no`: 92 · blank: 109.
- **201 of 242 (83%) have no `original_doi` and no `result`** — incomplete rows.
- Structural: 5 duplicate `replication_doi`, 1 self-replication (orig==rep), 1 rep-DOI
  that is another row's original-DOI.

**DOI correctness — resolved all 36 `replication_doi`s against Crossref, compared titles:**
- ~**22 of 36 resolve to the claimed replication** (correct).
- ~**10 of 36 point to a *different or the original* paper** (wrong DOI), e.g.:
  - `10.1177/09721509241245544` (val=**yes**) → "Is Service Orientation Benefitting
    Manufacturing Exports…" (unrelated)
  - `10.4337/9780857939708.00013` (val=**yes**) → "IMF programs and private capital flows"
    (≠ "And the IMF Said, Let There Be Data")
  - `10.5040/9798216171119.0025` (val=**yes**) → "Case 19: Jose Canseco…" (a textbook case
    chapter, not the replication paper)
  - `10.1111/obes.12100` (val=yes) → "The Stock Market Crash Really Did Cause the Great
    Recession" (≠ "Farmer's Folly…")
  - plus `10.1017/s1755773911000257`, `10.1111/j.1468-0335…`, `10.1111/1368-423x…`,
    `10.1177/152700250000100103` (val=no/blank).
- ~4 ambiguous (Crossref returned a container/journal name, or the base title without the
  "A replication of…" descriptor).
- **~28% DOI error rate — and several bad DOIs are marked `validated=yes`.** This fully
  explains the extraction run: those "no replications" downloads fetched the *wrong* paper
  because the DOI itself is wrong.

## Corrected recommendation

- **Do NOT bulk-ingest the 36 (or the 230).** Roughly a quarter of the replication DOIs are
  wrong, and most rows are unvalidated / incomplete.
- **Ingest only a trusted subset**: rows that are `validated=yes` **AND** pass a
  DOI→title resolution check **AND** have `original_doi`+`result` — realistically only
  ~12–18 rows, each worth a 10-second manual confirm.
- **The 2 clean extraction matches** (`10.1016/j.jue.2006.01.001`,
  `10.1016/j.worlddev.2021.105731`) are the safest — DOI verified, extraction confirmed the
  matching original study and `result=failure`. Start there.
- Treat the rest of the CSV as leads to re-search, not facts to import.

Audit artifacts: `/tmp/claude-1000/econ_doi_check.json` (all 36 resolved),
`/tmp/claude-1000/econ_recheck.json` (the 15 flagged, full-title recheck).

## Artifacts

- Worklist: `mo_pipeline/data/econ_check_dois.csv` (36 DOIs + curated original/result)
- Curated source preserved: `mo_pipeline/data/economics_replication_papers_with_dois.csv`
- Downloads: `/media/…/econ_check/` (11 files) + `failed_dois.csv` (25 failures)
- Converted + extracted: `/media/…/econ_check_ready/` (11 folders, `econ_check/` result JSONs)
- Collated: `/media/…/econ_check_ready/collated_results_econ_check.csv` (15 rows: 8 replication + 7 no-rep)
- Extraction cost: **$6.64** for 11 papers, 5m 7s.
