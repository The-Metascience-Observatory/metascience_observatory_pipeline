#!/usr/bin/env python3
"""
Comprehensive evaluation of extraction performance using enhanced ground truth.

Usage:
    python evaluate_enhanced.py --tag v8_feb2026
    python evaluate_enhanced.py --tag v6_feb2026

This evaluation uses:
- Enhanced ground truth (226 entries with better coverage of multi-study papers)
- Improved matching algorithm (checks all extracted entries per paper, not just first)
- Field-by-field accuracy metrics
- Statistical completeness metrics
"""

import argparse
import pandas as pd
import numpy as np
from difflib import SequenceMatcher
import re
import json
from pathlib import Path
from datetime import date
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns

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

def result_match(r1, r2):
    """Check if results match, handling similar categories."""
    r1_norm = normalize_string(r1)
    r2_norm = normalize_string(r2)

    if r1_norm == r2_norm:
        return True

    # Handle near-matches (e.g., partial replication -> inconclusive)
    # For now, strict matching only
    return False

def evaluate(gt_path, extracted_path, output_path, tag):
    """Main evaluation function."""

    # Load datasets
    gt = pd.read_csv(gt_path)
    extracted = pd.read_csv(extracted_path)

    # Filter to replications
    gt = gt[gt['replication_url'].notna()].copy()
    extracted = extracted[extracted['contains_replications'] == True].copy()

    # Normalize URLs
    gt['replication_url_norm'] = gt['replication_url'].str.lower().str.replace('http://', 'https://').str.replace('dx.doi.org', 'doi.org')
    extracted['replication_url_norm'] = extracted['replication_url'].str.lower().str.replace('http://', 'https://').str.replace('dx.doi.org', 'doi.org')

    tag_upper = tag.split('_')[0].upper()  # e.g. "v8_feb2026" -> "V8"

    # Track metrics
    metrics = {
        'total_gt_entries': len(gt),
        'total_extracted_entries': len(extracted),
        'gt_papers': gt['replication_url_norm'].nunique(),
        'extracted_papers': extracted['replication_url_norm'].nunique(),
        'matched_entries': 0,
        'missing_entries': 0,
        'extra_entries': 0,
        'field_accuracy': {},
        'statistical_completeness': {},
        'result_classification_accuracy': {},
    }

    matches = []
    missing = []

    # Evaluate each GT entry
    for idx, gt_row in gt.iterrows():
        # Find all extracted entries for this replication paper
        ext_entries = extracted[extracted['replication_url_norm'] == gt_row['replication_url_norm']]

        if len(ext_entries) == 0:
            missing.append({
                'replication': gt_row['replication_url'],
                'original_title': gt_row['original_title'],
                'reason': f'Paper not in {tag_upper} dataset'
            })
            metrics['missing_entries'] += 1
            continue

        # Find best matching extracted entry for this GT entry
        best_match = None
        best_score = 0

        for _, ext_row in ext_entries.iterrows():
            # Calculate match score (0-3)
            title_match = fuzzy_match(ext_row['original_title'], gt_row['original_title'], threshold=0.75)
            authors_match_result = authors_match(ext_row['original_authors'], gt_row['original_authors'])
            year_match_result = year_match(ext_row['original_year'], gt_row['original_year'])

            score = sum([title_match, authors_match_result, year_match_result])

            if score > best_score:
                best_score = score
                best_match = ext_row

        if best_score >= 2:
            # Found a match (at least 2 of 3 fields match)
            metrics['matched_entries'] += 1

            # Detailed field comparison
            match_data = {
                'replication_doi': gt_row['replication_url'].split('/')[-1],
                'gt_title': str(gt_row['original_title'])[:60],
                'ext_title': str(best_match['original_title'])[:60],
                'title_match': fuzzy_match(best_match['original_title'], gt_row['original_title'], 0.75),
                'authors_match': authors_match(best_match['original_authors'], gt_row['original_authors']),
                'journal_match': fuzzy_match(best_match['original_journal'], gt_row['original_journal'], 0.75),
                'year_match': year_match(best_match['original_year'], gt_row['original_year']),
                'result_match': result_match(best_match['result'], gt_row['result']),
                'has_url_gt': pd.notna(gt_row['original_url']) and gt_row['original_url'] != '',
                'has_url_ext': pd.notna(best_match['original_url']) and best_match['original_url'] != '',
                'gt_result': gt_row['result'],
                'ext_result': best_match['result'],
            }

            # URL matching
            if match_data['has_url_gt'] and match_data['has_url_ext']:
                url_gt = normalize_string(gt_row['original_url']).replace('http://', 'https://').replace('dx.doi.org', 'doi.org')
                url_ext = normalize_string(best_match['original_url']).replace('http://', 'https://').replace('dx.doi.org', 'doi.org')
                match_data['url_match'] = url_gt == url_ext
            else:
                match_data['url_match'] = False

            # Statistical fields
            stat_fields = ['original_n', 'original_es', 'original_p_value',
                          'replication_n', 'replication_es', 'replication_p_value']
            for field in stat_fields:
                has_gt = pd.notna(gt_row.get(field)) and gt_row.get(field) != ''
                has_ext = pd.notna(best_match.get(field)) and best_match.get(field) != ''
                match_data[f'{field}_gt'] = has_gt
                match_data[f'{field}_ext'] = has_ext

                # Store actual values for accuracy comparison
                if has_gt and has_ext:
                    try:
                        match_data[f'{field}_gt_val'] = float(gt_row[field])
                        match_data[f'{field}_ext_val'] = float(best_match[field])
                    except (ValueError, TypeError):
                        match_data[f'{field}_gt_val'] = None
                        match_data[f'{field}_ext_val'] = None
                else:
                    match_data[f'{field}_gt_val'] = None
                    match_data[f'{field}_ext_val'] = None

            matches.append(match_data)
        else:
            # No good match found
            missing.append({
                'replication': gt_row['replication_url'],
                'original_title': gt_row['original_title'],
                'reason': f'No matching entry found (best score: {best_score}/3)',
                'ext_entries_count': len(ext_entries)
            })
            metrics['missing_entries'] += 1

    # Calculate field accuracy
    matches_df = pd.DataFrame(matches)

    if len(matches_df) > 0:
        # Generate confusion matrix PNG
        cm_path = output_path.with_name(f'{tag}_confusion_matrix.png')
        generate_confusion_matrix_png(matches_df, cm_path, tag_upper)

        metrics['field_accuracy'] = {
            'title': matches_df['title_match'].mean(),
            'authors': matches_df['authors_match'].mean(),
            'journal': matches_df['journal_match'].mean(),
            'year': matches_df['year_match'].mean(),
            'url': matches_df['url_match'].sum() / matches_df[matches_df['has_url_gt']]['url_match'].count() if matches_df['has_url_gt'].sum() > 0 else 0,
            'all_bibliographic': (matches_df['title_match'] & matches_df['authors_match'] &
                                 matches_df['journal_match'] & matches_df['year_match']).mean(),
        }

        # Result classification accuracy
        metrics['result_classification_accuracy'] = {
            'overall': matches_df['result_match'].mean(),
            'by_result': {}
        }

        for result in ['success', 'failure', 'inconclusive', 'reversal']:
            result_subset = matches_df[matches_df['gt_result'] == result]
            if len(result_subset) > 0:
                metrics['result_classification_accuracy']['by_result'][result] = {
                    'accuracy': result_subset['result_match'].mean(),
                    'count': len(result_subset),
                }

        # Statistical completeness and accuracy
        stat_fields = ['original_n', 'original_es', 'original_p_value',
                      'replication_n', 'replication_es', 'replication_p_value']
        for field in stat_fields:
            gt_has = matches_df[f'{field}_gt'].sum()
            ext_has = matches_df[f'{field}_ext'].sum()
            both_have = (matches_df[f'{field}_gt'] & matches_df[f'{field}_ext']).sum()

            # Calculate exact match rate for values
            exact_matches = 0
            value_pairs = 0
            for _, row in matches_df.iterrows():
                gv = row.get(f'{field}_gt_val')
                ev = row.get(f'{field}_ext_val')
                if gv is not None and ev is not None and not pd.isna(gv) and not pd.isna(ev):
                    value_pairs += 1
                    if abs(gv - ev) < 1e-10 or (gv != 0 and abs(gv - ev) / abs(gv) < 0.001):
                        exact_matches += 1

            metrics['statistical_completeness'][field] = {
                'gt_has': gt_has,
                'ext_has': ext_has,
                'both_have': both_have,
                'ext_coverage': ext_has / len(matches_df) if len(matches_df) > 0 else 0,
                'ext_recall': ext_has / gt_has if gt_has > 0 else 0,
                'value_pairs': value_pairs,
                'exact_matches': exact_matches,
                'exact_match_rate': exact_matches / gt_has if gt_has > 0 else 0,
            }

        # URL coverage
        url_analysis = {
            'gt_has_url': matches_df['has_url_gt'].sum(),
            'ext_has_url': matches_df['has_url_ext'].sum(),
            'both_have_url': (matches_df['has_url_gt'] & matches_df['has_url_ext']).sum(),
            'ext_url_recall': (matches_df['has_url_gt'] & matches_df['has_url_ext']).sum() / matches_df['has_url_gt'].sum() if matches_df['has_url_gt'].sum() > 0 else 0,
            'url_match_rate': matches_df[matches_df['has_url_gt'] & matches_df['has_url_ext']]['url_match'].mean() if (matches_df['has_url_gt'] & matches_df['has_url_ext']).sum() > 0 else 0,
        }
        metrics['url_analysis'] = url_analysis

    # Generate report
    report = generate_report(metrics, matches_df, missing, gt, extracted, tag_upper)

    # Write report
    with open(output_path, 'w') as f:
        f.write(report)

    return metrics, matches_df, missing

