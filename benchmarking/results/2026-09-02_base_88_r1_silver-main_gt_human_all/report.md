# Extraction benchmark — main_gt_human / tags base_88_r1 / split all

## Provenance

- models: {'claude-sonnet-5': 26}  | ai_version in results: {'8.8-base': 26}
- prompt version now: 8.8 (prompts dirty in git: False); git 2771205043
- claude CLI: 2.1.259 (Claude Code) | harness 1.1 | 2026-09-02T23:54:50Z
- ground truth: silver:main_gt_human files ['silver/main_gt_human.csv', 'silver/main_gt_human_corrections.csv']; 6 corrections applied
- matcher: {'mode': 'llm', 'provider': 'claude_cli', 'model': 'haiku', 'prompt_version': '1.1', 'calls': 2, 'cache_hits': 10, 'failures': 0, 'offline': False}
- input tiers: {'grobid': 26}

## Coverage funnel

papers in GT: 27; not downloaded/converted: 0; not extracted under these tags: 1; bad json: 0; pipeline said no replications: 0; wrong document on disk: 0; scored: 26

## Entry-level matching (effect-level GT only)

TP 29  FN 3  FP 36  → precision 44.6%, recall 90.6%, F1 59.8%
FN breakdown: 3 judged different_original, 0 no candidate row, 0 paper reported no replications
granularity misses (same original, other effect): 0; wrong-original errors flagged by judge: 3; paper-level GT extra rows (unpenalized): 0

## Paper-level (contains_replications)

positives: TP 26 FN 0 | negatives: TN 0 FP 0 (not evaluated: 0) → precision 100.0%, negative-set FPR n/a

## Result classification (3-value; reversal→failure where the GT source lacks the class)

n=29  accuracy 65.5% [46.2%, 82.1%]  κ 0.374  macro-F1 47.5%  (collapse applied on 0 rows)

| class | support | predicted | recall | precision | F1 |
|---|---|---|---|---|---|
| success | 8 | 8 | 62.5% | 62.5% | 62.5% |
| failure | 19 | 16 | 73.7% | 87.5% | 80.0% |
| inconclusive | 2 | 5 | 0.0% | 0.0% | 0.0% |

confusion (rows = GT, cols = pipeline):

| | success | failure | inconclusive |
|---|---|---|---|
| **success** | 5 | 0 | 3 |
| **failure** | 3 | 14 | 2 |
| **inconclusive** | 0 | 2 | 0 |

Pipeline `reversal` across all scored rows (raw, before collapse): predicted 0; GT reversal support 0.

## Replication type

4-class n=0: accuracy n/a, adjacent-or-exact n/a, κ None; 2-class (FReD 'direct or close' vs conceptual) n=0: n/a

## Original study identification

original DOI exact (n=29): 93.1% [82.1%, 100.0%]; pipeline left DOI empty on 3.4%
bibliographic (n=29): title 86.2%, authors 86.2%, year 82.8%, journal 79.3%
citation_sentence: present 100.0%; author+year of GT original found in it 44.8%

## Statistics (denominator = both present; tiers: N exact/±5%/±20%, ES |Δ|≤.01/.05/.10 same type, p ≤1e-4 same type / ≤1e-4 / same side of .05)

| field | GT has | ext has | both | GT-only (miss) | ext-only | type mismatch | tier1 | tier2 | tier3 |
|---|---|---|---|---|---|---|---|---|---|
| original_n | 23 | 0 | 0 | 23 | 0 | 0 | n/a | n/a | n/a |
| original_es | 6 | 0 | 0 | 6 | 0 | 0 | n/a | n/a | n/a |
| original_p_value | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a |
| replication_n | 23 | 0 | 0 | 23 | 0 | 0 | n/a | n/a | n/a |
| replication_es | 7 | 0 | 0 | 7 | 0 | 0 | n/a | n/a | n/a |
| replication_p_value | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a |

## Matcher

{'methods': {'doi': 21, 'llm': 2, 'doi+judge': 6}, 'relations': {'same_effect': 28, 'subanalysis_of_gt': 1}, 'judge_calls': 2, 'cache_hits': 10, 'judge_failures': 0}

## Breakdowns (3-value result accuracy)

**by source**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| external:main_gt_human | 29 | 25 | 65.5% | 93.1% |

**by provenance**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| human:dan_elton | 10 | 10 | 70.0% | 80.0% |
| human:dan_elton;corrected:2026-09-02 | 3 | 3 | 66.7% | 100.0% |
| human:forrt_cd | 6 | 4 | 66.7% | 100.0% |
| human:forrt_lk | 10 | 9 | 60.0% | 100.0% |

**by discipline_group**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| biomed | 2 | 2 | 0.0% | 100.0% |
| other | 18 | 17 | 66.7% | 94.4% |
| psych | 4 | 2 | 100.0% | 100.0% |
| unknown | 5 | 5 | 60.0% | 80.0% |

**by gt_type**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| (blank) | 29 | 25 | 65.5% | 93.1% |

**by tier**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| grobid | 29 | 25 | 65.5% | 93.1% |

**by year_bucket**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| 2019-2022 | 12 | 10 | 58.3% | 91.7% |
| 2023+ | 2 | 1 | 100.0% | 100.0% |
| <=2018 | 15 | 14 | 66.7% | 93.3% |

**by split**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| all | 29 | 25 | 65.5% | 93.1% |

**by gt_ambiguity**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| (blank) | 29 | 25 | 65.5% | 93.1% |

**by match_method**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| doi | 21 | 21 | 61.9% | 100.0% |
| doi+judge | 6 | 3 | 83.3% | 100.0% |
| llm | 2 | 2 | 50.0% | 0.0% |
