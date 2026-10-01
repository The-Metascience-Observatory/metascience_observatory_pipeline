# Extraction benchmark — main_gt_human / tags sonnet_base_v2_02_2026 / split all

## Provenance

- models: {'claude-sonnet-4-5-20250929': 27}  | ai_version in results: {'unknown': 27}
- prompt version now: 8.8 (prompts dirty in git: False); git c676d6aa9b
- claude CLI: 2.1.260 (Claude Code) | harness 1.2 | 2026-09-04T01:13:06Z
- ground truth: silver:main_gt_human files ['silver/main_gt_human.csv', 'silver/main_gt_human_corrections.csv']; 6 corrections applied
- matcher: {'mode': 'llm', 'provider': 'claude_cli', 'model': 'haiku', 'prompt_version': '1.1', 'calls': 0, 'cache_hits': 12, 'failures': 0, 'offline': False}
- input tiers: {'unknown': 27}

## Coverage funnel

papers in GT: 27; not downloaded/converted: 0; not extracted under these tags: 0; bad json: 0; pipeline said no replications: 1; wrong document on disk: 0; scored: 26

## Entry-level matching (effect-level GT only)

TP 26  FN 7  FP 10  → precision 72.2%, recall 78.8%, F1 75.4%
FN breakdown: 0 judged different_original, 4 no candidate row, 3 paper reported no replications
granularity misses (same original, other effect): 1; wrong-original errors flagged by judge: 0; paper-level GT extra rows (unpenalized): 0

Precision here is a **lower bound**: this ground truth codes the replication each paper is about, not every sub-analysis of it, so an extra extracted row may be a real effect nobody coded. Recall and the FN breakdown are unaffected.
Of the 10 penalized extras, 0 cite an original a matched row already names (per-effect splits of a recorded replication, which the prompt asks for) and 10 name an original the GT does not record at all — only the second group can contain a wrong-original error. See `same_original_as_a_matched_row` in extra_rows.csv.

## Paper-level (contains_replications)

positives: TP 26 FN 1 | negatives: TN 0 FP 0 (not evaluated: 0) → precision 100.0%, negative-set FPR n/a

## Result classification (3-value; reversal→failure where the GT source lacks the class)

n=26  accuracy 76.9% [60.0%, 92.3%]  κ 0.537  macro-F1 63.9%  (collapse applied on 1 rows)

| class | support | predicted | recall | precision | F1 |
|---|---|---|---|---|---|
| success | 6 | 5 | 66.7% | 80.0% | 72.7% |
| failure | 19 | 16 | 78.9% | 93.8% | 85.7% |
| inconclusive | 1 | 5 | 100.0% | 20.0% | 33.3% |

confusion (rows = GT, cols = pipeline):

| | success | failure | inconclusive |
|---|---|---|---|
| **success** | 4 | 1 | 1 |
| **failure** | 1 | 15 | 3 |
| **inconclusive** | 0 | 0 | 1 |

### Same rows, aggregated per original study

Where the ground truth carries one verdict for a paper (FLoRa) or codes one effect of an original the pipeline split into several rows, this compares that verdict with the aggregate of every pipeline row about the same original, rather than the single row the assignment picked. The strict number above stays the headline.
n=15  accuracy 73.3%  κ 0.450  macro-F1 61.9%

Pipeline `reversal` across all scored rows (raw, before collapse): predicted 1; GT reversal support 0.

## Replication type

4-class n=0: accuracy n/a, adjacent-or-exact n/a, κ None; 2-class (FReD 'direct or close' vs conceptual) n=0: n/a

## Original study identification

original DOI exact (n=26): 69.2% [50.0%, 88.0%]; pipeline left DOI empty on 11.5%
bibliographic (n=26): title 92.3%, authors 69.2%, year 76.9%, journal 69.2%
citation_sentence: present 0.0%; author+year of GT original found in it n/a

## Statistics (denominator = both present; tiers: N exact/±5%/±20%, ES |Δ|≤.01/.05/.10 same type, p ≤1e-4 same type / ≤1e-4 / same side of .05)

| field | GT has | ext has | both | GT-only (miss) | ext-only | type mismatch | tier1 | tier2 | tier3 |
|---|---|---|---|---|---|---|---|---|---|
| original_n | 19 | 0 | 0 | 19 | 0 | 0 | n/a | n/a | n/a |
| original_es | 5 | 0 | 0 | 5 | 0 | 0 | n/a | n/a | n/a |
| original_p_value | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a |
| replication_n | 19 | 0 | 0 | 19 | 0 | 0 | n/a | n/a | n/a |
| replication_es | 6 | 0 | 0 | 6 | 0 | 0 | n/a | n/a | n/a |
| replication_p_value | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a |

## Matcher

{'methods': {'doi': 17, 'llm': 8, 'doi+judge': 1}, 'relations': {'same_effect': 26}, 'judge_calls': 0, 'cache_hits': 12, 'judge_failures': 0}

## Breakdowns (3-value result accuracy)

**by source**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| external:main_gt_human | 26 | 25 | 76.9% | 69.2% |

**by provenance**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| human:dan_elton | 10 | 10 | 60.0% | 50.0% |
| human:dan_elton;corrected:2026-09-02 | 3 | 3 | 100.0% | 66.7% |
| human:forrt_cd | 3 | 3 | 100.0% | 66.7% |
| human:forrt_lk | 10 | 9 | 80.0% | 90.0% |

**by discipline_group**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| biomed | 1 | 1 | 100.0% | 100.0% |
| other | 17 | 16 | 70.6% | 70.6% |
| psych | 2 | 2 | 100.0% | 100.0% |
| unknown | 6 | 6 | 83.3% | 50.0% |

**by gt_type**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| (blank) | 26 | 25 | 76.9% | 69.2% |

**by tier**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| unknown | 26 | 25 | 76.9% | 69.2% |

**by year_bucket**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| 2019-2022 | 10 | 10 | 80.0% | 80.0% |
| 2023+ | 2 | 1 | 100.0% | 100.0% |
| <=2018 | 14 | 14 | 71.4% | 57.1% |

**by split**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| all | 26 | 25 | 76.9% | 69.2% |

**by gt_ambiguity**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| (blank) | 26 | 25 | 76.9% | 69.2% |

**by match_method**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| doi | 17 | 17 | 82.4% | 100.0% |
| doi+judge | 1 | 1 | 100.0% | 100.0% |
| llm | 8 | 8 | 62.5% | 0.0% |
