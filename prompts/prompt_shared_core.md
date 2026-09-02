## Output

<!-- mode:write -->
When you have your result, use the **Write** tool to save it as `result.json` in the paper directory. The JSON must follow this schema:
<!-- /mode -->
<!-- mode:reply -->
Reply with the JSON object only — no prose before or after it, no code fences. It must follow this schema:
<!-- /mode -->

```json
{
  "contains_replications": true,
  "replications": [
    {
      "original_url": "https://doi.org/10.1234/example",
      "original_authors": "Smith, J.; Jones, A.",
      "original_title": "The original study title",
      "original_journal": "Journal of Example Studies",
      "original_volume": "42",
      "original_issue": "3",
      "original_pages": "112-128",
      "original_year": "2015",
      "description": "Providing a default enrollment option increases retirement savings plan participation rates",
      "result": "success",
      "replication_type": "direct",
      "discipline": "economics",
      "subdiscipline": "Behavioral Economics",
      "confidence": "high",
      "explanation": "The authors explicitly state in their conclusion: 'we successfully replicated the main findings of the original study with similar effect sizes'",
      "citation_sentence": "We aimed to replicate Smith and Jones (2015), who found that providing a default enrollment option significantly increased participation rates.",
<!-- mode:full -->
      "original_n": "",
      "original_es": "",
      "original_es_type": "",
      "original_es_95_CI": "",
      "original_p_value": "",
      "original_p_value_type": "",
      "original_p_value_tails": "",
      "replication_n": "",
      "replication_es": "",
      "replication_es_type": "",
      "replication_es_95_CI": "",
      "replication_p_value": "",
      "replication_p_value_type": "",
      "replication_p_value_tails": ""
<!-- /mode -->
    }
  ]
}
```

### Multi-study Replications

**IMPORTANT: When a paper replicates multiple studies or experiments, create separate entries for each one.**

**Case A: Multiple original papers replicated**
- Create one row per distinct original study
- Example: Paper replicates Smith (2010), Jones (2015), Brown (2018) → **3 separate rows**

**Case B: Multiple studies/experiments from the SAME original paper**
- Create one row per study/experiment replicated from that paper
- Example: Paper replicates Studies 1, 3, and 5 from Smith (2010) → **3 separate rows**, all with same `original_url`
- Each row has a different `description` (describing what that specific study tested)
- Each row gets its own `result` classification based on that study's outcome
<!-- mode:full -->
- Each row should include statistical details specific to that study
<!-- /mode -->

**Example output for multi-study replication:**

If a paper replicates Studies 1, 2, and 4 from the same original paper:

```json
{
  "contains_replications": true,
  "replications": [
    {
      "original_url": "https://doi.org/10.1234/original",
      "original_authors": "Smith, J.; Jones, A.",
      "original_title": "The Original Study Title",
      "description": "Study 1: Priming with elderly-related words causes participants to walk more slowly",
      "result": "failure",
      "replication_type": "direct",
<!-- mode:full -->
      "original_n": "30",
      "original_es": "0.50",
      "original_es_type": "d",
      "replication_n": "150",
      "replication_es": "0.02",
      "replication_es_type": "d",
<!-- /mode -->
      ...
    },
    {
      "original_url": "https://doi.org/10.1234/original",
      "original_authors": "Smith, J.; Jones, A.",
      "original_title": "The Original Study Title",
      "description": "Study 2: Priming with elderly-related words affects performance on lexical decision task",
      "result": "success",
      "replication_type": "direct",
<!-- mode:full -->
      "original_n": "40",
      "original_es": "0.35",
      "original_es_type": "d",
      "replication_n": "180",
      "replication_es": "0.32",
      "replication_es_type": "d",
<!-- /mode -->
      ...
    },
    {
      "original_url": "https://doi.org/10.1234/original",
      "original_authors": "Smith, J.; Jones, A.",
      "original_title": "The Original Study Title",
      "description": "Study 4: Priming with elderly-related words impairs memory recall performance",
      "result": "failure",
      "replication_type": "close experiment",
<!-- mode:full -->
      "original_n": "35",
      "original_es": "0.42",
      "original_es_type": "d",
      "replication_n": "160",
      "replication_es": "-0.05",
      "replication_es_type": "d",
<!-- /mode -->
      ...
    }
  ]
}
```

