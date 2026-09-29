# Extraction Pipeline Performance

*Last updated: February 2026*

The Metascience Observatory uses an automated pipeline (Claude Sonnet 4.5) to extract structured replication data from academic papers. We evaluated it against a hand-validated ground truth of **226 replication entries** across **145 papers**, primarily from psychology and related social sciences.

## Key metrics

| Metric | Score |
|--------|-------|
| Entry matching | 88.5% (200/226 ground truth entries found) |
| Result classification | 85.0% overall accuracy |
| Bibliographic accuracy | 79.5% (all four fields correct) |
| Original study URL recall | 70.8% (114/161 URLs recovered) |

## Precision and false positives

The pipeline extracted 283 entries from 141 papers, versus 226 ground truth entries across 145 papers. All 141 pipeline papers are genuine replication papers — **zero false positives at the paper level**. The pipeline never flagged a non-replication paper as containing replications.

Among unmatched entries, the main source of error is **wrong original study identification** (22 cases): the pipeline correctly identified that a replication occurred but attributed it to the wrong original study. The remaining unmatched pipeline entries are additional legitimate replications that the ground truth did not include.

## Result classification

The pipeline classifies each replication as success, failure, or inconclusive. Performance is strong on clear-cut cases:

| Category | Accuracy |
|----------|----------|
| Success | 97.0% (65/67) |
| Failure | 93.9% (92/98) |
| Inconclusive | 37.1% (13/35) |

The main weakness is **inconclusive under-prediction**: the pipeline tends to commit to success or failure where human annotators chose a more cautious label. Some of these disagreements reflect genuine ambiguity rather than extraction errors.

![Confusion Matrix](../v6_confusion_matrix.png)

## Statistical data extraction

Coverage measures how often the pipeline extracts a value when the ground truth has one. Exact match measures how often the extracted value is correct (within 0.1% relative error).

| Field | Coverage | Exact match |
|-------|----------|-------------|
| Replication sample size | 100% (171/171) | 89.5% (153/171) |
| Replication p-value | 100% (149/149) | 97.3% (145/149) |
| Replication effect size | 95.2% (139/146) | 96.4% (134/139) |
| Original effect size | 97.7% (129/132) | 95.3% (122/128) |
| Original p-value | 102.3% (45/44) | 100% (43/43) |
| Original sample size | 93.4% (128/137) | 89.8% (115/128) |

Sample size mismatches are typically due to different definitions of N (e.g., total participants vs. per-condition) rather than extraction errors. Effect size and p-value extraction is highly accurate.

## Manual spot checks

In addition to the formal benchmark, we spot-checked papers outside the ground truth set. In the most recent batch (February 2026, 6 papers), all were extracted correctly, including complex multi-replication papers and correct success/failure/inconclusive classifications verified against Discussion sections.

## Known limitations

1. **Inconclusive under-prediction** — the pipeline classifies ambiguous cases as success or failure more often than human annotators would.
2. **DOI resolution ceiling** — when the original study's DOI is absent from the source paper's reference list, downstream title-to-DOI resolution is imperfect.
3. **Ground truth subjectivity** — some classification disagreements may reflect legitimate differences in interpretation rather than errors.
