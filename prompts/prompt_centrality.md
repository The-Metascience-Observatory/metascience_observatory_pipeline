# Claim Centrality Labeling

You are labeling claims for **The Metascience Observatory**. Each claim below was tested by a replication study. Your job is to judge, for each claim, whether it was a **central claim** of the ORIGINAL paper it came from — i.e., part of the original paper's main contribution — or a secondary finding.

## Blinding rule (critical — read first)

Your judgment must be based ONLY on how the original study and its claims are **described**:

- the original paper's title, abstract, and metadata (provided in the task input),
- how the replication paper's introduction/background sections describe the original study and why it matters.

You must NOT use, weigh, or mention **replication outcomes** — whether any replication succeeded, failed, or what effect sizes were found. If you encounter results, discussion, or conclusion content about how the replication turned out, disregard it entirely. Centrality is a property of the original paper as published, independent of what later replications found. Do not let "this claim held up" or "this claim didn't" influence the label in either direction, and do not mention outcomes in your rationale.

## Labels

**central** — The claim is (one of) the original paper's main contribution(s): the finding the title/abstract is about, the effect the paper is cited for, the hypothesis the paper was designed to test. A paper CAN have more than one central claim (e.g., Fama & MacBeth 1973 tested three named hypotheses of the CAPM — linearity, no non-beta risk, positive risk–return trade-off — all three are central). Signals: the claim appears in the title or abstract as a contribution; the replication paper introduces it as "the seminal finding", "the key result", "the X effect" named after the paper.

**secondary** — The claim is auxiliary to the main contribution: a moderator or interaction qualifying the main effect, a subgroup or robustness analysis, a manipulation check, one of many exploratory correlations reported alongside the headline result, or a supporting measurement. Signals: not mentioned in the original's abstract; the replication paper describes it as "additionally", "a secondary analysis", "Study 3's supplementary finding".

**cannot_determine** — The claim description is too vague or ambiguous to match to anything in the original paper, AND neither the original's abstract nor the replication paper's description of the original resolves it. Use this honestly rather than guessing; do not use it merely because the judgment is difficult.

## Judging guidance

- The unit is the CLAIM, not the paper. When several claims from the same original paper are listed, judge each one; they may well differ (some central, some secondary).
- A claim phrased narrowly (specific operationalization) still counts as central if it is the paper's main effect as actually tested.
- For multi-study original papers, the central claim(s) are those of the paper as a whole (what the abstract asserts), not every per-study result.
- Original papers whose abstract is unavailable: rely on the title and on how the replication paper's introduction characterizes the original.

## Workflow

1. Read the task input: original paper metadata + abstract, and the list of claims with `row_id`s.
2. Read the replication paper in the provided folder — prefer `abstract.md` and the introduction/background portion of `body.md` (or the PDF if no markdown). You are looking for how it DESCRIBES the original study: why it was chosen for replication, what its contribution was. Per the blinding rule, skip results/discussion content about replication outcomes.
3. Label every claim. Every `row_id` in the input must appear exactly once in the output.

## Output

Write a file named `centrality_result.json` at the exact output path given in the task input, containing ONLY:

```json
{
  "labels": [
    {
      "row_id": "<row_id from input>",
      "label": "central | secondary | cannot_determine",
      "confidence": "high | medium | low",
      "rationale": "One sentence: why this claim is/isn't part of the original paper's main contribution. No replication outcomes."
    }
  ]
}
```

No other keys, no commentary outside the JSON file.
