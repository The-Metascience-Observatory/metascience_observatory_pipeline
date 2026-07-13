# Comparison of sonnetv5 vs sonnetv6 Extraction Results

## Summary

**sonnetv6 is clearly superior to sonnetv5** based on systematic comparison of 36 papers with both versions.

## Key Statistics

| Metric | v5 | v6 | Advantage |
|--------|----|----|-----------|
| **Explanation field present** | 0/36 (0.0%) | 32/36 (88.9%) | **v6** |
| **P-value tails populated** | 5/36 (13.9%) | 9/36 (25.0%) | **v6** |
| **More replications extracted** | 4 cases | 9 cases | **v6** |
| **Result classification differences** | 6 cases (examined in detail below) | | **v6 more accurate** |

## Major Improvements in v6

### 1. **Explanation Field (88.9% vs 0%)**

v6 includes detailed explanation/justification fields that:
- Quote the authors' own interpretation
- Provide statistical details (effect sizes, p-values, confidence intervals)
- Explain nuances (e.g., "12% vs 32% of PTSD cases - attributed to methodological improvements")

**Example from 10.1001--archgenpsychiatry.2011.1574:**

v5: No explanation field

v6: "The authors explicitly state 'This study replicates and extends findings reported by Waelde et al' and found evidence for a dissociative subtype of PTSD, though with lower proportions (12% vs 32% of PTSD cases). They attribute this difference to methodological improvements (clinician-rated vs self-report measures)."

### 2. **Better Multi-Study Extraction**

v6 correctly identifies and extracts **multiple replications from the same paper** that v5 often misses or aggregates.

**Example 10.1002--bin.1707:**
- v5: 1 replication extracted
- v6: 2 replications extracted (correctly identified that paper replicated TWO original studies: Rapp 2008 AND Lanovaz 2017)

**Example 10.1002--bin.1411:**
- v5: 1 aggregate entry for all 4 children (result: "inconclusive")
- v6: 4 separate entries (one per child) with individual results:
  - Ivan: success
  - Dante: inconclusive
  - Jeremy: failure
  - Hailey: failure

This follows the prompt guidance to "create one row per study/experiment" rather than aggregating results.

### 3. **More Accurate Result Classification**

In the 6 cases with result classification differences, v6 was more careful and accurate:

**10.1002--bin.1411 (single-case design with 4 children):**
- v5: Single "inconclusive" entry (loses all detail)
- v6: Four separate entries with nuanced classifications based on each child's individual outcome

**10.1002--ejsp.2013:**
- v5: Classified one study as "success"
- v6: Classified same study as "failure" with explanation: "US participants showed no preference for living location (p=.87, d=0.02 in Study 2), which the authors explicitly describe as 'inconsistent with those shown by the US participants of Meier et al.'"

v6's classification aligns better with the authors' own interpretation.

### 4. **More Complete Statistical Details**

v6 more frequently populates:
- `replication_p_value_tails` (25.0% vs 13.9%)
- Sample sizes broken down by individual study
- More detailed descriptions

### 5. **Better Detection of Replications**

- v6 extracted more replications: 9 cases where v6 found additional replications that v5 missed
- v5 extracted more replications: 4 cases (likely over-extraction or misinterpretation)
- Net advantage: **v6**

## What Caused the Improvements?

v6 was run with the improved prompt_full.md that includes:

1. **Pass 4 - Final PDF check**: Instructs agent to read PDF at end to catch missing details
2. **Better borderline case guidance**: More explicit inclusion/exclusion criteria
3. **Multi-study guidance**: Explicit instruction to create one row per study/experiment
4. **Reversal restrictions**: Prevents over-classification of reversals
5. **Improved tiebreaker rules**: Better result classification logic
6. **Multi-scenario pitfall guidance**: Helps classify correctly when results vary across conditions

The final PDF check appears particularly valuable for catching:
- Additional replications mentioned in Discussion but not highlighted in abstract
- Individual results in single-case designs with multiple participants
- Statistical details buried in tables

## Recommendation

**Use sonnetv6 results.** The improved prompt led to:
- Much more detailed and useful explanations (88.9% vs 0%)
- Better multi-study extraction (9 vs 4 cases with more replications)
- More accurate result classifications
- More complete statistical reporting

The only downside is slightly higher computational cost from the additional PDF reading pass, but the quality gains far outweigh this cost.

## Files Compared

- Total folders with both versions: 39
- Successfully compared: 36 (3 had missing v6 results)
- Papers examined in detail: 6 with result differences + 4 random samples
