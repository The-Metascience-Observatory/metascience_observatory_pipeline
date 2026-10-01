"""Code defaults for the stage-1 search keyword lists.

Data only, so the dashboard and keyword stats can read the defaults without
importing the search module (requests + Biopython). The dashboard edits a
per-list overlay in data/keywords.json (see keywords.py); which list feeds
which source is keywords.API_FANOUT.
"""

# ═══════════════════════════════════════════════════════════════════════════════
# PubMed queries
# ═══════════════════════════════════════════════════════════════════════════════

PUBMED_QUERIES = [
    # Direct replication language
    '"replication study"[Title/Abstract]',
    '"replication of"[Title/Abstract]',
    '"failed to replicate"[Title/Abstract] OR "failure to replicate"[Title/Abstract]',
    '"direct replication"[Title/Abstract] OR "exact replication"[Title/Abstract]',
    '"attempted to replicate"[Title/Abstract] OR "replication attempt"[Title/Abstract]',
    '"we replicated"[Title/Abstract]',

    # Reproducibility language
    '"reproducibility of"[Title/Abstract] AND ("experiment"[Title/Abstract] OR "findings"[Title/Abstract])',
    '"failed to reproduce"[Title/Abstract] OR "could not reproduce"[Title/Abstract]',

    # Registered replication reports
    '"registered replication report"[Title/Abstract]',

    # Multi-site
    '"multi-site replication"[Title/Abstract] OR "multisite replication"[Title/Abstract]',

    # ── New queries (Phase 5a) ──
    '"close replication"[Title/Abstract]',
    '"replicability of"[Title/Abstract]',
    '"replicate the findings"[Title/Abstract]',
    '"reproduce the findings"[Title/Abstract]',
    '"Many Labs"[Title/Abstract]',
    '"replication project"[Title/Abstract]',
    '"conceptual replication"[Title/Abstract]',
    '"replication failure"[Title/Abstract]',
    '"original findings"[Title/Abstract] AND "replicat"[Title/Abstract]',

    # ── Phase 5c: additional phrasings ──
    '"replicated the result"[Title/Abstract] OR "replicated the results"[Title/Abstract]',
    '"replicate the effect"[Title/Abstract] OR "replicated the effect"[Title/Abstract]',
    '"preregistered replication"[Title/Abstract]',
    '"non-replication"[Title/Abstract] OR "non-replications"[Title/Abstract]',
    '"prior study"[Title/Abstract] AND "replicat"[Title/Abstract]',

    # ── Phase 5b: genetics / association-study replication language ──
    '"replication cohort"[Title/Abstract]',
    '"replication sample"[Title/Abstract]',
    '"replication dataset"[Title/Abstract]',
    '"discovery and replication"[Title/Abstract]',
    '"replicated the association"[Title/Abstract] OR "association was replicated"[Title/Abstract]',
    '"independent replication"[Title/Abstract] AND ("SNP"[Title/Abstract] OR "association"[Title/Abstract] OR "cohort"[Title/Abstract] OR "locus"[Title/Abstract])',
    '"two-stage"[Title/Abstract] AND ("GWAS"[Title/Abstract] OR "genome-wide association"[Title/Abstract])',
    '"replicated in" AND "independent cohort"[Title/Abstract]',
    '"meta-analysis"[Title/Abstract] AND "replication cohort"[Title/Abstract]',

    # ── Phase 6: negative/failure phrasings + registered reports ──
    '"did not replicate"[Title/Abstract] OR "does not replicate"[Title/Abstract]',
    '"unable to replicate"[Title/Abstract] OR "were unable to replicate"[Title/Abstract]',
    '"reproduce our findings"[Title/Abstract] OR "reproduce their findings"[Title/Abstract]',
    '"reanalysis of"[Title/Abstract] OR "re-analysis of"[Title/Abstract]',
    '"registered report"[Title/Abstract] AND "replicat"[Title/Abstract]',
    '"adversarial collaboration"[Title/Abstract]',
    # ── Phase 6: ML / CS reproducibility ──
    '"reproducibility study"[Title/Abstract] OR "reproducibility challenge"[Title/Abstract]',
]


# ═══════════════════════════════════════════════════════════════════════════════
# OpenAlex queries
# ═══════════════════════════════════════════════════════════════════════════════

# Two explicit blocks (replacing the old `[:15]` slice that fed OSF/S2). The
# genetics/GWAS block is biomedical-only and generates noise on the social-sci /
# CS-biased repositories (OSF, Semantic Scholar), so those sources get only the
# CORE block. OpenAlex + Crossref search both blocks.
REPLICATION_CORE = [
    "replication study",
    "failed to replicate",
    "reproducibility of",
    "replication attempt",
    "registered replication report",
    "direct replication",
    # ── Phase 5a ──
    "close replication",
    "conceptual replication",
    "replication failure",
    "replicability",
    "Many Labs",
    # ── Phase 5c: additional phrasings ──
    "replicated the result",
    "replicate the effect",
    "preregistered replication",
    "non-replication",

    # ── Phase 6: negative/failure phrasings (high recall, classifier filters) ──
    "did not replicate",
    "does not replicate",
    "unable to replicate",
    "reproduce our findings",
    "reanalysis",
    "Registered Report",           # broader than "registered replication report"
    "adversarial collaboration",

    # ── Phase 6: ML / CS reproducibility (retrieves ~100% — mostly OA/arXiv) ──
    "reproducibility study",
    "we reproduce",
    "reproducibility challenge",

    # ── Phase 6: ecology / poli-sci / sociology (retrieve 62–99%) ──
    "replication data",
    "reproducibility in ecology",

    # ── Phase 6: economics — NOTE only ~30% of econ replications are
    #    retrievable without RePEc/SSRN/NBER, so keep this minimal ──
    "computational reproducibility",
    "replication in economics",
]

