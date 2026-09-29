# V8 Extraction Pipeline Evaluation

2/20/2026

The Metascience Observatory uses an automated pipeline to extract structured replication data from academic papers. We evaluated it against a hand-validated ground truth of 189 effect replication entries across 143 papers, primarily from psychology and related social sciences.

## Key results

The pipeline extracted 268 entries total versus 189 in the ground truth. The pipeline got 164 of 189 ground truth entries, a recall of 87%. Many of the ones it missed were because it was not as granular in separating out individual results as the ground truth is, in the case where one paper replicates many specific findings. The 79 extra entries are either additional legitimate replications the ground truth missed, ones that our evaluation system had trouble matching, or cases where the pipeline identified a different original study than the ground truth for the same replication.

The pipeline classifies each replication as success, failure, or inconclusive. Among the 164 replications it found that matched the ground truth:

| Category | Accuracy |
|----------|----------|
| Success  | 88.5% (54/61) |
| Failure  | 90.9% (70/77) |
| Inconclusive  | 23.1% (6/26) |

The main difference here is that the pipeline tends to commit to success or failure where human annotators are more likely to go with inconclusive.

![Confusion Matrix](/docs/v8_confusion_matrix.png)

## Statistical data extraction

The ground truth dataset did not have full coverage of the statistical data in the papers. However, where we had statistical data in the ground truth, the pipeline's extracted values were compared:

| Field | Coverage | Exact match |
|-------|----------|-------------|
| Replication sample size | 103.2% (160/155) | 74.8% (116/154) |
| Replication p-value | 103.3% (125/121) | 77.7% (94/109) |
| Replication effect size | 88.9% (96/108) | 66.7% (72/88) |
| Original effect size | 86.5% (83/96) | 70.8% (68/79) |
| Original p-value | 104.7% (45/43) | 83.7% (36/37) |
| Original sample size | 89.1% (98/110) | 65.5% (72/92) |

The results of this evaluation informed the creation of subsequent extraction pipelines.
