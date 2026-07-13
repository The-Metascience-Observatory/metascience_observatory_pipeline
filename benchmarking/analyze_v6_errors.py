#!/usr/bin/env python3
"""
Identify and categorize serious errors in V6 extraction for manual review.

Error categories:
1. Wrong original study identified (wrong title/authors/year)
2. Wrong result classification (especially success ↔ failure)
3. Missing critical replication entries
4. Incorrect bibliographic data
"""

import pandas as pd
from difflib import SequenceMatcher
import re

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

def year_match(y1, y2):
    try:
        y1_str = str(y1).replace('.0', '').strip()
        y2_str = str(y2).replace('.0', '').strip()
        return y1_str == y2_str
    except:
        return False

# Load datasets
gt = pd.read_csv('ground_truth_enhanced.csv')
v6 = pd.read_csv('ground_truth_data_filtered_PDFs/collated_results_v6_feb2026.csv')

gt = gt[gt['replication_url'].notna()].copy()
v6 = v6[v6['contains_replications'] == True].copy()

gt['replication_url_norm'] = gt['replication_url'].str.lower().str.replace('http://', 'https://').str.replace('dx.doi.org', 'doi.org')
v6['replication_url_norm'] = v6['replication_url'].str.lower().str.replace('http://', 'https://').str.replace('dx.doi.org', 'doi.org')

errors = {
    'wrong_study': [],
    'wrong_result_critical': [],
    'wrong_result_minor': [],
    'missing_entry': [],
    'wrong_bibliographic': [],
}

# Analyze each GT entry
for idx, gt_row in gt.iterrows():
    v6_entries = v6[v6['replication_url_norm'] == gt_row['replication_url_norm']]

    if len(v6_entries) == 0:
        errors['missing_entry'].append({
            'replication_doi': gt_row['replication_url'].split('/')[-1],
            'replication_url': gt_row['replication_url'],
            'gt_original_title': gt_row['original_title'],
            'gt_original_authors': gt_row['original_authors'][:50],
            'gt_result': gt_row['result'],
            'error': 'Paper completely missing from V6',
            'severity': 'HIGH'
        })
        continue

    # Find best match
    best_match = None
    best_score = 0

    for _, v6_row in v6_entries.iterrows():
        title_match = fuzzy_match(v6_row['original_title'], gt_row['original_title'], 0.75)
        authors_match_result = authors_match(v6_row['original_authors'], gt_row['original_authors'])
        year_match_result = year_match(v6_row['original_year'], gt_row['original_year'])

        score = sum([title_match, authors_match_result, year_match_result])

        if score > best_score:
            best_score = score
            best_match = v6_row

    if best_score < 2:
        # Wrong study identified
        errors['wrong_study'].append({
            'replication_doi': gt_row['replication_url'].split('/')[-1],
            'replication_url': gt_row['replication_url'],
            'gt_original_title': gt_row['original_title'][:70],
            'gt_original_authors': str(gt_row['original_authors'])[:50],
            'gt_original_year': gt_row['original_year'],
            'v6_original_title': best_match['original_title'][:70] if best_match is not None else 'N/A',
            'v6_original_authors': str(best_match['original_authors'])[:50] if best_match is not None else 'N/A',
            'v6_original_year': best_match['original_year'] if best_match is not None else 'N/A',
            'match_score': best_score,
            'v6_entries_available': len(v6_entries),
            'error': f'No matching original study found (score {best_score}/3)',
            'severity': 'HIGH'
        })
    else:
        # Study matched, check other fields
        title_match = fuzzy_match(best_match['original_title'], gt_row['original_title'], 0.75)
        authors_match_result = authors_match(best_match['original_authors'], gt_row['original_authors'])
        year_match_result = year_match(best_match['original_year'], gt_row['original_year'])

        # Check bibliographic errors
        if not (title_match and authors_match_result and year_match_result):
            errors['wrong_bibliographic'].append({
                'replication_doi': gt_row['replication_url'].split('/')[-1],
                'gt_original_title': gt_row['original_title'][:60],
                'v6_original_title': best_match['original_title'][:60],
                'title_match': title_match,
                'authors_match': authors_match_result,
                'year_match': year_match_result,
                'error': 'Bibliographic mismatch on matched study',
                'severity': 'MEDIUM'
            })

        # Check result classification
        gt_result = normalize_string(gt_row['result'])
        v6_result = normalize_string(best_match['result'])

        if gt_result != v6_result:
            # Critical: success ↔ failure mismatch
            if (gt_result == 'success' and v6_result == 'failure') or \
               (gt_result == 'failure' and v6_result == 'success'):
                errors['wrong_result_critical'].append({
                    'replication_doi': gt_row['replication_url'].split('/')[-1],
                    'original_study': gt_row['original_title'][:60],
                    'gt_result': gt_result,
                    'v6_result': v6_result,
                    'error': f'Critical result mismatch: {gt_result} → {v6_result}',
                    'severity': 'HIGH'
                })
            else:
                # Minor mismatches (inconclusive, reversal, etc.)
                errors['wrong_result_minor'].append({
                    'replication_doi': gt_row['replication_url'].split('/')[-1],
                    'original_study': gt_row['original_title'][:60],
                    'gt_result': gt_result,
                    'v6_result': v6_result,
                    'error': f'Result mismatch: {gt_result} → {v6_result}',
                    'severity': 'MEDIUM'
                })

