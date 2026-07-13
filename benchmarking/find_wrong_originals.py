#!/usr/bin/env python3
"""
Find cases where V6 pipeline identified the WRONG original study for a replication paper.

Specifically:
- A GT entry exists for replication paper X pointing to original study A
- A V6 entry exists for the same replication paper X pointing to a different original study B
- Score(A, B) < 2/3 using the matching logic from evaluate_enhanced.py
"""

import pandas as pd
import numpy as np
from difflib import SequenceMatcher
import re
from pathlib import Path

# ── Matching helpers (copied from evaluate_enhanced.py) ────────────────────

def normalize_string(s):
    if pd.isna(s) or s == '':
        return ''
    return str(s).lower().strip()

def extract_last_names(author_string):
    if pd.isna(author_string) or author_string == '':
        return set()
    author_string = str(author_string)
    authors = [a.strip() for a in author_string.split(';') if a.strip()]
    last_names = set()
    for author in authors:
        if ',' in author:
            parts = author.split(',')
            lastname = normalize_string(parts[0])
            if lastname:
                last_names.add(lastname)
        else:
            parts = author.split()
            if parts:
                lastname = normalize_string(parts[-1])
                if lastname and len(lastname) > 1:
                    last_names.add(lastname)
    return last_names

def authors_match(a1, a2, threshold=0.7):
    last_names_1 = extract_last_names(a1)
    last_names_2 = extract_last_names(a2)
    if not last_names_1 or not last_names_2:
        return False
    intersection = last_names_1 & last_names_2
    union = last_names_1 | last_names_2
    if len(union) == 0:
        return False
    jaccard = len(intersection) / len(union)
    return jaccard >= threshold

def fuzzy_match(s1, s2, threshold=0.75):
    s1_norm = normalize_string(s1)
    s2_norm = normalize_string(s2)
    if not s1_norm or not s2_norm:
        return False
    ratio = SequenceMatcher(None, s1_norm, s2_norm).ratio()
    return ratio >= threshold

def title_sim(s1, s2):
    s1_norm = normalize_string(s1)
    s2_norm = normalize_string(s2)
    if not s1_norm or not s2_norm:
        return 0.0
    return SequenceMatcher(None, s1_norm, s2_norm).ratio()

def year_match(y1, y2):
    try:
        y1_str = str(y1).replace('.0', '').strip()
        y2_str = str(y2).replace('.0', '').strip()
        return y1_str == y2_str
    except:
        return False

def match_score(ext_row, gt_row):
    """Return (score 0-3, title_match, authors_match, year_match)"""
    tm = fuzzy_match(ext_row.get('original_title',''), gt_row.get('original_title',''), threshold=0.75)
    am = authors_match(ext_row.get('original_authors',''), gt_row.get('original_authors',''))
    ym = year_match(ext_row.get('original_year',''), gt_row.get('original_year',''))
    return sum([tm, am, ym]), tm, am, ym

# ── Load data ──────────────────────────────────────────────────────────────

base = Path('/home/dan/Dropbox/AAA_METASCIENCE_OBSERVATORY/claude_code_replications/benchmarking')
gt_path = base / 'ground_truth_enhanced.csv'
v6_path = base / 'ground_truth_data_filtered_PDFs/collated_results_v6_feb2026.csv'

gt = pd.read_csv(gt_path)
v6 = pd.read_csv(v6_path)

# Filter to replication papers only
gt = gt[gt['replication_url'].notna()].copy()
v6 = v6[v6['contains_replications'] == True].copy()

# Normalize URLs
def norm_url(u):
    if pd.isna(u):
        return ''
    return str(u).lower().replace('http://', 'https://').replace('dx.doi.org', 'doi.org').rstrip('/')

gt['rep_url_norm'] = gt['replication_url'].apply(norm_url)
v6['rep_url_norm'] = v6['replication_url'].apply(norm_url)

# DOI → folder path: replace '/' with '--'
def doi_to_folder(doi_str):
    """Convert DOI like '10.1002/cpp.70057' to folder name '10.1002--cpp.70057'"""
    if pd.isna(doi_str) or not doi_str:
        return None
    return str(doi_str).replace('/', '--')

# ── For each GT paper, find V6 entries that don't match any GT entry ───────

# Group GT and V6 by replication paper URL
gt_by_paper = gt.groupby('rep_url_norm')
v6_by_paper = v6.groupby('rep_url_norm')

wrong_original_cases = []   # V6 entry that matched no GT entry, paper has >=1 correct V6 entry
only_wrong_cases = []       # paper has exactly 1 GT entry, V6 got it wrong (no correct match at all)

