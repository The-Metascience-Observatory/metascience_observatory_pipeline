# Extraction benchmark — main_gt_human / tags v8_feb2026 / split all

## Provenance

- models: {'claude-sonnet-4-6': 27}  | ai_version in results: {'8': 27}
- prompt version now: 8.8 (prompts dirty in git: False); git c676d6aa9b
- claude CLI: 2.1.260 (Claude Code) | harness 1.2 | 2026-09-04T01:13:05Z
- ground truth: silver:main_gt_human files ['silver/main_gt_human.csv', 'silver/main_gt_human_corrections.csv']; 6 corrections applied
- matcher: {'mode': 'llm', 'provider': 'claude_cli', 'model': 'haiku', 'prompt_version': '1.1', 'calls': 0, 'cache_hits': 16, 'failures': 0, 'offline': False}
- input tiers: {'unknown': 27}

## Coverage funnel

papers in GT: 27; not downloaded/converted: 0; not extracted under these tags: 0; bad json: 0; pipeline said no replications: 0; wrong document on disk: 0; scored: 27

## Entry-level matching (effect-level GT only)

TP 29  FN 4  FP 19  → precision 60.4%, recall 87.9%, F1 71.6%
FN breakdown: 3 judged different_original, 1 no candidate row, 0 paper reported no replications
granularity misses (same original, other effect): 1; wrong-original errors flagged by judge: 3; paper-level GT extra rows (unpenalized): 0

Precision here is a **lower bound**: this ground truth codes the replication each paper is about, not every sub-analysis of it, so an extra extracted row may be a real effect nobody coded. Recall and the FN breakdown are unaffected.
Of the 19 penalized extras, 7 cite an original a matched row already names (per-effect splits of a recorded replication, which the prompt asks for) and 12 name an original the GT does not record at all — only the second group can contain a wrong-original error. See `same_original_as_a_matched_row` in extra_rows.csv.

## Paper-level (contains_replications)

positives: TP 27 FN 0 | negatives: TN 0 FP 0 (not evaluated: 0) → precision 100.0%, negative-set FPR n/a

## Result classification (3-value; reversal→failure where the GT source lacks the class)

n=29  accuracy 75.9% [61.5%, 89.7%]  κ 0.486  macro-F1 52.4%  (collapse applied on 1 rows)

| class | support | predicted | recall | precision | F1 |
|---|---|---|---|---|---|
| success | 8 | 8 | 75.0% | 75.0% | 75.0% |
| failure | 19 | 20 | 84.2% | 80.0% | 82.1% |
| inconclusive | 2 | 1 | 0.0% | 0.0% | 0.0% |

confusion (rows = GT, cols = pipeline):

| | success | failure | inconclusive |
|---|---|---|---|
| **success** | 6 | 2 | 0 |
| **failure** | 2 | 16 | 1 |
| **inconclusive** | 0 | 2 | 0 |

### Same rows, aggregated per original study

Where the ground truth carries one verdict for a paper (FLoRa) or codes one effect of an original the pipeline split into several rows, this compares that verdict with the aggregate of every pipeline row about the same original, rather than the single row the assignment picked. The strict number above stays the headline.
n=17  accuracy 82.4%  κ 0.495  macro-F1 56.3%

Pipeline `reversal` across all scored rows (raw, before collapse): predicted 1; GT reversal support 0.

## Replication type

4-class n=0: accuracy n/a, adjacent-or-exact n/a, κ None; 2-class (FReD 'direct or close' vs conceptual) n=0: n/a

## Original study identification

original DOI exact (n=29): 82.8% [65.5%, 96.4%]; pipeline left DOI empty on 6.9%
bibliographic (n=29): title 89.7%, authors 82.8%, year 79.3%, journal 75.9%
citation_sentence: present 0.0%; author+year of GT original found in it n/a

## Statistics (denominator = both present; tiers: N exact/±5%/±20%, ES |Δ|≤.01/.05/.10 same type, p ≤1e-4 same type / ≤1e-4 / same side of .05)

| field | GT has | ext has | both | GT-only (miss) | ext-only | type mismatch | tier1 | tier2 | tier3 |
|---|---|---|---|---|---|---|---|---|---|
| original_n | 22 | 17 | 13 | 9 | 4 | 0 | 53.8% | 61.5% | 76.9% |
| original_es | 5 | 11 | 2 | 3 | 9 | 0 | 0.0% | 0.0% | 0.0% |
| original_p_value | 0 | 6 | 0 | 0 | 6 | 0 | n/a | n/a | n/a |
| replication_n | 22 | 27 | 22 | 0 | 5 | 0 | 40.9% | 54.5% | 63.6% |
| replication_es | 6 | 15 | 4 | 2 | 11 | 1 | 0.0% | 33.3% | 33.3% |
| replication_p_value | 0 | 19 | 0 | 0 | 19 | 0 | n/a | n/a | n/a |

## Matcher

{'methods': {'doi': 20, 'llm': 5, 'doi+judge': 4}, 'relations': {'same_effect': 27, 'subanalysis_of_gt': 2}, 'judge_calls': 0, 'cache_hits': 16, 'judge_failures': 0}

## Breakdowns (3-value result accuracy)

**by source**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| external:main_gt_human | 29 | 25 | 75.9% | 82.8% |

**by provenance**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| human:dan_elton | 10 | 10 | 70.0% | 70.0% |
| human:dan_elton;corrected:2026-09-02 | 3 | 3 | 100.0% | 100.0% |
| human:forrt_cd | 6 | 4 | 50.0% | 83.3% |
| human:forrt_lk | 10 | 9 | 90.0% | 90.0% |

**by discipline_group**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| biomed | 2 | 2 | 0.0% | 100.0% |
| other | 17 | 16 | 88.2% | 82.4% |
| psych | 4 | 2 | 75.0% | 100.0% |
| unknown | 6 | 6 | 66.7% | 66.7% |

**by gt_type**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| (blank) | 29 | 25 | 75.9% | 82.8% |

**by tier**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| unknown | 29 | 25 | 75.9% | 82.8% |

**by year_bucket**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| 2019-2022 | 12 | 10 | 75.0% | 83.3% |
| 2023+ | 2 | 1 | 100.0% | 100.0% |
| <=2018 | 15 | 14 | 73.3% | 80.0% |

**by split**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| all | 29 | 25 | 75.9% | 82.8% |

**by gt_ambiguity**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| (blank) | 29 | 25 | 75.9% | 82.8% |

**by match_method**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| doi | 20 | 20 | 75.0% | 100.0% |
| doi+judge | 4 | 3 | 100.0% | 100.0% |
| llm | 5 | 5 | 60.0% | 0.0% |