def generate_confusion_matrix_png(matches_df, output_path, tag_upper):
    """Generate a confusion matrix heatmap PNG."""
    all_classes = ['success', 'failure', 'inconclusive', 'reversal']
    # Only include classes that appear in the data
    gt_classes = [c for c in all_classes if c in matches_df['gt_result'].values]
    pred_classes = [c for c in all_classes if c in matches_df['ext_result'].values]

    # Build confusion matrix
    cm = np.zeros((len(gt_classes), len(pred_classes)), dtype=int)
    for i, gt_cls in enumerate(gt_classes):
        for j, pred_cls in enumerate(pred_classes):
            cm[i, j] = ((matches_df['gt_result'] == gt_cls) & (matches_df['ext_result'] == pred_cls)).sum()

    y_labels = [c.capitalize() for c in gt_classes]
    x_labels = [c.capitalize() for c in pred_classes]

    fig, ax = plt.subplots(figsize=(11, 5))
    sns.heatmap(cm, annot=False, cmap='Blues', xticklabels=x_labels,
                yticklabels=y_labels, linewidths=2, linecolor='white',
                cbar=False, ax=ax)
    ax.set_yticklabels(ax.get_yticklabels(), rotation=0)

    # Annotate cells: bold+large on diagonal, smaller off-diagonal
    max_val = cm.max()
    for i in range(len(gt_classes)):
        for j in range(len(pred_classes)):
            val = cm[i, j]
            color = 'white' if val > max_val * 0.5 else '#1a3a5c'
            is_diag = (i == j)
            ax.text(j + 0.5, i + 0.5, str(val),
                    ha='center', va='center',
                    fontsize=20 if is_diag else 14,
                    fontweight='bold' if is_diag else 'normal',
                    color=color)

    ax.set_xlabel('Pipeline Prediction', fontsize=14)
    ax.set_ylabel('Ground Truth', fontsize=14)
    ax.set_title(f'{tag_upper} Result Classification Confusion Matrix', fontsize=16, pad=16)
    ax.tick_params(labelsize=13)
    fig.savefig(output_path, bbox_inches='tight', dpi=150, facecolor='white')
    plt.close(fig)


