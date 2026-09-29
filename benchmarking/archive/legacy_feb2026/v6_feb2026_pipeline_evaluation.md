# V6 Extraction Pipeline Evaluation

2/20/2026

The Metascience Observatory uses an automated pipeline to extract structured replication data from academic papers. We evaluated it against a hand-validated ground truth of 189 effect replication entries across 143 papers, primarily from psychology and related social sciences.

## Key results

The pipeline extracted 260 entries total versus 189 in the ground truth. The pipeline got 163 of 189 ground truth entries, a recall of 86%. Many of the ones it missed were because it was not as granular in separating out individual results as the ground truth is, in the case where one paper replicates many specific findings. The 71 extra entries are either additional legitimate replications the ground truth missed, ones that our evaluation system had trouble matching, or cases where the pipeline identified a different original study than the ground truth for the same replication.

The pipeline classifies each replication as success, failure, or inconclusive. Among the 163 replications it found that matched the ground truth:

| Category | Accuracy |
|----------|----------|
| Success  | 96.6% (57/59) |
| Failure  | 93.6% (73/78) |
| Inconclusive  | 34.6% (9/26) |

The main difference here is that the pipeline tends to commit to success or failure where human annotators are more likely to go with inconclusive.

![Confusion Matrix](/docs/v6_confusion_matrix.png)

## Statistical data extraction

The ground truth dataset did not have full coverage of the statistical data in the papers. However, where we had statistical data in the ground truth, the pipeline's extracted values were compared:

| Field | Coverage | Exact match |
|-------|----------|-------------|
| Replication sample size | 100.0% (159/159) | 88.1% (140/159) |
| Replication p-value | 100.0% (124/124) | 96.8% (120/124) |
| Replication effect size | 93.6% (102/109) | 87.2% (95/109) |
| Original effect size | 96.9% (93/96) | 89.6% (86/96) |
| Original p-value | 102.3% (45/44) | 97.7% (43/44) |
| Original sample size | 92.0% (103/112) | 80.4% (90/112) |

The results of this evaluation informed the creation of subsequent extraction pipelines.
