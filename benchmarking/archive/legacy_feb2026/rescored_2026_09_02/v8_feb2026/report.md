# Extraction benchmark — benchmarking/archive/legacy_feb2026/ground_truth_enhanced_V6DERIVED.csv / tags v,8,_,f,e,b,2,0,2,6 / split all

## Provenance

- models: {'claude-sonnet-4-6': 143}  | ai_version in results: {'8': 141, 'unknown': 2}
- prompt version now: 8.6 (prompts dirty in git: True); git 67749053b2
- claude CLI: 2.1.258 (Claude Code) | harness 1.0 | 2026-09-02T15:24:08Z
- ground truth: benchmarking/archive/legacy_feb2026/ground_truth_enhanced_V6DERIVED.csv files ['benchmarking/archive/legacy_feb2026/ground_truth_enhanced_V6DERIVED.csv']  **CONTAMINATED (pipeline-authored rows allowed by --allow-dirty-gt)**
- matcher: {'mode': 'deterministic'}
- input tiers: {'unknown': 143}

## Coverage funnel

papers in GT: 143; not downloaded/converted: 0; not extracted under these tags: 0; bad json: 0; pipeline said no replications: 2; scored: 141

## Entry-level matching (effect-level GT only)

TP 161  FN 28  FP 104  → precision 60.8%, recall 85.2%, F1 70.9%
granularity misses (same original, other effect): 0; wrong-original errors flagged by judge: 0; paper-level GT extra rows (unpenalized): 0

## Paper-level (contains_replications)

positives: TP 141 FN 2 | negatives: TN 0 FP 0 (not evaluated: 0) → precision 100.0%, negative-set FPR n/a

## Result classification (3-value; reversal→failure where the GT source lacks the class)

n=161  accuracy 80.7% [73.9%, 85.7%]  κ 0.678  macro-F1 68.5%  (collapse applied on 1 rows)

| class | support | predicted | recall | precision | F1 |
|---|---|---|---|---|---|
| success | 60 | 64 | 88.3% | 82.8% | 85.5% |
| failure | 75 | 83 | 94.7% | 85.5% | 89.9% |
| inconclusive | 26 | 14 | 23.1% | 42.9% | 30.0% |

confusion (rows = GT, cols = pipeline):

| | success | failure | inconclusive |
|---|---|---|---|
| **success** | 53 | 3 | 4 |
| **failure** | 0 | 71 | 4 |
| **inconclusive** | 11 | 9 | 6 |

Pipeline `reversal` across all scored rows (raw, before collapse): predicted 1; GT reversal support 0.

## Replication type

4-class n=0: accuracy n/a, adjacent-or-exact n/a, κ None; 2-class (FReD 'direct or close' vs conceptual) n=0: n/a

## Original study identification

original DOI exact (n=144): 91.0% [86.1%, 95.7%]; pipeline left DOI empty on 2.8%
bibliographic (n=161): title 93.2%, authors 93.2%, year 95.7%, journal 96.9%
citation_sentence: present 0.0%; author+year of GT original found in it n/a

## Statistics (denominator = both present; tiers: N exact/±5%/±20%, ES |Δ|≤.01/.05/.10 same type, p ≤1e-4 same type / ≤1e-4 / same side of .05)

| field | GT has | ext has | both | GT-only (miss) | ext-only | type mismatch | tier1 | tier2 | tier3 |
|---|---|---|---|---|---|---|---|---|---|
| original_n | 109 | 98 | 91 | 18 | 7 | 0 | 82.4% | 86.8% | 89.0% |
| original_es | 93 | 78 | 74 | 19 | 4 | 8 | 83.3% | 86.4% | 87.9% |
| original_p_value | 42 | 42 | 35 | 7 | 7 | 0 | 91.4% | 94.3% | 97.1% |
| replication_n | 152 | 157 | 151 | 1 | 6 | 0 | 77.5% | 81.5% | 84.1% |
| replication_es | 105 | 95 | 83 | 22 | 12 | 8 | 81.3% | 86.7% | 94.7% |
| replication_p_value | 119 | 123 | 107 | 12 | 16 | 0 | 81.3% | 82.2% | 95.3% |

## Matcher

{'methods': {'doi': 131, 'fuzzy': 30}, 'relations': {'same_effect': 161}, 'judge_calls': 0, 'cache_hits': 0, 'judge_failures': 0}

## Breakdowns (3-value result accuracy)

**by source**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| ground_truth_enhanced_V6DERIVED | 161 | 125 | 80.7% | 91.0% |

**by provenance**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| external:fred_api | 110 | 104 | 79.1% | 94.5% |
| human:dan_elton | 6 | 6 | 83.3% | 50.0% |
| human:forrt_cd | 6 | 4 | 50.0% | 83.3% |
| human:forrt_lk | 10 | 9 | 90.0% | 90.0% |
| pipeline:v6 | 29 | 17 | 89.7% | 83.3% |

**by discipline_group**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| biomed | 23 | 21 | 56.5% | 100.0% |
| other | 85 | 68 | 84.7% | 86.1% |
| psych | 50 | 36 | 86.0% | 97.4% |
| unknown | 3 | 3 | 66.7% | 66.7% |

**by gt_type**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| (blank) | 161 | 125 | 80.7% | 91.0% |

**by tier**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| unknown | 161 | 125 | 80.7% | 91.0% |

**by year_bucket**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| 2019-2022 | 64 | 50 | 79.7% | 90.5% |
| 2023+ | 55 | 50 | 81.8% | 94.5% |
| <=2018 | 42 | 25 | 81.0% | 84.6% |

**by split**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| all | 161 | 125 | 80.7% | 91.0% |

**by gt_ambiguity**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| (blank) | 161 | 125 | 80.7% | 91.0% |

**by match_method**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| doi | 131 | 116 | 79.4% | 100.0% |
| fuzzy | 30 | 17 | 86.7% | 0.0% |
