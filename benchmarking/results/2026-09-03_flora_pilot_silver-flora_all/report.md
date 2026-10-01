# Extraction benchmark — flora_pilot / tags flora_pilot / split all

## Provenance

- models: {'claude-sonnet-5': 37}  | ai_version in results: {'8.5': 36, 'unknown': 1}
- prompt version now: 8.8 (prompts dirty in git: False); git c676d6aa9b
- claude CLI: 2.1.260 (Claude Code) | harness 1.2 | 2026-09-04T01:06:14Z
- ground truth: silver:flora files ['silver/flora.csv', 'silver/flora_corrections.csv']; 41 corrections applied
- matcher: {'mode': 'llm', 'provider': 'claude_cli', 'model': 'haiku', 'prompt_version': '1.1', 'calls': 8, 'cache_hits': 0, 'failures': 0, 'offline': False}
- input tiers: {'unknown': 37}

## Coverage funnel

papers in GT: 50; not downloaded/converted: 12; not extracted under these tags: 1; bad json: 0; pipeline said no replications: 1; wrong document on disk: 0; scored: 36

## Entry-level matching (effect-level GT only)

TP 35  FN 2  FP 0  → precision 100.0%, recall 94.6%, F1 97.2%
FN breakdown: 1 judged different_original, 0 no candidate row, 1 paper reported no replications
granularity misses (same original, other effect): 0; wrong-original errors flagged by judge: 1; paper-level GT extra rows (unpenalized): 31

## Paper-level (contains_replications)

positives: TP 36 FN 1 | negatives: TN 0 FP 0 (not evaluated: 0) → precision 100.0%, negative-set FPR n/a

## Result classification (3-value; reversal→failure where the GT source lacks the class)

n=35  accuracy 74.3% [60.0%, 88.6%]  κ 0.583  macro-F1 73.6%  (collapse applied on 0 rows)

| class | support | predicted | recall | precision | F1 |
|---|---|---|---|---|---|
| success | 19 | 17 | 73.7% | 82.4% | 77.8% |
| failure | 7 | 8 | 85.7% | 75.0% | 80.0% |
| inconclusive | 9 | 10 | 66.7% | 60.0% | 63.2% |

confusion (rows = GT, cols = pipeline):

| | success | failure | inconclusive |
|---|---|---|---|
| **success** | 14 | 2 | 3 |
| **failure** | 0 | 6 | 1 |
| **inconclusive** | 3 | 0 | 6 |

### Same rows, aggregated per original study

Where the ground truth carries one verdict for a paper (FLoRa) or codes one effect of an original the pipeline split into several rows, this compares that verdict with the aggregate of every pipeline row about the same original, rather than the single row the assignment picked. The strict number above stays the headline.
n=35  accuracy 74.3%  κ 0.588  macro-F1 74.3%

Clear slice (2 rows flagged as a convention boundary in the GT corrections excluded): n=33  accuracy 78.8%  κ 0.652

Pipeline `reversal` across all scored rows (raw, before collapse): predicted 0; GT reversal support 0.

## Replication type

4-class n=0: accuracy n/a, adjacent-or-exact n/a, κ None; 2-class (FReD 'direct or close' vs conceptual) n=0: n/a

## Original study identification

original DOI exact (n=35): 80.0% [65.7%, 91.4%]; pipeline left DOI empty on 0.0%
bibliographic (n=35): title 85.7%, authors 82.9%, year 85.7%, journal 88.6%
citation_sentence: present 100.0%; author+year of GT original found in it 48.6%

## Statistics (denominator = both present; tiers: N exact/±5%/±20%, ES |Δ|≤.01/.05/.10 same type, p ≤1e-4 same type / ≤1e-4 / same side of .05)

| field | GT has | ext has | both | GT-only (miss) | ext-only | type mismatch | tier1 | tier2 | tier3 |
|---|---|---|---|---|---|---|---|---|---|
| original_n | 0 | 12 | 0 | 0 | 12 | 0 | n/a | n/a | n/a |
| original_es | 0 | 9 | 0 | 0 | 9 | 0 | n/a | n/a | n/a |
| original_p_value | 0 | 3 | 0 | 0 | 3 | 0 | n/a | n/a | n/a |
| replication_n | 0 | 34 | 0 | 0 | 34 | 0 | n/a | n/a | n/a |
| replication_es | 0 | 12 | 0 | 0 | 12 | 0 | n/a | n/a | n/a |
| replication_p_value | 0 | 20 | 0 | 0 | 20 | 0 | n/a | n/a | n/a |

## Matcher

{'methods': {'llm': 7, 'doi': 28}, 'relations': {'same_effect': 33, 'subanalysis_of_gt': 2}, 'judge_calls': 8, 'cache_hits': 0, 'judge_failures': 0}

## Breakdowns (3-value result accuracy)

**by source**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| external:flora | 35 | 35 | 74.3% | 80.0% |

**by provenance**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| external:flora | 32 | 32 | 78.1% | 81.2% |
| external:flora;corrected:2026-09-02 | 3 | 3 | 33.3% | 66.7% |

**by discipline_group**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| biomed | 7 | 7 | 85.7% | 85.7% |
| lang_edu | 4 | 4 | 100.0% | 100.0% |
| other | 1 | 1 | 100.0% | 100.0% |
| psych | 19 | 19 | 68.4% | 73.7% |
| socsci | 4 | 4 | 50.0% | 75.0% |

**by gt_type**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| (blank) | 35 | 35 | 74.3% | 80.0% |

**by tier**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| unknown | 35 | 35 | 74.3% | 80.0% |

**by year_bucket**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| 2019-2022 | 12 | 12 | 75.0% | 83.3% |
| 2023+ | 3 | 3 | 66.7% | 100.0% |
| <=2018 | 20 | 20 | 75.0% | 75.0% |

**by split**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| all | 35 | 35 | 74.3% | 80.0% |

**by gt_ambiguity**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| (blank) | 33 | 33 | 78.8% | 78.8% |
| boundary | 2 | 2 | 0.0% | 100.0% |

**by match_method**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| doi | 28 | 28 | 71.4% | 100.0% |
| llm | 7 | 7 | 85.7% | 0.0% |
