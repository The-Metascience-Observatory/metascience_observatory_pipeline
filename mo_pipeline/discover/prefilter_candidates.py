"""
Step 2b: Keyword pre-filter to remove obvious non-replication papers.

Strategy: REQUIRE a positive signal rather than just excluding negatives.
A paper is kept only if:
  1. It has a strong positive signal for study replication, OR
  2. It mentions "replicat" or "reproduc" in title/abstract AND is not
     clearly about biological/molecular replication or technical replicates.

Policy note: Strong positive matches at Step 2 take precedence over the
exclusion patterns at Steps 3-5 — a legitimate replication study about
viruses isn't dropped just because "viral replication" also appears in its
abstract. The ordering inside classify_row() is intentional.

This is a cheap heuristic step to reduce the number of candidates sent
to the LLM classifier.

Input:  data/candidates_dedup.csv
Output: data/candidates_filtered.csv

Usage:
    python prefilter_candidates.py
    python prefilter_candidates.py --verbose    # log which pattern matched each keep
"""

import argparse
import csv
import re
import sys
import time

from mo_pipeline.config import DATA_DIR, CANDIDATES_DEDUP_CSV, CANDIDATES_FILTERED_CSV

csv.field_size_limit(sys.maxsize)

FIELDNAMES = [
    "pmid", "doi", "title", "abstract", "authors",
    "journal", "year", "source_api", "source_query", "match_count",
]

# ── Strong positive signals ──────────────────────────────────────────────────
# Papers matching these are very likely about study replication → auto-keep
STRONG_POSITIVE_PATTERNS = [
    r"\breplicat\w*\s+stud(?:y|ies)\b",
    r"\breplicat\w*\s+(?:the|a|an|this|these|those|prior|previous|original|earlier)\s+(?:stud|finding|result|experiment|effect|observation)",

    # ── Phase 3 (recall audit): title-form replications ──────────────────────
    # Derived from the 102 FLoRa ground-truth papers this filter was measured
    # dropping (see benchmarking/recall_harness.py). The patterns above all
    # require "replication" to be FOLLOWED by study/finding/result/etc, which
    # misses the commonest title form in the literature: "A Replication of X",
    # "A Replication and Extension of X", "Replication of <NamedThing>".
    # Verified against ground truth rather than guessed.
    # NOTE: a bare `replication of` cannot go here. Strong positives take
    # precedence over the exclusion steps (see classify_row), so it would
    # bypass the bio/viral guard and admit ~11k rows like "Replication of
    # SARS-CoV-2" / "Filovirus Replication in Vero E6 Cells". Measured. Instead
    # it is handled as a *weak* positive below, which still runs the exclusions.
    r"\breplications?\s+and\s+extensions?\b",
    r"\breplication\s+and\s+(?:validation|cross[\s-]validation|adaptation)\b",
    r"\breplicat\w*\s+report\b",
    r"\breplicat\w*\s+and\s+adapt\w*\b",
    r"\bfailures?\s+to\s+replicat",
    r"\bnon[\s-]replication\b",
    r"\b(?:constructive|approximate|conceptual|systematic|large[\s-]scale|cross[\s-]cultural|cross[\s-]cent(?:er|re)|transethnic|preregistered|pre[\s-]registered|independent|successful|partial|attempted)\s+replicat",
    r"\breplicat\w+\s+and\s+extend\w*\b",
    r"\breproduc\w+\s+the\s+results?\b",
    r"\bfailed?\s+to\s+replicat",
    r"\bfailure\s+to\s+replicat",
    r"\bdirect\s+replicat",
    r"\bexact\s+replicat",
    r"\bclose\s+replicat",
    r"\bconceptual\s+replicat",
    r"\bsystematic\s+replicat",
    r"\breplicat\w*\s+attempt",
    r"\battempt\w*\s+to\s+replicat",
    r"\bregistered\s+replicat",
    r"\bmulti[\s-]?site\s+replicat",
    r"\bmultisite\s+replicat",
    r"\bwe\s+replicat\w+\b",
    r"\bfailed?\s+to\s+reproduc",
    r"\bcould\s+not\s+reproduc",
    r"\boriginal\s+(?:study|finding|result|experiment|author)",
    r"\breplicat\w+\s+(?:across|in)\s+(?:a\s+)?(?:new|different|independent|another)\b",
    r"\breplicat\w*\s+crisis",
    r"\breproducibility\s+crisis",
    r"\breproducibility\s+project",

    # ── Phase 5b: genetics / association-study replication language ──
    # These phrases are characteristic of GWAS / genetic-association replication
    # studies (typically "close extension" by MO definition — same effect tested
    # in different ancestry / cohort / population). Previously excluded at the
    # "weak_replication_word" step; now admitted as strong positives.
    r"\breplication\s+cohort",
    r"\breplication\s+sample",
    r"\breplication\s+dataset",
    r"\bdiscovery\s+and\s+replication",
    r"\breplicat\w*\s+the\s+association",
    r"\bassociation\s+was\s+replicat",
    r"\breplicat\w*\s+(?:in|across)\s+(?:an?\s+)?independent\s+(?:cohort|sample|population|dataset)",
    r"\btwo[\s-]stage\s+(?:gwas|genome[\s-]wide|association|meta[\s-]analysis)",
    r"\bindependent\s+replication\b",

    # Additional phrasings: validation language, stage-2 GWAS design, ancestry-specific replications.
    # These use "validation"/"confirmed"/ancestry terms instead of "replication" and were previously
    # bucketed into weak_replication_word.
    r"\bstage\s+(?:2|II|two)\b.*\b(?:gwas|genome[\s-]wide|association)",
    r"\bvalidat(?:ion|ed)\s+in\s+(?:an?\s+)?independent\s+(?:cohort|sample|dataset|population)",
    r"\bconfirmatory\s+(?:study|analysis|sample|cohort)",
    r"\bconfirmed\s+in\s+(?:an?\s+)?(?:independent\s+)?(?:cohort|sample|dataset|population)",
    r"\breplicat\w+\s+in\s+(?:a\s+)?(?:japanese|chinese|african|european|asian|korean|han|caucasian|hispanic|latino|finnish|icelandic|ashkenazi|mexican|brazilian|indian)\s+(?:cohort|sample|population)",
]


