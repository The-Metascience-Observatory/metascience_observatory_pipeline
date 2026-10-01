# Extraction benchmark — flora_pilot_30 / tags base_87_r1 / split all

## Provenance

- models: {'claude-sonnet-5': 26}  | ai_version in results: {'8.7-base': 26}
- prompt version now: 8.7 (prompts dirty in git: True); git 67749053b2
- claude CLI: 2.1.258 (Claude Code) | harness 1.0 | 2026-09-02T22:29:14Z
- ground truth: silver:flora files ['silver/flora.csv', 'silver/flora_corrections.csv']; 41 corrections applied
- matcher: {'mode': 'llm', 'provider': 'claude_cli', 'model': 'haiku', 'prompt_version': '1.1', 'calls': 2, 'cache_hits': 0, 'failures': 0, 'offline': False}
- input tiers: {'xml': 5, 'grobid': 21}

## Coverage funnel

papers in GT: 30; not downloaded/converted: 0; not extracted under these tags: 2; bad json: 0; pipeline said no replications: 0; wrong document on disk: 2; scored: 26

## Entry-level matching (effect-level GT only)

TP 25  FN 1  FP 0  → precision 100.0%, recall 96.2%, F1 98.0%
FN breakdown: 1 judged different_original, 0 no candidate row, 0 paper reported no replications
granularity misses (same original, other effect): 0; wrong-original errors flagged by judge: 1; paper-level GT extra rows (unpenalized): 25

## Paper-level (contains_replications)

positives: TP 26 FN 0 | negatives: TN 0 FP 0 (not evaluated: 0) → precision 100.0%, negative-set FPR n/a

## Result classification (3-value; reversal→failure where the GT source lacks the class)

n=25  accuracy 68.0% [48.0%, 84.0%]  κ 0.507  macro-F1 64.5%  (collapse applied on 0 rows)

| class | support | predicted | recall | precision | F1 |
|---|---|---|---|---|---|
| success | 10 | 12 | 80.0% | 66.7% | 72.7% |
| failure | 9 | 7 | 77.8% | 100.0% | 87.5% |
| inconclusive | 6 | 6 | 33.3% | 33.3% | 33.3% |

confusion (rows = GT, cols = pipeline):

| | success | failure | inconclusive |
|---|---|---|---|
| **success** | 8 | 0 | 2 |
| **failure** | 0 | 7 | 2 |
| **inconclusive** | 4 | 0 | 2 |

### Same rows, aggregated per paper (paper-level GT only)

FLoRa labels the whole paper, so this compares its verdict with the aggregate of every pipeline row about the same original, rather than the single row the assignment picked. The strict number above stays the headline.
n=25  accuracy 72.0%  κ 0.580  macro-F1 71.1%

Clear slice (4 rows flagged as a convention boundary in the GT corrections excluded): n=21  accuracy 81.0%  κ 0.698

Pipeline `reversal` across all scored rows (raw, before collapse): predicted 0; GT reversal support 0.

## Replication type

4-class n=0: accuracy n/a, adjacent-or-exact n/a, κ None; 2-class (FReD 'direct or close' vs conceptual) n=0: n/a

## Original study identification

original DOI exact (n=25): 96.0% [88.0%, 100.0%]; pipeline left DOI empty on 4.0%
bibliographic (n=25): title 92.0%, authors 92.0%, year 84.0%, journal 92.0%
citation_sentence: present 100.0%; author+year of GT original found in it 56.0%

## Statistics (denominator = both present; tiers: N exact/±5%/±20%, ES |Δ|≤.01/.05/.10 same type, p ≤1e-4 same type / ≤1e-4 / same side of .05)

| field | GT has | ext has | both | GT-only (miss) | ext-only | type mismatch | tier1 | tier2 | tier3 |
|---|---|---|---|---|---|---|---|---|---|
| original_n | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a |
| original_es | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a |
| original_p_value | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a |
| replication_n | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a |
| replication_es | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a |
| replication_p_value | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a |

## Matcher

{'methods': {'doi': 24, 'llm': 1}, 'relations': {'same_effect': 25}, 'judge_calls': 2, 'cache_hits': 0, 'judge_failures': 0}

## Breakdowns (3-value result accuracy)

**by source**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| external:flora | 25 | 25 | 68.0% | 96.0% |

**by provenance**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| external:flora | 17 | 17 | 76.5% | 94.1% |
| external:flora;corrected:2026-09-02 | 8 | 8 | 50.0% | 100.0% |

**by discipline_group**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| biomed | 5 | 5 | 80.0% | 80.0% |
| lang_edu | 5 | 5 | 60.0% | 100.0% |
| other | 2 | 2 | 100.0% | 100.0% |
| psych | 8 | 8 | 62.5% | 100.0% |
| socsci | 5 | 5 | 60.0% | 100.0% |

**by gt_type**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| (blank) | 25 | 25 | 68.0% | 96.0% |

**by tier**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| grobid | 21 | 21 | 66.7% | 95.2% |
| xml | 4 | 4 | 75.0% | 100.0% |

**by year_bucket**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| 2019-2022 | 9 | 9 | 77.8% | 100.0% |
| 2023+ | 6 | 6 | 83.3% | 83.3% |
| <=2018 | 10 | 10 | 50.0% | 100.0% |

**by split**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| all | 25 | 25 | 68.0% | 96.0% |

**by gt_ambiguity**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| (blank) | 21 | 21 | 81.0% | 95.2% |
| boundary | 4 | 4 | 0.0% | 100.0% |

**by match_method**

| value | n rows | n papers | accuracy | DOI acc |
|---|---|---|---|---|
| doi | 24 | 24 | 66.7% | 100.0% |
| llm | 1 | 1 | 100.0% | 0.0% |
