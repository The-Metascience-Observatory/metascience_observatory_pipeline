# Extraction benchmark — main_gt_human / tags v8_feb2026 / split all

## Provenance

- models: {'claude-sonnet-4-6': 27}  | ai_version in results: {'8': 27}
- prompt version now: 8.8 (prompts dirty in git: False); git 2771205043
- claude CLI: 2.1.259 (Claude Code) | harness 1.1 | 2026-09-02T23:37:33Z
- ground truth: silver:main_gt_human files ['silver/main_gt_human.csv']
- matcher: {'mode': 'llm', 'provider': 'claude_cli', 'model': 'haiku', 'prompt_version': '1.1', 'calls': 17, 'cache_hits': 0, 'failures': 0, 'offline': False}
- input tiers: {'unknown': 27}

## Coverage funnel

papers in GT: 27; not downloaded/converted: 0; not extracted under these tags: 0; bad json: 0; pipeline said no replications: 0; wrong document on disk: 0; scored: 27

## Entry-level matching (effect-level GT only)

TP 26  FN 7  FP 22  → precision 54.2%, recall 78.8%, F1 64.2%
FN breakdown: 6 judged different_original, 1 no candidate row, 0 paper reported no replications
granularity misses (same original, other effect): 1; wrong-original errors flagged by judge: 6; paper-level GT extra rows (unpenalized): 0

## Paper-level (contains_replications)

positives: TP 27 FN 0 | negatives: TN 0 FP 0 (not evaluated: 0) → precision 100.0%, negative-set FPR n/a

## Result classification (3-value; reversal→failure where the GT source lacks the class)

n=26  accuracy 73.1% [56.0%, 88.0%]  κ 0.462  macro-F1 51.3%  (collapse applied on 0 rows)

| class | support | predicted | recall | precision | F1 |
|---|---|---|---|---|---|
| success | 8 | 8 | 75.0% | 75.0% | 75.0% |
| failure | 16 | 17 | 81.2% | 76.5% | 78.8% |
| inconclusive | 2 | 1 | 0.0% | 0.0% | 0.0% |

confusion (rows = GT, cols = pipeline):

| | success | failure | inconclusive |
|---|---|---|---|
| **success** | 6 | 2 | 0 |
| **failure** | 2 | 13 | 1 |
| **inconclusive** | 0 | 2 | 0 |

Pipeline `reversal` across all scored rows (raw, before collapse): predicted 0; GT reversal support 0.

## Replication type

4-class n=0: accuracy n/a, adjacent-or-exact n/a, κ None; 2-class (FReD 'direct or close' vs conceptual) n=0: n/a

## Original study identification

original DOI exact (n=26): 80.8% [65.2%, 93.9%]; pipeline left DOI empty on 7.7%
bibliographic (n=26): title 88.5%, authors 92.3%, year 88.5%, journal 84.6%
citation_sentence: present 0.0%; author+year of GT original found in it n/a

## Statistics (denominator = both present; tiers: N exact/±5%/±20%, ES |Δ|≤.01/.05/.10 same type, p ≤1e-4 same type / ≤1e-4 / same side of .05)

| field | GT has | ext has | both | GT-only (miss) | ext-only | type mismatch | tier1 | tier2 | tier3 |
|---|---|---|---|---|---|---|---|---|---|
| original_n | 21 | 16 | 12 | 9 | 4 | 0 | 66.7% | 75.0% | 75.0% |
| original_es | 4 | 10 | 1 | 3 | 9 | 0 | 0.0% | 0.0% | 0.0% |
| original_p_value | 0 | 5 | 0 | 0 | 5 | 0 | n/a | n/a | n/a |
| replication_n | 21 | 25 | 21 | 0 | 4 | 0 | 47.6% | 61.9% | 71.4% |
| replication_es | 5 | 14 | 3 | 2 | 11 | 1 | 0.0% | 0.0% | 0.0% |
| replication_p_value | 0 | 18 | 0 | 0 | 18 | 0 | n/a | n/a | n/a |

## Matcher

{'methods': {'doi': 18, 'llm': 5, 'doi+judge': 3}, 'relations': {'same_effect': 24, 'subanalysis_of_gt': 2}, 'judge_calls': 17, 'cache_hits': 0, 'judge_failures': 0}

## Breakdowns (3-value result accuracy)

**by source**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| external:main_gt_human | 26 | 23 | 73.1% | 80.8% |

**by provenance**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| human:dan_elton | 10 | 10 | 70.0% | 70.0% |
| human:forrt_cd | 6 | 4 | 50.0% | 83.3% |
| human:forrt_lk | 10 | 9 | 90.0% | 90.0% |

**by discipline_group**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| biomed | 2 | 2 | 0.0% | 100.0% |
| other | 16 | 15 | 87.5% | 81.2% |
| psych | 4 | 2 | 75.0% | 100.0% |
| unknown | 4 | 4 | 50.0% | 50.0% |

**by gt_type**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| (blank) | 26 | 23 | 73.1% | 80.8% |

**by tier**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| unknown | 26 | 23 | 73.1% | 80.8% |

**by year_bucket**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| 2019-2022 | 12 | 10 | 75.0% | 83.3% |
| 2023+ | 2 | 1 | 100.0% | 100.0% |
| <=2018 | 12 | 12 | 66.7% | 75.0% |

**by split**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| all | 26 | 23 | 73.1% | 80.8% |

**by gt_ambiguity**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| (blank) | 26 | 23 | 73.1% | 80.8% |

**by match_method**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| doi | 18 | 18 | 72.2% | 100.0% |
| doi+judge | 3 | 2 | 100.0% | 100.0% |
| llm | 5 | 5 | 60.0% | 0.0% |
