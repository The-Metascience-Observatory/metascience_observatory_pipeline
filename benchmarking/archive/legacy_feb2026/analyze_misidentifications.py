#!/usr/bin/env python3
"""
Analyze V6 extraction entries on ground-truth replication papers that don't match
any GT entry. Separate fuzzy matching failures from genuine misidentifications.
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

def authors_jaccard(a1, a2):
    last_names_1 = extract_last_names(a1)
    last_names_2 = extract_last_names(a2)
    if not last_names_1 or not last_names_2:
        return 0.0
    intersection = last_names_1 & last_names_2
    union = last_names_1 | last_names_2
    if len(union) == 0:
        return 0.0
    return len(intersection) / len(union)

def title_similarity(s1, s2):
    s1_norm = normalize_string(s1)
    s2_norm = normalize_string(s2)
    if not s1_norm or not s2_norm:
        return 0.0
    return SequenceMatcher(None, s1_norm, s2_norm).ratio()

def is_title_prefix(s1, s2):
    """Check if one title is a prefix/truncation of the other (same paper, just shortened)."""
    s1_norm = normalize_string(s1)
    s2_norm = normalize_string(s2)
    if not s1_norm or not s2_norm:
        return False
    short, long = (s1_norm, s2_norm) if len(s1_norm) <= len(s2_norm) else (s2_norm, s1_norm)
    # Check if short title is a prefix of long title (with colon/subtitle)
    if long.startswith(short):
        return True
    # Also check if short title + ":" starts the long title
    if long.startswith(short.rstrip(':') + ':'):
        return True
    return False

def year_match(y1, y2):
    try:
        y1_str = str(y1).replace('.0', '').strip()
        y2_str = str(y2).replace('.0', '').strip()
        return y1_str == y2_str
    except:
        return False

def match_score(ext_row, gt_row):
    """Calculate match score (0-3) between an extracted entry and a GT entry."""
    t_sim = title_similarity(ext_row['original_title'], gt_row['original_title'])
    t_match = t_sim >= 0.75
    a_jacc = authors_jaccard(ext_row['original_authors'], gt_row['original_authors'])
    a_match = a_jacc >= 0.7
    y_match = year_match(ext_row['original_year'], gt_row['original_year'])
    score = sum([t_match, a_match, y_match])
    return score, t_sim, a_jacc, y_match


def main():
    gt = pd.read_csv('ground_truth_enhanced.csv')
    v6 = pd.read_csv('ground_truth_data_filtered_PDFs/collated_results_v6_feb2026.csv')

    # Filter
    gt = gt[gt['replication_url'].notna()].copy()
    v6 = v6[v6['contains_replications'] == True].copy()

    # Normalize URLs
    gt['replication_url_norm'] = gt['replication_url'].str.lower().str.replace('http://', 'https://').str.replace('dx.doi.org', 'doi.org')
    v6['replication_url_norm'] = v6['replication_url'].str.lower().str.replace('http://', 'https://').str.replace('dx.doi.org', 'doi.org')

    # For each V6 entry, check if it matches any GT entry for the same replication paper
    unmatched_v6 = []

    for idx, v6_row in v6.iterrows():
        rep_url = v6_row['replication_url_norm']
        gt_entries = gt[gt['replication_url_norm'] == rep_url]

        if len(gt_entries) == 0:
            # This replication paper is not in GT at all - skip
            continue

        # Check if this V6 entry matches any GT entry
        best_score = 0
        best_gt_row = None
        best_t_sim = 0
        best_a_jacc = 0
        best_y_match = False

        for _, gt_row in gt_entries.iterrows():
            score, t_sim, a_jacc, y_match = match_score(v6_row, gt_row)
            if score > best_score or (score == best_score and t_sim > best_t_sim):
                best_score = score
                best_gt_row = gt_row
                best_t_sim = t_sim
                best_a_jacc = a_jacc
                best_y_match = y_match

        if best_score < 2:
            # This V6 entry didn't match any GT entry
            unmatched_v6.append({
                'v6_idx': idx,
                'replication_url': v6_row['replication_url'],
                'v6_original_title': str(v6_row['original_title']) if pd.notna(v6_row['original_title']) else '',
                'v6_original_authors': str(v6_row['original_authors']) if pd.notna(v6_row['original_authors']) else '',
                'v6_original_year': str(v6_row['original_year']).replace('.0', '') if pd.notna(v6_row['original_year']) else '',
                'v6_result': str(v6_row['result']) if pd.notna(v6_row['result']) else '',
                'gt_original_title': str(best_gt_row['original_title']) if best_gt_row is not None and pd.notna(best_gt_row['original_title']) else '',
                'gt_original_authors': str(best_gt_row['original_authors']) if best_gt_row is not None and pd.notna(best_gt_row['original_authors']) else '',
                'gt_original_year': str(best_gt_row['original_year']).replace('.0', '') if best_gt_row is not None and pd.notna(best_gt_row['original_year']) else '',
                'gt_result': str(best_gt_row['result']) if best_gt_row is not None and pd.notna(best_gt_row['result']) else '',
                'best_score': best_score,
                'title_sim': best_t_sim,
                'author_jaccard': best_a_jacc,
                'year_match': best_y_match,
                'gt_count': len(gt_entries),
            })

    print(f"Total V6 entries on GT replication papers: {len(v6[v6['replication_url_norm'].isin(gt['replication_url_norm'])])}")
    print(f"Unmatched V6 entries (score < 2): {len(unmatched_v6)}")
    print()

    # Separate into fuzzy matching failures and genuine misidentifications
    # A fuzzy failure is: title_sim >= 0.5, OR one title is a prefix of the other (truncated subtitle)
    fuzzy_failures = []
    genuine_misid = []
    for u in unmatched_v6:
        if u['title_sim'] >= 0.5 or is_title_prefix(u['v6_original_title'], u['gt_original_title']):
            fuzzy_failures.append(u)
        else:
            genuine_misid.append(u)

    print(f"Fuzzy matching failures (title_sim >= 0.5): {len(fuzzy_failures)}")
    print(f"Genuine misidentifications (title_sim < 0.5): {len(genuine_misid)}")
    print()

    # Print fuzzy matching failures briefly
    print("=" * 100)
    print("FUZZY MATCHING FAILURES (title_sim >= 0.5 -- same study, algorithm just failed to match)")
    print("=" * 100)
    for i, u in enumerate(fuzzy_failures, 1):
        print(f"\n--- Fuzzy Failure #{i} ---")
        print(f"  Replication: {u['replication_url']}")
        print(f"  Title sim:   {u['title_sim']:.3f}  |  Author Jaccard: {u['author_jaccard']:.3f}  |  Year match: {u['year_match']}  |  Score: {u['best_score']}/3")
        print(f"  V6 title:    {u['v6_original_title'][:100]}")
        print(f"  GT title:    {u['gt_original_title'][:100]}")

    # Print genuine misidentifications in detail
    print()
    print("=" * 100)
    print("GENUINE MISIDENTIFICATIONS (title_sim < 0.5 -- V6 extracted a different original study)")
    print("=" * 100)
    for i, u in enumerate(genuine_misid, 1):
        print(f"\n{'='*100}")
        print(f"CASE #{i}")
        print(f"{'='*100}")
        print(f"  Replication paper: {u['replication_url']}")
        print(f"  GT has {u['gt_count']} entries for this paper")
        print()
        print(f"  GT original study:")
        print(f"    Title:   {u['gt_original_title']}")
        print(f"    Authors: {u['gt_original_authors']}")
        print(f"    Year:    {u['gt_original_year']}")
        print(f"    Result:  {u['gt_result']}")
        print()
        print(f"  V6 extracted original study:")
        print(f"    Title:   {u['v6_original_title']}")
        print(f"    Authors: {u['v6_original_authors']}")
        print(f"    Year:    {u['v6_original_year']}")
        print(f"    Result:  {u['v6_result']}")
        print()
        print(f"  Matching scores:")
        print(f"    Title similarity: {u['title_sim']:.3f}")
        print(f"    Author Jaccard:   {u['author_jaccard']:.3f}")
        print(f"    Year match:       {u['year_match']}")
        print(f"    Overall score:    {u['best_score']}/3")

        # Try to diagnose what happened
        note = diagnose(u)
        print(f"  Likely explanation: {note}")

def diagnose(u):
    """Try to figure out what happened in a misidentification case."""
    v6_t = normalize_string(u['v6_original_title'])
    gt_t = normalize_string(u['gt_original_title'])
    a_jacc = u['author_jaccard']

    parts = []

    # Author overlap
    if a_jacc >= 0.5:
        parts.append("Same/overlapping authors -- V6 picked a different paper by the same research group")
    elif a_jacc > 0:
        parts.append(f"Some author overlap (Jaccard={a_jacc:.2f}) -- possibly related research group")

    # Year match
    if u['year_match']:
        parts.append("Same publication year -- V6 confused which of multiple same-year papers was being replicated")

    # Topic overlap
    stop = {'the', 'a', 'an', 'of', 'in', 'and', 'on', 'for', 'to', 'is', 'by', 'with', 'from', 'are', 'at', 'or'}
    v6_words = set(v6_t.split()) - stop
    gt_words = set(gt_t.split()) - stop
    if v6_words and gt_words:
        overlap = v6_words & gt_words
        if len(overlap) >= 3:
            parts.append(f"Related topic (shared words: {', '.join(sorted(overlap)[:5])}) -- different study in same research area")
        elif len(overlap) >= 1:
            parts.append(f"Weak topical overlap ({', '.join(sorted(overlap)[:3])})")

    if not parts:
        parts.append("Completely different study -- V6 misidentified which paper was being replicated")

    return "; ".join(parts)


if __name__ == '__main__':
    main()