all_paper_urls = sorted(set(gt['rep_url_norm'].unique()) & set(v6['rep_url_norm'].unique()))

print(f"GT unique papers: {gt['rep_url_norm'].nunique()}")
print(f"V6 unique papers: {v6['rep_url_norm'].nunique()}")
print(f"Papers in both: {len(all_paper_urls)}")
print()

for rep_url in all_paper_urls:
    gt_entries = gt_by_paper.get_group(rep_url)
    v6_entries = v6_by_paper.get_group(rep_url)

    # For each GT entry, find best V6 match
    gt_matched_v6_indices = set()  # V6 row indices that matched a GT entry

    for _, gt_row in gt_entries.iterrows():
        best_score = 0
        best_idx = None
        for v6_idx, v6_row in v6_entries.iterrows():
            sc, tm, am, ym = match_score(v6_row, gt_row)
            if sc > best_score:
                best_score = sc
                best_idx = v6_idx
        if best_score >= 2 and best_idx is not None:
            gt_matched_v6_indices.add(best_idx)

    # Find V6 entries that did NOT match any GT entry
    unmatched_v6 = v6_entries[~v6_entries.index.isin(gt_matched_v6_indices)]

    if len(unmatched_v6) == 0:
        continue  # All V6 entries are accounted for

    # Determine if this paper has at least one correct V6 match
    has_correct_match = len(gt_matched_v6_indices) > 0

    # Now determine which unmatched V6 entries are "wrong original" cases:
    # i.e. the V6 entry is about a different original study than what GT expects.
    # For this we check: is there a GT entry for this paper that this V6 entry
    # is CLOSE to but not quite matching (score 1/3) OR is it completely unrelated?
    # We want all unmatched V6 entries that are non-trivially wrong.

    # Get the replication DOI from the folder name
    # The V6 replication_doi column or we derive from URL
    rep_doi = v6_entries.iloc[0].get('replication_doi', None)
    if pd.isna(rep_doi) or not rep_doi:
        # Derive from URL: https://doi.org/10.XXXX/YYYY -> 10.XXXX/YYYY
        rep_doi = rep_url.replace('https://doi.org/', '').replace('http://doi.org/', '')

    folder_name = doi_to_folder(rep_doi)

    for v6_idx, v6_row in unmatched_v6.iterrows():
        # Find the best-scoring GT entry for this V6 entry (to show what it's closest to)
        best_gt_score = 0
        best_gt_row = None
        for _, gt_row in gt_entries.iterrows():
            sc, tm, am, ym = match_score(v6_row, gt_row)
            if sc > best_gt_score:
                best_gt_score = sc
                best_gt_row = gt_row

        # Compute title similarity between V6 and best GT
        v6_title = str(v6_row.get('original_title', '') or '')
        gt_title = str(best_gt_row['original_title'] if best_gt_row is not None else '')
        tsim = title_sim(v6_title, gt_title)

        case = {
            'rep_url': rep_url,
            'rep_doi': rep_doi,
            'folder_name': folder_name,
            'n_gt_entries': len(gt_entries),
            'n_v6_entries': len(v6_entries),
            'has_correct_v6_match': has_correct_match,
            'v6_original_title': v6_row.get('original_title', ''),
            'v6_original_authors': v6_row.get('original_authors', ''),
            'v6_original_year': v6_row.get('original_year', ''),
            'v6_original_url': v6_row.get('original_url', ''),
            'best_gt_title': best_gt_row['original_title'] if best_gt_row is not None else '',
            'best_gt_authors': best_gt_row['original_authors'] if best_gt_row is not None else '',
            'best_gt_year': best_gt_row['original_year'] if best_gt_row is not None else '',
            'best_gt_url': best_gt_row['original_url'] if best_gt_row is not None else '',
            'best_match_score_to_gt': best_gt_score,
            'title_sim_to_best_gt': round(tsim, 3),
        }

        if has_correct_match:
            wrong_original_cases.append(case)
        else:
            # Paper has no correct V6 match for any GT entry
            only_wrong_cases.append(case)

# ── Focus: clean 1-to-1 cases ─────────────────────────────────────────────
# 1 GT entry, 1 V6 entry, V6 got it wrong (only_wrong_cases where n_gt=1, n_v6=1)

clean_1to1_wrong = [c for c in only_wrong_cases if c['n_gt_entries'] == 1 and c['n_v6_entries'] == 1]
clean_1to1_extra_wrong = [c for c in wrong_original_cases if c['n_gt_entries'] == 1]

# ── Print results ─────────────────────────────────────────────────────────