# ── Exclusion patterns: biological/molecular replication ─────────────────────
BIO_REPLICATION_PATTERNS = [
    r"\bdna\s+replicat",
    r"\bviral\s+replicat",
    r"\bvirus\s+replicat",
    r"\breplicat\w*\s+fork",
    r"\breplicat\w*\s+origin",
    r"\borigin\s+of\s+replicat",
    r"\breplicat\w*\s+machinery",
    r"\breplicat\w*\s+complex",
    r"\breplicat\w*\s+initiat",
    r"\breplicat\w*\s+terminat",
    r"\breplicat\w*\s+stress",
    r"\bgenome\s+replicat",
    r"\bchromosom\w*\s+replicat",
    r"\breplication\s+protein",
    r"\breplication\s+factor",
    r"\breplication\s+enzyme",
    r"\breplicase\b",
    r"\breplicat\w*\s+cycle",
    r"\breplicat\w*\s+intermediat",
    r"\breplicat\w*\s+bubble",
    r"\bplasmid\s+replicat",
    r"\breplicat\w*\s+helicase",
    r"\breplicat\w*\s+primase",
    r"\breplicat\w*\s+polymerase",
    r"\brna\s+replicat",
    r"\bself[\s-]replicat",
    r"\breplicat\w*\s+niche",
    r"\breplicat\w*\s+competent",
    r"\breplication[\s-]deficient",
    r"\breplication[\s-]defective",
    r"\bnon[\s-]replicat\w+\s+(vector|virus|particle)",
    r"\breplicat\w*\s+kinetics",
    r"\btranscription\s+and\s+replicat",
    r"\bhiv\b.*\breplicat",
    r"\bhepatitis\b.*\breplicat",
    r"\binfluenza\b.*\breplicat",
    r"\bsars\b.*\breplicat",
    r"\bcovid\b.*\breplicat",
    # Reverse direction: these only matched virus-before-"replicat", so
    # "Replication of SARS-CoV-2 in bronchial epithelium" slipped past into the
    # weak-positive tier. Pathogen names are never study-replication targets.
    r"\breplicat\w*\s+(?:of|in)\s+(?:the\s+)?(?:hiv|sars|covid|influenza|hepatitis|dengue|zika|ebola|filovirus|rotavirus|norovirus|coronaviruse?s?)\b",
    r"\breplicat\w*\s+of\s+\w*\s*virus\w*\b",
    r"\bcell\s+replicat",
    r"\bcellular\s+replicat",
]