def generate_report(metrics, matches_df, missing, gt, extracted, tag_upper):
    """Generate human-readable report."""

    report = []
    report.append("=" * 90)
    report.append(f"{tag_upper} EXTRACTION PERFORMANCE REPORT")
    report.append("Using Enhanced Ground Truth Dataset")
    report.append("=" * 90)
    report.append("")

    # Overview
    report.append("OVERVIEW")
    report.append("-" * 90)
    report.append(f"Ground Truth Entries: {metrics['total_gt_entries']}")
    report.append(f"{tag_upper} Extracted Entries: {metrics['total_extracted_entries']}")
    report.append(f"Unique Replication Papers (GT): {metrics['gt_papers']}")
    report.append(f"Unique Replication Papers ({tag_upper}): {metrics['extracted_papers']}")
    report.append("")

    # Entry matching
    report.append("ENTRY-LEVEL MATCHING")
    report.append("-" * 90)
    report.append(f"GT Entries Matched in {tag_upper}: {metrics['matched_entries']}/{metrics['total_gt_entries']} ({100*metrics['matched_entries']/metrics['total_gt_entries']:.1f}%)")
    report.append(f"GT Entries Not Found: {metrics['missing_entries']}/{metrics['total_gt_entries']} ({100*metrics['missing_entries']/metrics['total_gt_entries']:.1f}%)")
    report.append("")

    if len(matches_df) > 0:
        # Field accuracy
        report.append("FIELD-BY-FIELD ACCURACY (for matched entries)")
        report.append("-" * 90)
        report.append(f"Title:     {len(matches_df[matches_df['title_match']])}/{len(matches_df)} ({100*metrics['field_accuracy']['title']:.1f}%)")
        report.append(f"Authors:   {len(matches_df[matches_df['authors_match']])}/{len(matches_df)} ({100*metrics['field_accuracy']['authors']:.1f}%)")
        report.append(f"Journal:   {len(matches_df[matches_df['journal_match']])}/{len(matches_df)} ({100*metrics['field_accuracy']['journal']:.1f}%)")
        report.append(f"Year:      {len(matches_df[matches_df['year_match']])}/{len(matches_df)} ({100*metrics['field_accuracy']['year']:.1f}%)")
        report.append("")
        report.append(f"ALL 4 BIBLIOGRAPHIC FIELDS CORRECT: {(matches_df['title_match'] & matches_df['authors_match'] & matches_df['journal_match'] & matches_df['year_match']).sum()}/{len(matches_df)} ({100*metrics['field_accuracy']['all_bibliographic']:.1f}%)")
        report.append("")

        # URL analysis
        url = metrics['url_analysis']
        report.append("URL EXTRACTION")
        report.append("-" * 90)
        report.append(f"GT entries with URL: {url['gt_has_url']}/{len(matches_df)} ({100*url['gt_has_url']/len(matches_df):.1f}%)")
        report.append(f"{tag_upper} extracted URL:    {url['ext_has_url']}/{len(matches_df)} ({100*url['ext_has_url']/len(matches_df):.1f}%)")
        report.append(f"Both have URL:       {url['both_have_url']}/{len(matches_df)} ({100*url['both_have_url']/len(matches_df):.1f}%)")
        report.append("")
        report.append(f"{tag_upper} URL Recall (of GT URLs):        {url['both_have_url']}/{url['gt_has_url']} ({100*url['ext_url_recall']:.1f}%)")
        report.append(f"URL Match Rate (when both present): {100*url['url_match_rate']:.1f}%")
        report.append("")

        # Result classification
        report.append("RESULT CLASSIFICATION ACCURACY")
        report.append("-" * 90)
        report.append(f"Overall: {len(matches_df[matches_df['result_match']])}/{len(matches_df)} ({100*metrics['result_classification_accuracy']['overall']:.1f}%)")
        report.append("")
        report.append("By result type:")
        for result, data in metrics['result_classification_accuracy']['by_result'].items():
            report.append(f"  {result:15s}: {int(data['accuracy']*data['count'])}/{data['count']} ({100*data['accuracy']:.1f}%)")
        report.append("")

        # Confusion matrix for results
        report.append(f"Result Classification Confusion (GT -> {tag_upper}):")
        report.append("-" * 90)
        confusion = pd.crosstab(matches_df['gt_result'], matches_df['ext_result'], margins=True)
        report.append(str(confusion))
        report.append("")

        # Statistical completeness and accuracy
        report.append("STATISTICAL DATA EXTRACTION")
        report.append("-" * 90)
        report.append(f"{'Field':<20} {'GT Has':<10} {tag_upper+' Has':<10} {'Coverage':<12} {'Exact Match':<15}")
        report.append("-" * 90)
        for field, data in metrics['statistical_completeness'].items():
            exact_str = f"{data['exact_matches']}/{data['gt_has']} ({100*data['exact_match_rate']:.1f}%)" if data['gt_has'] > 0 else "N/A"
            report.append(f"{field:<20} {data['gt_has']:<10} {data['ext_has']:<10} {100*data['ext_recall']:>6.1f}%      {exact_str}")
        report.append("")

        # Summary statistics
        report.append("=" * 90)
        report.append("SUMMARY")
        report.append("=" * 90)
        report.append(f"Entry Matching:           {100*metrics['matched_entries']/metrics['total_gt_entries']:.1f}% of GT entries found in {tag_upper}")
        report.append(f"Bibliographic Accuracy:   {100*metrics['field_accuracy']['all_bibliographic']:.1f}% perfect bibliographic matches")
        report.append(f"URL Extraction:           {100*url['ext_url_recall']:.1f}% of GT URLs recovered")
        report.append(f"Result Classification:    {100*metrics['result_classification_accuracy']['overall']:.1f}% agreement with GT")
        report.append(f"Replication N Coverage:   {100*metrics['statistical_completeness']['replication_n']['ext_coverage']:.1f}%")
        report.append(f"Replication ES Coverage:  {100*metrics['statistical_completeness']['replication_es']['ext_coverage']:.1f}%")
        report.append("")

        # Error analysis
        if len(missing) > 0:
            report.append("MISSING ENTRIES (first 10)")
            report.append("-" * 90)
            for m in missing[:10]:
                report.append(f"Replication: {m['replication']}")
                report.append(f"  Original: {m['original_title']}")
                report.append(f"  Reason: {m['reason']}")
                report.append("")

        report.append("=" * 90)

    return "\n".join(report)


