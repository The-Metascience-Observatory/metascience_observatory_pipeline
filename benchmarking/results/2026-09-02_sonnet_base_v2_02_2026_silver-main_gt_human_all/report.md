# Extraction benchmark — main_gt_human / tags sonnet_base_v2_02_2026 / split all

## Provenance

- models: {'claude-sonnet-4-5-20250929': 27}  | ai_version in results: {'unknown': 27}
- prompt version now: 8.8 (prompts dirty in git: False); git 2771205043
- claude CLI: 2.1.259 (Claude Code) | harness 1.1 | 2026-09-02T23:39:31Z
- ground truth: silver:main_gt_human files ['silver/main_gt_human.csv']
- matcher: {'mode': 'llm', 'provider': 'claude_cli', 'model': 'haiku', 'prompt_version': '1.1', 'calls': 14, 'cache_hits': 0, 'failures': 0, 'offline': False}
- input tiers: {'unknown': 27}

## Coverage funnel

papers in GT: 27; not downloaded/converted: 0; not extracted under these tags: 0; bad json: 0; pipeline said no replications: 1; wrong document on disk: 0; scored: 26

## Entry-level matching (effect-level GT only)

TP 24  FN 9  FP 12  → precision 66.7%, recall 72.7%, F1 69.6%
FN breakdown: 3 judged different_original, 3 no candidate row, 3 paper reported no replications
granularity misses (same original, other effect): 1; wrong-original errors flagged by judge: 3; paper-level GT extra rows (unpenalized): 0

## Paper-level (contains_replications)

positives: TP 26 FN 1 | negatives: TN 0 FP 0 (not evaluated: 0) → precision 100.0%, negative-set FPR n/a

## Result classification (3-value; reversal→failure where the GT source lacks the class)

n=24  accuracy 70.8% [52.2%, 88.0%]  κ 0.462  macro-F1 60.4%  (collapse applied on 1 rows)

| class | support | predicted | recall | precision | F1 |
|---|---|---|---|---|---|
| success | 6 | 5 | 66.7% | 80.0% | 72.7% |
| failure | 16 | 14 | 75.0% | 85.7% | 80.0% |
| inconclusive | 2 | 5 | 50.0% | 20.0% | 28.6% |

confusion (rows = GT, cols = pipeline):

| | success | failure | inconclusive |
|---|---|---|---|
| **success** | 4 | 1 | 1 |
| **failure** | 1 | 12 | 3 |
| **inconclusive** | 0 | 1 | 1 |

Pipeline `reversal` across all scored rows (raw, before collapse): predicted 1; GT reversal support 0.

## Replication type

4-class n=0: accuracy n/a, adjacent-or-exact n/a, κ None; 2-class (FReD 'direct or close' vs conceptual) n=0: n/a

## Original study identification

original DOI exact (n=24): 66.7% [47.8%, 84.6%]; pipeline left DOI empty on 12.5%
bibliographic (n=24): title 87.5%, authors 79.2%, year 87.5%, journal 79.2%
citation_sentence: present 0.0%; author+year of GT original found in it n/a

## Statistics (denominator = both present; tiers: N exact/±5%/±20%, ES |Δ|≤.01/.05/.10 same type, p ≤1e-4 same type / ≤1e-4 / same side of .05)

| field | GT has | ext has | both | GT-only (miss) | ext-only | type mismatch | tier1 | tier2 | tier3 |
|---|---|---|---|---|---|---|---|---|---|
| original_n | 19 | 0 | 0 | 19 | 0 | 0 | n/a | n/a | n/a |
| original_es | 4 | 0 | 0 | 4 | 0 | 0 | n/a | n/a | n/a |
| original_p_value | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a |
| replication_n | 19 | 0 | 0 | 19 | 0 | 0 | n/a | n/a | n/a |
| replication_es | 5 | 0 | 0 | 5 | 0 | 0 | n/a | n/a | n/a |
| replication_p_value | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a |

## Matcher

{'methods': {'doi': 15, 'llm': 8, 'doi+judge': 1}, 'relations': {'same_effect': 23, 'subanalysis_of_gt': 1}, 'judge_calls': 14, 'cache_hits': 0, 'judge_failures': 0}

## Breakdowns (3-value result accuracy)

**by source**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| external:main_gt_human | 24 | 23 | 70.8% | 66.7% |

**by provenance**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| human:dan_elton | 10 | 10 | 60.0% | 50.0% |
| human:forrt_cd | 4 | 4 | 75.0% | 50.0% |
| human:forrt_lk | 10 | 9 | 80.0% | 90.0% |

**by discipline_group**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| biomed | 2 | 2 | 50.0% | 50.0% |
| other | 16 | 15 | 68.8% | 75.0% |
| psych | 2 | 2 | 100.0% | 100.0% |
| unknown | 4 | 4 | 75.0% | 25.0% |

**by gt_type**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| (blank) | 24 | 23 | 70.8% | 66.7% |

**by tier**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| unknown | 24 | 23 | 70.8% | 66.7% |

**by year_bucket**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| 2019-2022 | 10 | 10 | 80.0% | 80.0% |
| 2023+ | 2 | 1 | 100.0% | 100.0% |
| <=2018 | 12 | 12 | 58.3% | 50.0% |

**by split**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| all | 24 | 23 | 70.8% | 66.7% |

**by gt_ambiguity**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| (blank) | 24 | 23 | 70.8% | 66.7% |

**by match_method**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| doi | 15 | 15 | 80.0% | 100.0% |
| doi+judge | 1 | 1 | 100.0% | 100.0% |
| llm | 8 | 8 | 50.0% | 0.0% |