Each study gets its own entry even though they all replicate the same original paper. Notice:
- Same `original_url`, `original_authors`, and `original_title` for all entries
- Different `description` for each study (specify which study and what it tested)
- Different `result` classifications based on each study's individual outcome
<!-- mode:full -->
- Different statistical details (sample sizes, effect sizes) for each study
<!-- /mode -->

**When to create a single entry:** Only when a paper reports an aggregate result across all studies without breaking them down individually (rare). If the paper provides individual results for each study, create separate entries.

**What counts as a separate replication entry:**
- Each distinct study/experiment with its own participants or independent design
- NOT each individual statistical test, mediator path, or dependent variable within one study
- If a single study tests whether A mediates X→Y AND whether B mediates X→Y using the same participants, that is ONE replication entry, not two

Example: A paper replicates a mediation study testing 3 mediator pathways using the same 300 participants. This is 1 replication entry (the mediation study), not 3 separate entries.

<!-- mode:write -->
You may narrate your reasoning as you work — this is saved for debugging. But you **must** write `result.json` before finishing.
<!-- /mode -->
<!-- mode:reply -->
Do not narrate. Your entire reply must be the JSON object.
<!-- /mode -->

## Field Reference

### Required fields

| Field | Type | Description |
|-------|------|-------------|
| `original_url` | string | DOI URL (`https://doi.org/...`). `""` if not found — do not fabricate. |
| `original_authors` | string | Semicolon-separated full names (e.g., `"Smith, John; Jones, Alice"`) |
| `original_title` | string | Title in sentence case (only first word capitalized). **Always extract this even if DOI is missing.** |
| `original_journal` | string | Full journal or venue name |
| `original_volume` | string | Volume number. `""` if unavailable. |
| `original_issue` | string | Issue number. `""` if unavailable. |
| `original_pages` | string | Page range (e.g., `"112-128"`). `""` if unavailable. |
| `original_year` | string | Publication year |
| `description` | string | One sentence describing the **original study's claimed effect** that was being replicated — NOT what the replication found. Example: "Exposure to elderly-related words causes participants to walk more slowly" (the original claim), not "Priming did not affect walking speed" (the replication result). |
| `result` | string | `"success"`, `"failure"`, `"inconclusive"`, or `"reversal"` |
| `replication_type` | string | `"direct"`, `"close experiment"`, `"close extension"`, or `"conceptual"` — see Replication Type section below |
| `discipline` | string | One value from the discipline list below |
| `subdiscipline` | string | One value from the subdiscipline list below |
| `confidence` | string | `"low"`, `"medium"`, or `"high"` — your confidence that this entry is correct (see below) |
| `explanation` | string | A one to two sentence explanation/justification for the result finding, perhaps featuring a brief quote from the text in support of the result. |
| `citation_sentence` | string | The exact sentence(s) from the paper where the authors identify which prior study they are replicating. Must include the author name(s) and year as cited in the text. This grounds the original study identification in specific textual evidence. |

<!-- mode:full -->
### Statistical fields (lower priority — extract if reported, otherwise leave as "")

These fields are secondary to the required fields above. Most papers will not report all of them — that is expected and fine.

**Only fill in a statistical field if you are confident the value is correct.** It is far better to leave a field as `""` than to put in a wrong value. Do **not** guess, estimate, calculate, derive, or infer values. Do **not** search the web for statistics. Only extract values that are explicitly and clearly stated in the paper text you have already read. If there is any doubt, leave it as `""`.

