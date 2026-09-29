# Extraction benchmark — benchmarking/archive/legacy_feb2026/ground_truth_enhanced_V6DERIVED.csv / tags v,6,_,f,e,b,2,0,2,6 / split all

## Provenance

- models: {'claude-sonnet-4-5-20250929': 143}  | ai_version in results: {'unknown': 143}
- prompt version now: 8.6 (prompts dirty in git: True); git 67749053b2
- claude CLI: 2.1.258 (Claude Code) | harness 1.0 | 2026-09-02T15:24:09Z
- ground truth: benchmarking/archive/legacy_feb2026/ground_truth_enhanced_V6DERIVED.csv files ['benchmarking/archive/legacy_feb2026/ground_truth_enhanced_V6DERIVED.csv']  **CONTAMINATED (pipeline-authored rows allowed by --allow-dirty-gt)**
- matcher: {'mode': 'deterministic'}
- input tiers: {'unknown': 143}

## Coverage funnel

papers in GT: 143; not downloaded/converted: 0; not extracted under these tags: 0; bad json: 0; pipeline said no replications: 4; scored: 139

## Entry-level matching (effect-level GT only)

TP 161  FN 28  FP 99  → precision 61.9%, recall 85.2%, F1 71.7%
granularity misses (same original, other effect): 0; wrong-original errors flagged by judge: 0; paper-level GT extra rows (unpenalized): 0

## Paper-level (contains_replications)

positives: TP 139 FN 4 | negatives: TN 0 FP 0 (not evaluated: 0) → precision 100.0%, negative-set FPR n/a

## Result classification (3-value; reversal→failure where the GT source lacks the class)

n=161  accuracy 85.1% [79.7%, 90.8%]  κ 0.749  macro-F1 75.8%  (collapse applied on 1 rows)

| class | support | predicted | recall | precision | F1 |
|---|---|---|---|---|---|
| success | 58 | 66 | 94.8% | 83.3% | 88.7% |
| failure | 78 | 82 | 93.6% | 89.0% | 91.2% |
| inconclusive | 25 | 13 | 36.0% | 69.2% | 47.4% |

confusion (rows = GT, cols = pipeline):

| | success | failure | inconclusive |
|---|---|---|---|
| **success** | 55 | 2 | 1 |
| **failure** | 2 | 73 | 3 |
| **inconclusive** | 9 | 7 | 9 |

Pipeline `reversal` across all scored rows (raw, before collapse): predicted 1; GT reversal support 0.

## Replication type

4-class n=0: accuracy n/a, adjacent-or-exact n/a, κ None; 2-class (FReD 'direct or close' vs conceptual) n=0: n/a

## Original study identification

original DOI exact (n=141): 69.5% [61.1%, 77.6%]; pipeline left DOI empty on 27.7%
bibliographic (n=161): title 94.4%, authors 89.4%, year 96.3%, journal 95.0%
citation_sentence: present 0.0%; author+year of GT original found in it n/a

## Statistics (denominator = both present; tiers: N exact/±5%/±20%, ES |Δ|≤.01/.05/.10 same type, p ≤1e-4 same type / ≤1e-4 / same side of .05)

| field | GT has | ext has | both | GT-only (miss) | ext-only | type mismatch | tier1 | tier2 | tier3 |
|---|---|---|---|---|---|---|---|---|---|
| original_n | 109 | 102 | 101 | 8 | 1 | 0 | 92.1% | 93.1% | 95.0% |
| original_es | 94 | 91 | 90 | 4 | 1 | 1 | 92.1% | 93.3% | 93.3% |
| original_p_value | 42 | 44 | 42 | 0 | 2 | 0 | 100.0% | 100.0% | 100.0% |
| replication_n | 156 | 157 | 156 | 0 | 1 | 0 | 91.7% | 91.7% | 93.6% |
| replication_es | 107 | 107 | 103 | 4 | 4 | 3 | 95.0% | 96.0% | 96.0% |
| replication_p_value | 122 | 123 | 122 | 0 | 1 | 0 | 94.3% | 94.3% | 96.7% |

## Matcher

{'methods': {'doi': 98, 'fuzzy': 63}, 'relations': {'same_effect': 161}, 'judge_calls': 0, 'cache_hits': 0, 'judge_failures': 0}

## Breakdowns (3-value result accuracy)

**by source**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| ground_truth_enhanced_V6DERIVED | 161 | 121 | 85.1% | 69.5% |

**by provenance**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| external:fred_api | 106 | 100 | 80.2% | 69.8% |
| human:dan_elton | 6 | 6 | 66.7% | 0.0% |
| human:forrt_cd | 5 | 3 | 80.0% | 80.0% |
| human:forrt_lk | 9 | 8 | 100.0% | 77.8% |
| pipeline:v6 | 35 | 19 | 100.0% | 86.7% |

**by discipline_group**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| biomed | 22 | 20 | 54.5% | 75.0% |
| other | 87 | 67 | 88.5% | 65.4% |
| psych | 49 | 33 | 93.9% | 81.1% |
| unknown | 3 | 3 | 66.7% | 0.0% |

**by gt_type**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| (blank) | 161 | 121 | 85.1% | 69.5% |

**by tier**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| unknown | 161 | 121 | 85.1% | 69.5% |

**by year_bucket**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| 2019-2022 | 63 | 48 | 84.1% | 75.8% |
| 2023+ | 53 | 48 | 81.1% | 81.1% |
| <=2018 | 45 | 25 | 91.1% | 30.8% |

**by split**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| all | 161 | 121 | 85.1% | 69.5% |

**by gt_ambiguity**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| (blank) | 161 | 121 | 85.1% | 69.5% |

**by match_method**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| doi | 98 | 82 | 85.7% | 100.0% |
| fuzzy | 63 | 42 | 84.1% | 0.0% |
