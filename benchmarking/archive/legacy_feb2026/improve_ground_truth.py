#!/usr/bin/env python3
"""
Improve ground truth dataset using V6 extraction results.

Strategy:
1. Keep all existing ground truth entries (baseline)
2. For multi-study papers: Add missing replication entries from V6
3. Fill missing URLs using V6 data when bibliographic info matches
4. Flag potential corrections where V6 differs from GT
5. Add statistical fields from V6 where GT is missing them

Output:
- ground_truth_enhanced.csv - improved ground truth
- enhancement_report.txt - what was changed and why
"""

import pandas as pd
import numpy as np
from difflib import SequenceMatcher
import re
from pathlib import Path

def normalize_string(s):
    if pd.isna(s) or s == '':
        return ''
    return str(s).lower().strip()

def extract_last_names(author_string):
    """Extract last names from author string."""
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

def authors_match(a1, a2, threshold=0.6):
    """Check if authors match based on last names."""
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
    """Fuzzy string matching."""
    s1_norm = normalize_string(s1)
    s2_norm = normalize_string(s2)
    if not s1_norm or not s2_norm:
        return False
    ratio = SequenceMatcher(None, s1_norm, s2_norm).ratio()
    return ratio >= threshold

def year_match(y1, y2):
    """Compare years."""
    try:
        y1_str = str(y1).replace('.0', '').strip()
        y2_str = str(y2).replace('.0', '').strip()
        return y1_str == y2_str
    except:
        return False

def find_matching_v6_entry(gt_row, v6_entries):
    """Find V6 entry that matches this GT row's original study."""
    for _, v6_row in v6_entries.iterrows():
        # Check if original study matches
        title_match = fuzzy_match(v6_row['original_title'], gt_row['original_title'], threshold=0.75)
        authors_match_result = authors_match(v6_row['original_authors'], gt_row['original_authors'])
        year_match_result = year_match(v6_row['original_year'], gt_row['original_year'])

        # Need at least 2 of 3 to match
        matches = sum([title_match, authors_match_result, year_match_result])
        if matches >= 2:
            return v6_row

    return None