# ── Exclusion patterns: measurement reproducibility (not study replication) ──
MEASUREMENT_REPRO_PATTERNS = [
    r"\breproducib\w+\s+(?:of\s+)?(?:the\s+)?(?:measurement|method|technique|assay|test|imaging|scan)",
    r"\b(?:measurement|method|technique|assay|test|imaging)\s+reproducib",
    r"\binter[\s-]?(?:observer|rater|reader|examiner)\s+reproducib",
    r"\bintra[\s-]?(?:observer|rater|reader|examiner)\s+reproducib",
    r"\btest[\s-]retest\s+reproducib",
    r"\breproducib\w+\s+(?:and|or)\s+repeatab",
    r"\brepeatab\w+\s+(?:and|or)\s+reproducib",
]

# ── Exclusion patterns: statistical/technical replicates ─────────────────────
STAT_REPLICATE_PATTERNS = [
    r"\bbiological\s+replicat",
    r"\btechnical\s+replicat",
    r"\b(?:three|3|two|2|four|4|five|5|six|6)\s+(?:biological|technical|independent)\s+replicat",
    r"\breplicat\w*\s+experiment\w*\s+were\s+performed",
    r"\bperformed\s+in\s+(?:triplicate|duplicate|quadruplicate)",
    r"\bn\s*=\s*\d+\s+replicat",
    r"\breplicat\w*\s+per\s+(?:condition|group|sample|well|plate)",
    r"\bindependent\s+replicat\w*\s+(?:of|per|for)\b",
    r"\breplicat\w*\s+wells?\b",
    r"\breplicat\w*\s+samples?\b",
    r"\breplicat\w*\s+measurements?\b",
]

# ── Weak positive signals ────────────────────────────────────────────────────
# Study-replication phrasings that are genuinely predictive but too generic to
# override the exclusion steps. Checked only AFTER bio/measurement/stat
# exclusions have run (see classify_row step 6), so "Replication of SARS-CoV-2"
# is still dropped while "Replication of the Asch effect" is kept.
WEAK_POSITIVE_PATTERNS = [
    r"\breplication\s+of\b",
    r"\breplicat\w*\s+in\s+(?:a\s+)?(?:new|second|different)\s+(?:sample|context|setting|population)",
]

# Compile all patterns
_strong_pos_re = [re.compile(p, re.IGNORECASE) for p in STRONG_POSITIVE_PATTERNS]
_weak_pos_re = [re.compile(p, re.IGNORECASE) for p in WEAK_POSITIVE_PATTERNS]
_bio_re = [re.compile(p, re.IGNORECASE) for p in BIO_REPLICATION_PATTERNS]
_meas_re = [re.compile(p, re.IGNORECASE) for p in MEASUREMENT_REPRO_PATTERNS]
_stat_re = [re.compile(p, re.IGNORECASE) for p in STAT_REPLICATE_PATTERNS]

# Simple word-level check
_has_replication_word = re.compile(r"\breplicat|\breproducib", re.IGNORECASE)


def find_strong_positive(text):
    """Return the first strong-positive pattern string that matches, or None."""
    for pattern, compiled in zip(STRONG_POSITIVE_PATTERNS, _strong_pos_re):
        if compiled.search(text):
            return pattern
    return None


def find_weak_positive(text):
    """Return the first weak-positive pattern string that matches, or None."""
    for pattern, compiled in zip(WEAK_POSITIVE_PATTERNS, _weak_pos_re):
        if compiled.search(text):
            return pattern
    return None


def has_bio_exclusion(text):
    return any(p.search(text) for p in _bio_re)


def has_measurement_repro(text):
    return any(p.search(text) for p in _meas_re)


def has_stat_exclusion(text):
    return any(p.search(text) for p in _stat_re)


# Every reason string classify_row() returns with keep=True. Kept beside the
# function so the two cannot drift (the summary printer reads this).
KEEP_REASONS = frozenset({"strong_positive", "weak_positive", "title_has_replication"})