# Generate report
print("=" * 90)
print("V6 SERIOUS ERRORS ANALYSIS")
print("=" * 90)
print()

print("ERROR SUMMARY:")
print("-" * 90)
print(f"Missing entries (paper not in V6):     {len(errors['missing_entry']):3d}")
print(f"Wrong original study identified:        {len(errors['wrong_study']):3d}")
print(f"Critical result mismatches (S↔F):       {len(errors['wrong_result_critical']):3d}")
print(f"Minor result mismatches:                {len(errors['wrong_result_minor']):3d}")
print(f"Wrong bibliographic data (matched):     {len(errors['wrong_bibliographic']):3d}")
print()

# Detailed errors
print("=" * 90)
print("CATEGORY 1: MISSING ENTRIES (HIGH SEVERITY)")
print("=" * 90)
print()
for i, err in enumerate(errors['missing_entry'][:10], 1):
    print(f"{i}. {err['replication_doi']}")
    print(f"   GT Original: {err['gt_original_title']}")
    print(f"   GT Authors:  {err['gt_original_authors']}")
    print(f"   GT Result:   {err['gt_result']}")
    print(f"   ERROR: {err['error']}")
    print()

print("=" * 90)
print("CATEGORY 2: WRONG ORIGINAL STUDY IDENTIFIED (HIGH SEVERITY)")
print("=" * 90)
print()
for i, err in enumerate(errors['wrong_study'][:15], 1):
    print(f"{i}. {err['replication_doi']}")
    print(f"   GT Original: {err['gt_original_title']}")
    print(f"   GT Authors:  {err['gt_original_authors']}")
    print(f"   GT Year:     {err['gt_original_year']}")
    print()
    print(f"   V6 Original: {err['v6_original_title']}")
    print(f"   V6 Authors:  {err['v6_original_authors']}")
    print(f"   V6 Year:     {err['v6_original_year']}")
    print(f"   Match score: {err['match_score']}/3")
    print(f"   V6 has {err['v6_entries_available']} entries for this paper")
    print()

print("=" * 90)
print("CATEGORY 3: CRITICAL RESULT MISMATCHES - SUCCESS ↔ FAILURE (HIGH SEVERITY)")
print("=" * 90)
print()
for i, err in enumerate(errors['wrong_result_critical'][:20], 1):
    print(f"{i}. {err['replication_doi']}")
    print(f"   Original Study: {err['original_study']}")
    print(f"   GT Result:  {err['gt_result']}")
    print(f"   V6 Result:  {err['v6_result']}")
    print()

print("=" * 90)
print("CATEGORY 4: MINOR RESULT MISMATCHES (MEDIUM SEVERITY)")
print("=" * 90)
print()
for i, err in enumerate(errors['wrong_result_minor'][:10], 1):
    print(f"{i}. {err['replication_doi']}")
    print(f"   Original Study: {err['original_study']}")
    print(f"   GT Result:  {err['gt_result']}")
    print(f"   V6 Result:  {err['v6_result']}")
    print()

print("=" * 90)
print("CATEGORY 5: WRONG BIBLIOGRAPHIC DATA (MEDIUM SEVERITY)")
print("=" * 90)
print()
for i, err in enumerate(errors['wrong_bibliographic'][:10], 1):
    print(f"{i}. {err['replication_doi']}")
    print(f"   GT: {err['gt_original_title']}")
    print(f"   V6: {err['v6_original_title']}")
    print(f"   Title match:   {err['title_match']}")
    print(f"   Authors match: {err['authors_match']}")
    print(f"   Year match:    {err['year_match']}")
    print()

print("=" * 90)

# Save detailed error data
import json
with open('v6_errors_detailed.json', 'w') as f:
    json.dump(errors, f, indent=2, default=str)

print()
print("Detailed error data saved to: v6_errors_detailed.json")