STAT_FIELD_NAMES = {
    'replication_n': 'Replication sample size',
    'replication_p_value': 'Replication p-value',
    'replication_es': 'Replication effect size',
    'original_es': 'Original effect size',
    'original_p_value': 'Original p-value',
    'original_n': 'Original sample size',
}

STAT_FIELD_ORDER = ['replication_n', 'replication_p_value', 'replication_es',
                    'original_es', 'original_p_value', 'original_n']


def generate_markdown_report(metrics, tag_upper, compare_metrics=None, compare_tag_upper=None):
    """Generate markdown evaluation report."""
    m = metrics
    gt_entries = m['total_gt_entries']
    gt_papers = m['gt_papers']
    ext_entries = m['total_extracted_entries']
    matched = m['matched_entries']
    extra = ext_entries - gt_entries
    recall_pct = 100 * matched / gt_entries

    lines = []
    lines.append(f'# {tag_upper} Extraction Pipeline Evaluation')
    lines.append('')
    lines.append(date.today().strftime('%-m/%-d/%Y'))
    lines.append('')
    lines.append(f'The Metascience Observatory uses an automated pipeline to extract structured replication data from academic papers. We evaluated it against a hand-validated ground truth of {gt_entries} effect replication entries across {gt_papers} papers, primarily from psychology and related social sciences.')
    lines.append('')

    # Key results
    lines.append('## Key results')
    lines.append('')
    lines.append(f'The pipeline extracted {ext_entries} entries total versus {gt_entries} in the ground truth. The pipeline got {matched} of {gt_entries} ground truth entries, a recall of {recall_pct:.0f}%. Many of the ones it missed were because it was not as granular in separating out individual results as the ground truth is, in the case where one paper replicates many specific findings. The {extra} extra entries are either additional legitimate replications the ground truth missed, ones that our evaluation system had trouble matching, or cases where the pipeline identified a different original study than the ground truth for the same replication.')
    lines.append('')

    # Result classification table
    by_result = m['result_classification_accuracy']['by_result']
    overall_pct = 100 * m['result_classification_accuracy']['overall']
    lines.append(f'The pipeline classifies each replication as success, failure, or inconclusive. Among the {matched} replications it found that matched the ground truth:')
    lines.append('')
    lines.append('| Category | Accuracy |')
    lines.append('|----------|----------|')
    for result in ['success', 'failure', 'inconclusive']:
        if result in by_result:
            d = by_result[result]
            correct = int(d['accuracy'] * d['count'])
            lines.append(f'| {result.capitalize()}  | {100*d["accuracy"]:.1f}% ({correct}/{d["count"]}) |')
    lines.append('')

    if compare_metrics:
        cmp_overall = 100 * compare_metrics['result_classification_accuracy']['overall']
        lines.append(f'Result classification accuracy {"dropped" if overall_pct < cmp_overall else "improved"} from {cmp_overall:.1f}% ({compare_tag_upper}) to {overall_pct:.1f}% ({tag_upper}). The pipeline continues to under-predict inconclusive, and now also misclassifies more success and failure cases than {compare_tag_upper} did.')
    else:
        lines.append(f'The main difference here is that the pipeline tends to commit to success or failure where human annotators are more likely to go with inconclusive.')
    lines.append('')

    lines.append(f'![Confusion Matrix](/docs/{tag_upper.lower()}_confusion_matrix.png)')
    lines.append('')

    # URL extraction (only if comparison available)
    url = m['url_analysis']
    url_recall_pct = 100 * url['ext_url_recall']
    if compare_metrics:
        cmp_url_recall = 100 * compare_metrics['url_analysis']['ext_url_recall']
        lines.append('## URL extraction')
        lines.append('')
        lines.append(f'URL recall {"improved" if url_recall_pct > cmp_url_recall else "changed"} from {cmp_url_recall:.1f}% ({compare_tag_upper}) to **{url_recall_pct:.1f}%** ({tag_upper}).')
        lines.append('')

    # Statistical data extraction
    lines.append('## Statistical data extraction')
    lines.append('')
    lines.append('The ground truth dataset did not have full coverage of the statistical data in the papers. However, where we had statistical data in the ground truth, the pipeline\'s extracted values were compared:')
    lines.append('')
    lines.append('| Field | Coverage | Exact match |')
    lines.append('|-------|----------|-------------|')
    for field in STAT_FIELD_ORDER:
        if field in m['statistical_completeness']:
            d = m['statistical_completeness'][field]
            cov_pct = 100 * d['ext_recall']
            cov_str = f'{cov_pct:.1f}% ({int(d["ext_has"])}/{int(d["gt_has"])})'
            if d['value_pairs'] > 0:
                exact_str = f'{100*d["exact_match_rate"]:.1f}% ({d["exact_matches"]}/{d["value_pairs"]})'
            else:
                exact_str = 'N/A'
            lines.append(f'| {STAT_FIELD_NAMES[field]} | {cov_str} | {exact_str} |')
    lines.append('')

    # Comparison table
    if compare_metrics:
        cm = compare_metrics
        lines.append(f'## Comparison with {compare_tag_upper}')
        lines.append('')
        lines.append(f'| Metric | {compare_tag_upper} | {tag_upper} | Change |')
        lines.append('|--------|----|----|--------|')

        rows = []
        # Entry matching
        cmp_match = 100 * cm['matched_entries'] / cm['total_gt_entries']
        cur_match = 100 * matched / gt_entries
        rows.append(('Entry matching',
                      f'{cmp_match:.1f}% ({cm["matched_entries"]}/{cm["total_gt_entries"]})',
                      f'{cur_match:.1f}% ({matched}/{gt_entries})',
                      cur_match - cmp_match))
        # Bibliographic accuracy
        cmp_bib = 100 * cm['field_accuracy']['all_bibliographic']
        cur_bib = 100 * m['field_accuracy']['all_bibliographic']
        rows.append(('Bibliographic accuracy', f'{cmp_bib:.1f}%', f'{cur_bib:.1f}%', cur_bib - cmp_bib))
        # URL recall
        rows.append(('URL recall', f'{cmp_url_recall:.1f}%', f'{url_recall_pct:.1f}%', url_recall_pct - cmp_url_recall))
        # Result classification
        rows.append(('Result classification', f'{cmp_overall:.1f}%', f'{overall_pct:.1f}%', overall_pct - cmp_overall))
        # Stat exact match rates
        for field, label in [('replication_es', 'Replication ES exact match'),
                              ('replication_p_value', 'Replication p-value exact match'),
                              ('replication_n', 'Replication N exact match')]:
            cmp_rate = 100 * cm['statistical_completeness'][field]['exact_match_rate']
            cur_rate = 100 * m['statistical_completeness'][field]['exact_match_rate']
            rows.append((label, f'{cmp_rate:.1f}%', f'{cur_rate:.1f}%', cur_rate - cmp_rate))

        for label, cmp_val, cur_val, delta in rows:
            sign = '+' if delta >= 0 else ''
            lines.append(f'| {label} | {cmp_val} | {cur_val} | {sign}{delta:.1f}% |')
        lines.append('')

        # Summary
        improvements = [l for l, _, _, d in rows if d > 1]
        regressions = [l for l, _, _, d in rows if d < -1]
        if improvements and regressions:
            lines.append(f'{tag_upper} improved on {", ".join(improvements).lower()}, but regressed on {", ".join(regressions).lower()}.')
        lines.append('')
    else:
        lines.append(f'The results of this evaluation informed the creation of subsequent extraction pipelines.')
        lines.append('')

    return '\n'.join(lines)