GENETICS_QUERIES = [
    # ── Phase 5b: genetics / association-study replication language ──
    "replication cohort",
    "replication sample",
    "discovery and replication",
    "replicated the association",
    "association was replicated",
    "independent replication cohort",
    "two-stage genome-wide association",
]

# ═══════════════════════════════════════════════════════════════════════════════
# Europe PMC queries (supports full-text search)
# ═══════════════════════════════════════════════════════════════════════════════

EUROPEPMC_QUERIES = [
    '(BODY:"replication of" AND BODY:"original study") AND SRC:MED',
    '(BODY:"we replicated" OR BODY:"we attempted to replicate") AND SRC:MED',
    '(BODY:"failed to replicate" OR BODY:"failure to replicate") AND SRC:MED',
    '(TITLE:"replication study" OR TITLE:"replication of") AND SRC:MED',
    '(TITLE:"registered replication") AND SRC:MED',
    # ── New queries (Phase 5a) ──
    '(BODY:"close replication" OR BODY:"exact replication") AND SRC:MED',
    '(BODY:"replicate the findings" OR BODY:"reproduce the findings") AND SRC:MED',
    '(BODY:"Many Labs" OR BODY:"Registered Replication Report") AND SRC:MED',
    '(BODY:"replication attempt" AND BODY:"original study") AND SRC:MED',
    '(TITLE:"conceptual replication" OR TITLE:"close replication") AND SRC:MED',
    '(TITLE:"replication failure" OR TITLE:"replicability") AND SRC:MED',

    # ── Phase 5b: genetics / association-study replication language ──
    # EuropePMC BODY: search is especially powerful here because "replication cohort"
    # often appears in Methods/Results rather than Abstract.
    '(BODY:"replication cohort" OR BODY:"replication sample" OR BODY:"replication dataset") AND SRC:MED',
    '(BODY:"discovery and replication" OR (BODY:"discovery cohort" AND BODY:"replication cohort")) AND SRC:MED',
    '(BODY:"replicated the association" OR BODY:"association was replicated") AND SRC:MED',
    '(BODY:"independent replication" AND (BODY:"SNP" OR BODY:"association" OR BODY:"locus")) AND SRC:MED',
    '(TITLE:"replication cohort" OR TITLE:"replication sample") AND SRC:MED',
    '(BODY:"two-stage" AND BODY:"genome-wide association") AND SRC:MED',

    # ── Phase 5c: additional phrasings ──
    '(BODY:"replicated the result" OR BODY:"replicated the results") AND SRC:MED',
    '(BODY:"replicate the effect" OR BODY:"replicated the effect") AND SRC:MED',
    '(TITLE:"preregistered replication" OR BODY:"preregistered replication") AND SRC:MED',
    '(BODY:"non-replication" OR BODY:"non-replications") AND SRC:MED',
    '(BODY:"prior study" AND BODY:"replicat") AND SRC:MED',

    # ── Phase 6: negative phrasings + registered reports + reanalysis ──
    '(BODY:"did not replicate" OR BODY:"does not replicate" OR BODY:"were unable to replicate") AND SRC:MED',
    '(BODY:"reproduce our findings" OR BODY:"reproduce their findings") AND SRC:MED',
    '(BODY:"reanalysis of" OR BODY:"re-analysis of") AND BODY:"replicat" AND SRC:MED',
    '(TITLE:"registered report" AND BODY:"replicat") AND SRC:MED',
    '(BODY:"adversarial collaboration") AND SRC:MED',
    # ── Phase 6: ML / CS reproducibility ──
    '(TITLE:"reproducibility study" OR BODY:"reproducibility challenge") AND SRC:MED',

    # ── Preprints (bioRxiv / medRxiv / etc. indexed by Europe PMC as SRC:PPR) ──
    '(TITLE:"replication" OR ABSTRACT:"failed to replicate") AND SRC:PPR',
    '(TITLE:"replication study" OR TITLE:"replication of") AND SRC:PPR',
    '(TITLE:"reproducibility" OR ABSTRACT:"did not replicate") AND SRC:PPR',
]


DEFAULTS = {
    "replication_core": REPLICATION_CORE,
    "genetics_queries": GENETICS_QUERIES,
    "pubmed_queries": PUBMED_QUERIES,
    "europepmc_queries": EUROPEPMC_QUERIES,
}