def improve_ground_truth(gt_path, v6_path, output_path, report_path):
    """Main function to improve ground truth."""

    # Load datasets
    gt = pd.read_csv(gt_path)
    v6 = pd.read_csv(v6_path)

    # Filter to replications
    gt = gt[gt['replication_url'].notna()].copy()
    v6 = v6[v6['contains_replications'] == True].copy()

    # Normalize URLs
    gt['replication_url_norm'] = gt['replication_url'].str.lower().str.replace('http://', 'https://').str.replace('dx.doi.org', 'doi.org')
    v6['replication_url_norm'] = v6['replication_url'].str.lower().str.replace('http://', 'https://').str.replace('dx.doi.org', 'doi.org')

    # Track changes
    changes = {
        'added_entries': [],
        'filled_urls': [],
        'filled_stats': [],
        'potential_corrections': [],
    }

    enhanced_rows = []

    # Process each GT row
    for _, gt_row in gt.iterrows():
        # Find corresponding V6 entries for this replication paper
        v6_entries = v6[v6['replication_url_norm'] == gt_row['replication_url_norm']]

        if len(v6_entries) == 0:
            # No V6 data - keep GT as is
            enhanced_rows.append(gt_row.to_dict())
            continue

        # Find the V6 entry that matches this GT entry's original study
        matching_v6 = find_matching_v6_entry(gt_row, v6_entries)

        if matching_v6 is not None:
            # Found a match - enhance GT with V6 data
            enhanced_row = gt_row.to_dict()

            # Fill missing URL
            if (pd.isna(gt_row['original_url']) or gt_row['original_url'] == '') and \
               (pd.notna(matching_v6['original_url']) and matching_v6['original_url'] != ''):
                enhanced_row['original_url'] = matching_v6['original_url']
                changes['filled_urls'].append({
                    'replication': gt_row['replication_url'],
                    'original_title': gt_row['original_title'],
                    'url_added': matching_v6['original_url']
                })

            # Fill missing statistical fields
            stat_fields = [
                'original_n', 'original_es', 'original_es_type', 'original_es_95_CI',
                'original_p_value', 'original_p_value_type', 'original_p_value_tails',
                'replication_n', 'replication_es', 'replication_es_type', 'replication_es_95_CI',
                'replication_p_value', 'replication_p_value_type', 'replication_p_value_tails'
            ]

            stats_filled = []
            for field in stat_fields:
                if field in gt_row.index and field in matching_v6.index:
                    if (pd.isna(gt_row[field]) or gt_row[field] == '') and \
                       (pd.notna(matching_v6[field]) and matching_v6[field] != ''):
                        enhanced_row[field] = matching_v6[field]
                        stats_filled.append(field)

            if stats_filled:
                changes['filled_stats'].append({
                    'replication': gt_row['replication_url'],
                    'fields_filled': stats_filled
                })

            enhanced_rows.append(enhanced_row)

            # Check if there are OTHER V6 entries for this paper (multi-study case)
            other_v6_entries = v6_entries[v6_entries.index != matching_v6.name]

            for _, other_v6 in other_v6_entries.iterrows():
                # Check if this study is already in GT
                existing = gt[
                    (gt['replication_url_norm'] == gt_row['replication_url_norm']) &
                    (gt['original_title'].apply(lambda x: fuzzy_match(x, other_v6['original_title'], 0.8)))
                ]

                if len(existing) == 0:
                    # This is a NEW study not in GT - add it
                    new_row = gt_row.to_dict()

                    # Update original study fields from V6
                    new_row['original_url'] = other_v6['original_url']
                    new_row['original_authors'] = other_v6['original_authors']
                    new_row['original_title'] = other_v6['original_title']
                    new_row['original_journal'] = other_v6['original_journal']
                    new_row['original_volume'] = other_v6['original_volume']
                    new_row['original_issue'] = other_v6['original_issue']
                    new_row['original_pages'] = other_v6['original_pages']
                    new_row['original_year'] = other_v6['original_year']
                    new_row['description'] = other_v6['description']
                    new_row['result'] = other_v6['result']

                    # Copy statistical fields
                    for field in stat_fields:
                        if field in other_v6.index:
                            new_row[field] = other_v6[field]

                    enhanced_rows.append(new_row)

                    changes['added_entries'].append({
                        'replication': gt_row['replication_url'],
                        'original_added': other_v6['original_title'],
                        'result': other_v6['result']
                    })
        else:
            # No matching V6 entry found - keep GT as is but flag for review
            enhanced_rows.append(gt_row.to_dict())
            changes['potential_corrections'].append({
                'replication': gt_row['replication_url'],
                'gt_original': gt_row['original_title'],
                'reason': f'No matching V6 entry found among {len(v6_entries)} V6 entries'
            })

    # Create enhanced dataframe
    enhanced_df = pd.DataFrame(enhanced_rows)

    # Write enhanced CSV
    enhanced_df.to_csv(output_path, index=False)

    # Write report
    with open(report_path, 'w') as f:
        f.write("=" * 80 + "\n")
        f.write("GROUND TRUTH ENHANCEMENT REPORT\n")
        f.write("=" * 80 + "\n\n")

        f.write(f"Original GT entries: {len(gt)}\n")
        f.write(f"Enhanced entries: {len(enhanced_df)}\n")
        f.write(f"New entries added: {len(enhanced_df) - len(gt)}\n\n")

        f.write(f"Changes made:\n")
        f.write(f"  URLs filled: {len(changes['filled_urls'])}\n")
        f.write(f"  Statistical fields filled: {len(changes['filled_stats'])}\n")
        f.write(f"  New replication entries added: {len(changes['added_entries'])}\n")
        f.write(f"  Potential corrections flagged: {len(changes['potential_corrections'])}\n\n")

        if changes['added_entries']:
            f.write("=" * 80 + "\n")
            f.write("NEW ENTRIES ADDED (Multi-study papers)\n")
            f.write("=" * 80 + "\n\n")
            for change in changes['added_entries'][:20]:  # Show first 20
                f.write(f"Replication: {change['replication']}\n")
                f.write(f"  Added: {change['original_added']}\n")
                f.write(f"  Result: {change['result']}\n\n")

        if changes['filled_urls']:
            f.write("=" * 80 + "\n")
            f.write("URLs FILLED\n")
            f.write("=" * 80 + "\n\n")
            for change in changes['filled_urls'][:20]:
                f.write(f"Replication: {change['replication']}\n")
                f.write(f"  Original: {change['original_title'][:60]}\n")
                f.write(f"  URL added: {change['url_added']}\n\n")

        if changes['potential_corrections']:
            f.write("=" * 80 + "\n")
            f.write("POTENTIAL CORRECTIONS NEEDED (Review manually)\n")
            f.write("=" * 80 + "\n\n")
            for change in changes['potential_corrections'][:20]:
                f.write(f"Replication: {change['replication']}\n")
                f.write(f"  GT original: {change['gt_original']}\n")
                f.write(f"  Reason: {change['reason']}\n\n")

    return enhanced_df, changes


if __name__ == '__main__':
    import sys

    gt_path = Path('ground_truth_data_filtered.csv')
    v6_path = Path('ground_truth_data_filtered_PDFs/collated_results_v6_feb2026.csv')
    output_path = Path('ground_truth_enhanced.csv')
    report_path = Path('enhancement_report.txt')

    print("Improving ground truth using V6 extraction results...")
    print(f"Input GT: {gt_path}")
    print(f"Input V6: {v6_path}")
    print()

    enhanced_df, changes = improve_ground_truth(gt_path, v6_path, output_path, report_path)

    print("=" * 80)
    print("ENHANCEMENT COMPLETE")
    print("=" * 80)
    print(f"Enhanced CSV: {output_path}")
    print(f"Report: {report_path}")
    print()
    print(f"Original entries: {len(pd.read_csv(gt_path))}")
    print(f"Enhanced entries: {len(enhanced_df)}")
    print(f"New entries added: {len(enhanced_df) - len(pd.read_csv(gt_path))}")
    print()
    print(f"URLs filled: {len(changes['filled_urls'])}")
    print(f"Stats fields filled: {len(changes['filled_stats'])}")
    print(f"Flagged for review: {len(changes['potential_corrections'])}")
    print("=" * 80)