def save_metrics_json(metrics, path):
    """Save metrics to JSON, converting numpy types."""
    def convert(obj):
        if isinstance(obj, (np.integer, np.int64)):
            return int(obj)
        if isinstance(obj, (np.floating, np.float64)):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        raise TypeError(f"Not serializable: {type(obj)}")
    with open(path, 'w') as f:
        json.dump(metrics, f, indent=2, default=convert)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Evaluate extraction pipeline against enhanced ground truth')
    parser.add_argument('--tag', required=True, help='Extraction tag (e.g., v8_feb2026, v6_feb2026)')
    parser.add_argument('--compare-tag', help='Previous tag to compare against (e.g., v6_feb2026)')
    args = parser.parse_args()

    gt_path = Path('ground_truth_enhanced.csv')
    extracted_path = Path(f'ground_truth_data_filtered_PDFs/collated_results_{args.tag}.csv')
    output_path = Path(f'{args.tag}_performance_report_enhanced.txt')

    tag_upper = args.tag.split('_')[0].upper()

    print(f"Evaluating {tag_upper} extraction performance...")
    print(f"Ground Truth: {gt_path}")
    print(f"Extracted Dataset: {extracted_path}")
    print()

    if not extracted_path.exists():
        print(f"ERROR: {extracted_path} not found. Run extraction with --tag {args.tag} first.")
        exit(1)

    metrics, matches_df, missing = evaluate(gt_path, extracted_path, output_path, args.tag)

    # Save metrics JSON
    metrics_json_path = Path(f'{args.tag}_metrics.json')
    save_metrics_json(metrics, metrics_json_path)
    print(f"Metrics JSON: {metrics_json_path}")

    # Generate markdown report
    compare_metrics = None
    compare_tag_upper = None
    if args.compare_tag:
        compare_json = Path(f'{args.compare_tag}_metrics.json')
        if compare_json.exists():
            with open(compare_json) as f:
                compare_metrics = json.load(f)
            compare_tag_upper = args.compare_tag.split('_')[0].upper()
            print(f"Comparing against: {compare_tag_upper} ({compare_json})")
        else:
            print(f"WARNING: {compare_json} not found, skipping comparison. Run --tag {args.compare_tag} first.")

    md_path = Path(f'{args.tag}_pipeline_evaluation.md')
    md_report = generate_markdown_report(metrics, tag_upper, compare_metrics, compare_tag_upper)
    with open(md_path, 'w') as f:
        f.write(md_report)
    print(f"Markdown report: {md_path}")

    print(f"Text report: {output_path}")
    print()
    print(f"Quick Summary:")
    print(f"  Matched: {metrics['matched_entries']}/{metrics['total_gt_entries']} ({100*metrics['matched_entries']/metrics['total_gt_entries']:.1f}%)")
    if len(matches_df) > 0:
        print(f"  Bibliographic accuracy: {100*metrics['field_accuracy']['all_bibliographic']:.1f}%")
        print(f"  Result classification: {100*metrics['result_classification_accuracy']['overall']:.1f}%")