def classify_row(row):
    """Return (keep: bool, reason: str, detail: str) for a candidate row.

    `detail` is the matching strong-positive pattern string when keep=True and
    reason='strong_positive', otherwise "". Used by --verbose to report which
    pattern triggered each keep decision (for pattern-frequency tuning).
    """
    title = row.get("title", "")
    abstract = row.get("abstract", "")
    text = f"{title} {abstract}"

    # Step 1: Must contain replicat* or reproducib* somewhere
    if not _has_replication_word.search(text):
        return False, "no_replication_word", ""

    # Step 2: Strong positive signal → always keep (takes precedence over Steps 3-5)
    matched = find_strong_positive(text)
    if matched is not None:
        return True, "strong_positive", matched

    # Step 3: Exclude biological/molecular replication
    if has_bio_exclusion(text):
        return False, "bio_replication", ""

    # Step 4: Exclude pure measurement reproducibility
    if has_measurement_repro(text):
        return False, "measurement_repro", ""

    # Step 5: Check if only stat/tech replicates in abstract. Strategy: strip out
    # every match of the stat-replicate patterns from the abstract, then see if any
    # replication-word occurrence still remains. If the remaining text still
    # mentions replication/reproducibility, it's not "only stat usage" and we
    # continue; otherwise we drop (or keep if the title itself mentions replication).
    if has_stat_exclusion(abstract):
        cleaned = abstract.lower()
        for p in _stat_re:
            cleaned = p.sub("", cleaned)
        if "replicat" not in cleaned and "reproducib" not in cleaned:
            if _has_replication_word.search(title):
                return True, "title_has_replication", ""
            return False, "stat_replicates_only", ""

    # Step 6: Weak positives — study-replication phrasings that are too generic to
    # bypass the exclusions above, but are strong enough once those have run.
    # "Replication of <X>" is the commonest replication title form in the
    # literature, yet a bare match also catches "Replication of SARS-CoV-2", so it
    # is only trusted *here*, downstream of the bio/measurement guards.
    matched_weak = find_weak_positive(text)
    if matched_weak is not None:
        return True, "weak_positive", matched_weak

    # Step 7: Has replication word but no signal at all → exclude
    # (too many false positives in this bucket — measurement reproducibility in
    # medical imaging, methodological reviews, etc.)
    return False, "weak_replication_word", ""


def main():
    parser = argparse.ArgumentParser(description="Keyword pre-filter for replication candidates.")
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Log the matching strong-positive pattern for each kept row. "
             "Useful for pattern-frequency tuning — pipe the output through "
             "`grep 'KEEP' | awk -F'matched /' '{print $2}' | sort | uniq -c | sort -rn`.",
    )
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with open(CANDIDATES_DEDUP_CSV, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        # Preserve whatever columns upstream produced, so new columns in
        # candidates_dedup.csv flow through to candidates_filtered.csv automatically.
        fieldnames = reader.fieldnames or FIELDNAMES

    print(f"Loaded {len(rows)} deduplicated candidates", flush=True)

    kept = []
    reasons = {}
    start = time.time()
    last_heartbeat = start

    for i, row in enumerate(rows, start=1):
        keep, reason, detail = classify_row(row)
        reasons[reason] = reasons.get(reason, 0) + 1
        if keep:
            kept.append(row)
            if args.verbose and detail:
                ident = row.get("pmid") or row.get("doi") or "?"
                print(f"  KEEP {ident}: matched /{detail}/", flush=True)

        now = time.time()
        if now - last_heartbeat > 2.0:
            pct = 100 * i / len(rows)
            print(f"  ... processed {i:,}/{len(rows):,} ({pct:.0f}%), "
                  f"{len(kept):,} kept so far ({now - start:.0f}s)", flush=True)
            last_heartbeat = now

    print("\nFilter results:", flush=True)
    for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
        tag = "KEEP" if reason in KEEP_REASONS else "EXCLUDE"
        print(f"  {tag:7s} {reason}: {count}", flush=True)

    print(f"\nTotal kept for LLM classification: {len(kept)}", flush=True)
    print(f"Total excluded: {len(rows) - len(kept)}", flush=True)

    with open(CANDIDATES_FILTERED_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in kept:
            writer.writerow(row)

    print(f"Wrote {len(kept)} rows to {CANDIDATES_FILTERED_CSV}", flush=True)


if __name__ == "__main__":
    main()