print("=" * 90)
print("CASES WHERE V6 IDENTIFIED WRONG ORIGINAL STUDY")
print("=" * 90)
print()

print(f"Total V6 unmatched entries (paper also has correct V6 match): {len(wrong_original_cases)}")
print(f"Total V6 unmatched entries (paper has NO correct V6 match): {len(only_wrong_cases)}")
print()
print(f"Clean 1-to-1 wrong cases (1 GT entry, 1 V6 entry, completely wrong): {len(clean_1to1_wrong)}")
print(f"Extra wrong entries on papers with 1 GT entry (but V6 also got one right): {len(clean_1to1_extra_wrong)}")
print()

# ── Section A: Clean 1-to-1 completely wrong ────────────────────────────

print("=" * 90)
print("SECTION A: CLEAN 1-to-1 WRONG CASES")
print("(1 GT entry, 1 V6 entry, V6 pointed to wrong original study)")
print("=" * 90)
print()

for i, c in enumerate(clean_1to1_wrong, 1):
    print(f"Case A{i}: {c['rep_doi']}")
    print(f"  Folder path: {c['folder_name']}")
    print(f"  Replication URL: {c['rep_url']}")
    print()
    print(f"  GT original:")
    print(f"    Title:   {c['best_gt_title']}")
    print(f"    Authors: {c['best_gt_authors']}")
    print(f"    Year:    {c['best_gt_year']}")
    print(f"    URL:     {c['best_gt_url']}")
    print()
    print(f"  V6 extracted (WRONG):")
    print(f"    Title:   {c['v6_original_title']}")
    print(f"    Authors: {c['v6_original_authors']}")
    print(f"    Year:    {c['v6_original_year']}")
    print(f"    URL:     {c['v6_original_url']}")
    print()
    print(f"  Match score to GT: {c['best_match_score_to_gt']}/3  |  Title similarity: {c['title_sim_to_best_gt']:.3f}")
    print()
    print("-" * 90)
    print()

# ── Section B: Papers where V6 had both a correct AND a wrong entry ──────

print()
print("=" * 90)
print("SECTION B: WRONG ENTRIES ON PAPERS WHERE V6 ALSO GOT AT LEAST ONE RIGHT")
print("(V6 processed paper, matched one study correctly, but also extracted a spurious wrong one)")
print("=" * 90)
print()

for i, c in enumerate(wrong_original_cases[:30], 1):
    print(f"Case B{i}: {c['rep_doi']}")
    print(f"  Folder path: {c['folder_name']}")
    print(f"  GT entries: {c['n_gt_entries']}  |  V6 entries: {c['n_v6_entries']}")
    print()
    print(f"  Best matching GT entry (for this V6 entry):")
    print(f"    Title:   {c['best_gt_title']}")
    print(f"    Authors: {c['best_gt_authors']}")
    print(f"    Year:    {c['best_gt_year']}")
    print()
    print(f"  V6 extracted (WRONG/EXTRA):")
    print(f"    Title:   {c['v6_original_title']}")
    print(f"    Authors: {c['v6_original_authors']}")
    print(f"    Year:    {c['v6_original_year']}")
    print(f"    URL:     {c['v6_original_url']}")
    print()
    print(f"  Match score to best GT: {c['best_match_score_to_gt']}/3  |  Title sim: {c['title_sim_to_best_gt']:.3f}")
    print()
    print("-" * 90)
    print()

# ── Section C: Papers with no correct V6 match, multiple entries ─────────

multi_wrong = [c for c in only_wrong_cases if c['n_gt_entries'] == 1 and c['n_v6_entries'] > 1]
print()
print("=" * 90)
print("SECTION C: 1 GT ENTRY, MULTIPLE V6 ENTRIES, ALL WRONG")
print("=" * 90)
print()

for i, c in enumerate(multi_wrong[:20], 1):
    print(f"Case C{i}: {c['rep_doi']}")
    print(f"  Folder: {c['folder_name']}")
    print(f"  V6 entries: {c['n_v6_entries']}")
    print(f"  GT original: {c['best_gt_title'][:70]}")
    print(f"  V6 wrong:    {str(c['v6_original_title'])[:70]}")
    print(f"  Title sim: {c['title_sim_to_best_gt']:.3f}")
    print()

# ── Summary table for Section A (folder paths) ───────────────────────────

print()
print("=" * 90)
print("FOLDER PATHS FOR SECTION A CASES (to re-run pipeline)")
print("=" * 90)
print()
print("Format: 10.XXXX--YYYY  (replace / with -- in DOI)")
print()
for i, c in enumerate(clean_1to1_wrong, 1):
    print(f"  A{i}: {c['folder_name']}")
print()
