# Extraction benchmark — flora_pilot / tags f,l,o,r,a,_,p,i,l,o,t / split all

## Provenance

- models: {'claude-sonnet-5': 37}  | ai_version in results: {'8.5': 36, 'unknown': 1}
- prompt version now: 8.7 (prompts dirty in git: True); git 67749053b2
- claude CLI: 2.1.258 (Claude Code) | harness 1.0 | 2026-09-02T16:41:30Z
- ground truth: silver:flora files ['silver/flora.csv']
- matcher: {'mode': 'llm', 'provider': 'claude_cli', 'model': 'haiku', 'prompt_version': '1.0', 'calls': 0, 'cache_hits': 7, 'failures': 0, 'offline': True}
- input tiers: {'unknown': 37}

## Coverage funnel

papers in GT: 50; not downloaded/converted: 12; not extracted under these tags: 1; bad json: 0; pipeline said no replications: 1; scored: 36

## Entry-level matching (effect-level GT only)

TP 33  FN 4  FP 0  → precision 100.0%, recall 89.2%, F1 94.3%
granularity misses (same original, other effect): 0; wrong-original errors flagged by judge: 3; paper-level GT extra rows (unpenalized): 33

## Paper-level (contains_replications)

positives: TP 36 FN 1 | negatives: TN 0 FP 0 (not evaluated: 0) → precision 100.0%, negative-set FPR n/a

## Result classification (3-value; reversal→failure where the GT source lacks the class)

n=33  accuracy 72.7% [57.6%, 87.9%]  κ 0.568  macro-F1 72.7%  (collapse applied on 0 rows)

| class | support | predicted | recall | precision | F1 |
|---|---|---|---|---|---|
| success | 17 | 15 | 70.6% | 80.0% | 75.0% |
| failure | 7 | 8 | 85.7% | 75.0% | 80.0% |
| inconclusive | 9 | 10 | 66.7% | 60.0% | 63.2% |

confusion (rows = GT, cols = pipeline):

| | success | failure | inconclusive |
|---|---|---|---|
| **success** | 12 | 2 | 3 |
| **failure** | 0 | 6 | 1 |
| **inconclusive** | 3 | 0 | 6 |

Pipeline `reversal` across all scored rows (raw, before collapse): predicted 0; GT reversal support 0.

## Replication type

4-class n=0: accuracy n/a, adjacent-or-exact n/a, κ None; 2-class (FReD 'direct or close' vs conceptual) n=0: n/a

## Original study identification

original DOI exact (n=33): 87.9% [75.8%, 97.0%]; pipeline left DOI empty on 0.0%
bibliographic (n=33): title 93.9%, authors 90.9%, year 90.9%, journal 93.9%
citation_sentence: present 100.0%; author+year of GT original found in it 51.5%

## Statistics (denominator = both present; tiers: N exact/±5%/±20%, ES |Δ|≤.01/.05/.10 same type, p ≤1e-4 same type / ≤1e-4 / same side of .05)

| field | GT has | ext has | both | GT-only (miss) | ext-only | type mismatch | tier1 | tier2 | tier3 |
|---|---|---|---|---|---|---|---|---|---|
| original_n | 0 | 11 | 0 | 0 | 11 | 0 | n/a | n/a | n/a |
| original_es | 0 | 7 | 0 | 0 | 7 | 0 | n/a | n/a | n/a |
| original_p_value | 0 | 2 | 0 | 0 | 2 | 0 | n/a | n/a | n/a |
| replication_n | 0 | 32 | 0 | 0 | 32 | 0 | n/a | n/a | n/a |
| replication_es | 0 | 10 | 0 | 0 | 10 | 0 | n/a | n/a | n/a |
| replication_p_value | 0 | 18 | 0 | 0 | 18 | 0 | n/a | n/a | n/a |

## Matcher

{'methods': {'doi': 29, 'llm': 4}, 'relations': {'same_effect': 31, 'subanalysis_of_gt': 2}, 'judge_calls': 0, 'cache_hits': 7, 'judge_failures': 0}

## Breakdowns (3-value result accuracy)

**by source**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| external:flora | 33 | 33 | 72.7% | 87.9% |

**by provenance**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| external:flora | 33 | 33 | 72.7% | 87.9% |

**by discipline_group**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| biomed | 6 | 6 | 83.3% | 100.0% |
| lang_edu | 4 | 4 | 100.0% | 100.0% |
| other | 1 | 1 | 100.0% | 100.0% |
| psych | 18 | 18 | 66.7% | 83.3% |
| socsci | 4 | 4 | 50.0% | 75.0% |

**by gt_type**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| (blank) | 33 | 33 | 72.7% | 87.9% |

**by tier**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| unknown | 33 | 33 | 72.7% | 87.9% |

**by year_bucket**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| 2019-2022 | 12 | 12 | 75.0% | 83.3% |
| 2023+ | 3 | 3 | 66.7% | 100.0% |
| <=2018 | 18 | 18 | 72.2% | 88.9% |

**by split**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| all | 33 | 33 | 72.7% | 87.9% |

**by gt_ambiguity**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| (blank) | 33 | 33 | 72.7% | 87.9% |

**by match_method**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| doi | 29 | 29 | 72.4% | 100.0% |
| llm | 4 | 4 | 75.0% | 0.0% |