| Field | Type | Description |
|-------|------|-------------|
| `original_n` | integer or `""` | Number of subjects actually analyzed in the original experiment (analytical N). Prefer the N used in statistical tests over the N recruited, as participants may be excluded. If the paper reports degrees of freedom (e.g., df=24 for a correlation), use df+1 for the analytical N rather than a larger "recruited" number. `""` if not reported. Must be a JSON number (e.g., `471`), not a quoted string (`"471"`). |
| `original_es` | float or `""` | Effect size in the original experiment. `""` if not reported. |
| `original_es_type` | string | Type of effect size: `"d"` (Cohen's d), `"r"` (Pearson's r), `"etasq"` (eta-squared), `"g"` (Hedges' g), `"OR"` (odds ratio), or other. `""` if not reported. |
| `original_es_95_CI` | [float, float] or `""` | 95% confidence interval for the original effect size, as a two-element array. `""` if not reported. |
| `original_p_value` | float or `""` | p-value from the original experiment. `""` if not reported. |
| `original_p_value_type` | string | How the p-value is reported: `"<"` (less than, e.g., "p < .05"), `"="` (exact value, e.g., "p = .03"), `">"` (greater than, e.g., "p > .05"), or `""` if not specified. These are the only allowed values — never use `">="`, `"<="`, `"≈"`, or any other symbol. |
| `original_p_value_tails` | string | `"one-sided"`, `"two-sided"`, or `""` if not specified |
| `replication_n` | integer or `""` | Number of subjects actually analyzed in the replication experiment (analytical N). Prefer the N used in statistical tests over the N recruited, as participants may be excluded. If the paper reports degrees of freedom (e.g., df=24 for a correlation), use df+1 for the analytical N rather than a larger "recruited" number. `""` if not reported. Must be a JSON number (e.g., `519`), not a quoted string (`"519"`). |
| `replication_es` | float or `""` | Effect size in the replication experiment. `""` if not reported. |
| `replication_es_type` | string | Type of effect size (same codes as above). `""` if not reported. |
| `replication_es_95_CI` | [float, float] or `""` | 95% confidence interval for the replication effect size. `""` if not reported. |
| `replication_p_value` | float or `""` | p-value from the replication experiment. `""` if not reported. |
| `replication_p_value_type` | string | How the p-value is reported: `"<"` (less than, e.g., "p < .05"), `"="` (exact value, e.g., "p = .03"), `">"` (greater than, e.g., "p > .05"), or `""` if not specified. These are the only allowed values — never use `">="`, `"<="`, `"≈"`, or any other symbol. |
| `replication_p_value_tails` | string | `"one-sided"`, `"two-sided"`, or `""` if not specified |

**Paired-field rule**: `original_es` and `original_es_type` must be filled together or both left as `""`. Same for `replication_es` / `replication_es_type`. Never fill one side without the other — a bare number with no type cannot be interpreted downstream.

### Guidelines for statistical extraction

- Extract effect sizes and p-values **for the specific effect being replicated**, not for the overall study or other analyses.
- If the paper reports the original study's statistics (common in replication papers), extract those. If not, leave as `""`.
- If a p-value is reported as an inequality (e.g., "p < .001"), record the bound value (0.001) in `original_p_value` and set `original_p_value_type` to `"<"`.
- If a p-value is reported as an exact value (e.g., "p = .03"), record the value (0.03) and set `original_p_value_type` to `"="`.
- If a p-value is reported as "ns" or "not significant" without a number, leave as `""`.
- Effect size types should match what the paper reports. Do not convert between types — downstream processing handles conversions.
- For confidence intervals, always use the 95% CI if multiple are reported.
<!-- /mode -->

## Replication Type

Classify each replication as one of four categories. These form a spectrum from most methodologically similar to most methodologically different from the original:

**direct** — The experimental procedure was repeated as closely as possible, following the original study's specifications. Same manipulation, same measures, same general procedure. Minor unavoidable differences (different participants, different lab, different time period) do not disqualify. If the authors call it a "direct replication" or "exact replication", use this.
- Examples: pre-registered replication following original protocol, same paradigm with larger sample, Multi-Lab or Many Labs replication
- Key signal: the goal is to *reproduce* the original result using the same methods

**close experiment** — The same general paradigm and methodology as the original, but with deliberate methodological changes that don't fundamentally alter what is being tested. The core manipulation and measures are recognizably the same, but the researchers made intentional modifications for practical, methodological, or improvement reasons. The goal is still to test the *same specific effect* using the *same general approach*, in the *same general context*.
- Examples: updated stimulus materials while keeping the same paradigm, improved experimental controls, used a computerized version of a previously paper-based task, added manipulation checks, pre-registered version with minor procedural refinements, different but equivalent measure of the same construct
- Key signal: changes were made to *how* the study is run (methodology) while keeping the same context/setting/population

**close extension** — The core hypothesis and general paradigm are preserved, but the authors test whether the effect holds in an expanded setting or under a different context. The goal shifts from "can we reproduce this result?" to "does this effect generalize beyond the original conditions?"
- Examples: translated to a different language or culture, conducted online instead of in-person, tested on a different population (e.g., children instead of adults, clinical sample instead of healthy, different country), used modified stimuli to test generalizability, applied the paradigm to a new domain, field study of a lab finding
- Key signal: changes were made to *where/who/when* the study is run (context), testing whether the effect generalizes
- **Note:** If both the methodology AND the context/setting are changed, classify as **close extension** (the broader category). A study that tweaks the procedure AND tests in a new population is a close extension, not a close experiment.

**conceptual** — The same theoretical claim or effect is tested using a fundamentally different experimental procedure — different manipulation, different measures, or a different paradigm altogether. The authors test whether the same conclusion holds under different methodological conditions.
- Examples: testing the same hypothesis with a completely different experimental design, using a different manipulation to produce the same predicted outcome, measuring the same construct with an entirely different methodology
- Key signal: someone unfamiliar with the studies might not immediately recognize they are testing the same thing

**Tiebreakers:**
- Authors' own characterization wins when available ("we conducted a direct replication" → direct)
- Doubt between direct/close → **direct** (minor variations are normal in any replication)
- Doubt between close experiment/close extension → if both methodology AND context changed → **close extension**. If only methodology changed → **close experiment**. If only context changed → **close extension**
- Doubt between close extension/conceptual → **close extension** (if the core paradigm is recognizably the same)

## Result Classification

Classify based on what the **replication authors report**. The Discussion/Conclusion section is the primary source for classification — always read it before classifying.

### Step 1: Check for the authors' explicit statement

Prioritize the authors' own words. Look for phrases like:
- "successfully replicated", "confirmed", "consistent with the original" → **success**
- "failed to replicate", "did not replicate", "no support for" → **failure**
- "partially replicated", "mixed results", "some support", "limited support" → **inconclusive**
- "opposite direction", "reversed", "contrary to the original" → **reversal**

If the authors make an explicit statement, use it. Only override if the statement clearly contradicts the reported statistics.

### Step 2: If no explicit statement, evaluate the evidence

**success** — All of these should be true:
- Statistically significant effect in the same direction
- Authors discuss the finding as confirming the original
- If multi-experiment: all or nearly all experiments show the effect

**failure** — Any of these:
- No significant effect found AND authors frame it as a failure
- Confidence interval includes zero/null AND authors don't claim support
- All experiments show null results

**inconclusive** — Use this when the result is genuinely ambiguous. Do not force a clear success/failure when the evidence is mixed. Any of these:
- **Qualified support**: Effect in expected direction but not significant, AND authors use hedging language ("trending", "marginal", "approaching significance") rather than calling it a clear failure
- **Conditional success**: Effect found only in subset of conditions or moderator levels within the same experiment
- **Mediated/indirect only**: Original tested direct effect A→B, this replication finds only indirect path A→C→B
- **Mixed signals within one experiment**: Significant on one measure but not another
- **Significant but questionable**: Statistically significant effect but authors express substantial validity concerns or caveats that undermine confidence in the result
- **Authors explicitly hedge**: Language like "some evidence", "limited support", "partially consistent", "weak support" for THIS SPECIFIC experiment's outcome

**reversal** — Statistically significant effect in the **opposite** direction from the original.

**IMPORTANT: "reversal" is extremely rare.** Only classify as reversal when ALL of these are true for this specific experiment:
1. Statistically significant effect in the OPPOSITE direction
2. Authors themselves describe it as a reversal or opposite finding for this experiment
3. The reversed effect is clear and unambiguous

If only some conditions within the experiment show opposite effects → "inconclusive", not "reversal".
When in doubt between reversal and anything else, choose the other category.

### Step 3: Tiebreaker rules (for a single experiment)
- Doubt between failure/inconclusive → **inconclusive** (if there is genuine ambiguity, reflect it)
- Doubt between success/inconclusive → use authors' explicit statement for THIS experiment
- Authors explicitly claim "we replicated [this experiment]" + significant effect in same direction → **success** (even if effect is weaker than original)
- Significant effect but authors express substantial validity concerns about THIS experiment → **inconclusive**
- Registered Report with clear null result → **failure**

### Common pitfalls
1. **Don't over-rely on p-values.** A single p < .05 does not automatically mean "success" — consider the full pattern of evidence and authors' discussion.
2. **Partial support = inconclusive**, not success. When describing aggregate results like "2 out of 4 experiments replicated", that overall characterization would be "inconclusive". However, create separate rows for each of the 4 experiments with their individual results.
3. **Mediated effects ≠ direct replication success.** If the original found A→B directly but replication only finds A→C→B, that's inconclusive.
4. **Multi-experiment papers: Create separate rows.** When a paper replicates multiple studies or experiments, create one row per study replicated. Each row gets classified based on that specific study's outcome. Don't collapse multiple studies into a single row just because they come from the same original paper or test related hypotheses.
5. **Read the Discussion section.** Authors' own interpretation matters more than your independent read of the statistics.
6. **Multi-scenario papers**: If the same effect is tested across multiple scenarios/vignettes and results differ across them, classify as "inconclusive". Do not report based on a single scenario when multiple were tested.

## Discipline & Subdiscipline

Select one discipline and one subdiscipline from the hierarchy below (based on the **original study's** topic). Format shows: discipline [subdisciplines]. If nothing fits exactly, choose the closest match or use "other".

**Genetic associations — classify by the relationship, not the organ system.** When the finding is an association between a genetic variant and a phenotype, do not file it under the clinical specialty that owns the phenotype. Use `medical fields / medical genetics` when the phenotype is a diagnosed disease in a patient population or a genotype-driven treatment response; use `biology / genetics` when it is a quantitative trait or a biological mechanism. Reach for the specialty itself (`ophthalmology`, `nephrology`, `cardiovascular medicine`, …) only when the claim is about care, diagnosis, or treatment rather than about a variant. A GWAS of glaucoma published in an eye journal is `medical genetics`, not `ophthalmology`.

{{DISCIPLINE_LIST}}

## Confidence

Rate your confidence that the specific extraction is correct overall — that you correctly identified this as a replication, found the right original study, and classified the result accurately.

- **high** — The paper clearly states it is replicating a specific prior study, the original is unambiguous, and the result is clearly reported.
- **medium** — The replication is likely correct but there is some ambiguity (e.g., unclear replication type, multiple possible originals, or the result classification is a judgment call).
- **low** — Significant uncertainty about whether this qualifies as a replication, which original study is being replicated, or what the result was.

## Edge Cases

- **Multi-study papers**: Only extract replication experiments, not original experiments.
- **Self-replications**: Include — authors replicating their own prior work counts.
- **Conceptual replications**: Include — tag as `"conceptual"` in the `replication_type` field.
- **Multiple originals**: One row per effect per original study.
- **Multi-effect replications from the same original**: If a paper replicates multiple distinct effects from the same original study (e.g., effects on compassion, empathy, and Theory of Mind), create one row per effect with the same `original_url` but different `description` and potentially different `result`.
- **Within-paper replications**: Exclude. If Study 2 replicates Study 1 within the same paper (even with separate participants), this does not count as a qualifying replication. The original study must be a separately published work.
- **Missing DOI**: Leave `""`. Title + journal + year will be used to resolve it downstream. But **always extract the title**.
<!-- mode:full -->
- **Missing statistics**: Many papers won't report all statistical fields. Leave unreported fields as `""`. Only extract what is explicitly stated clearly in paper for the particular experiment being analyzed — do not calculate or try to infer values.
<!-- /mode -->
