#!/usr/bin/env python3
"""
Compare sonnetv5 and sonnetv6 extraction results.
"""
import json
from pathlib import Path
from collections import defaultdict

base_dir = Path("/home/dan/metascience_observatory_pdfs/potential_replication_studies_identified_by_fred_team_not_in_ground_truth_need_to_be_ingested")

# Find all folders with both v5 and v6
folders_with_both = []
for folder in base_dir.iterdir():
    if folder.is_dir():
        v5_dir = folder / "sonnetv5"
        v6_dir = folder / "sonnetv6"
        if v5_dir.exists() and v6_dir.exists():
            folders_with_both.append(folder)

print(f"Found {len(folders_with_both)} folders with both v5 and v6 results\n")

# Compare results
stats = {
    'total_folders': len(folders_with_both),
    'v5_has_explanation': 0,
    'v6_has_explanation': 0,
    'v5_more_replications': 0,
    'v6_more_replications': 0,
    'same_num_replications': 0,
    'result_differences': 0,
    'v5_missing_result': 0,
    'v6_missing_result': 0,
    'v5_has_p_value_tails': 0,
    'v6_has_p_value_tails': 0,
}

differences = []

for folder in folders_with_both:
    folder_name = folder.name
    v5_file = folder / "sonnetv5" / f"{folder_name}_result_full.json"
    v6_file = folder / "sonnetv6" / f"{folder_name}_result_full.json"

    # Load results
    v5_data = None
    v6_data = None

    if v5_file.exists():
        try:
            with open(v5_file) as f:
                v5_data = json.load(f)
        except:
            pass
    else:
        stats['v5_missing_result'] += 1

    if v6_file.exists():
        try:
            with open(v6_file) as f:
                v6_data = json.load(f)
        except:
            pass
    else:
        stats['v6_missing_result'] += 1

    if not v5_data or not v6_data:
        continue

    # Compare
    v5_reps = v5_data.get('replications', [])
    v6_reps = v6_data.get('replications', [])

    # Count replications
    if len(v5_reps) > len(v6_reps):
        stats['v5_more_replications'] += 1
        differences.append({
            'folder': folder_name,
            'type': 'count',
            'v5_count': len(v5_reps),
            'v6_count': len(v6_reps)
        })
    elif len(v6_reps) > len(v5_reps):
        stats['v6_more_replications'] += 1
        differences.append({
            'folder': folder_name,
            'type': 'count',
            'v5_count': len(v5_reps),
            'v6_count': len(v6_reps)
        })
    else:
        stats['same_num_replications'] += 1

    # Check for explanation fields
    v5_has_exp = any('explanation' in rep for rep in v5_reps)
    v6_has_exp = any('explanation' in rep for rep in v6_reps)

    if v5_has_exp:
        stats['v5_has_explanation'] += 1
    if v6_has_exp:
        stats['v6_has_explanation'] += 1

    # Check for p_value_tails
    v5_has_tails = any(rep.get('replication_p_value_tails') and rep['replication_p_value_tails'] != '' for rep in v5_reps)
    v6_has_tails = any(rep.get('replication_p_value_tails') and rep['replication_p_value_tails'] != '' for rep in v6_reps)

    if v5_has_tails:
        stats['v5_has_p_value_tails'] += 1
    if v6_has_tails:
        stats['v6_has_p_value_tails'] += 1

    # Compare results for matching replications (by original_url)
    for i, v6_rep in enumerate(v6_reps):
        v6_url = v6_rep.get('original_url', '')
        # Find matching v5 replication
        matching_v5 = None
        for v5_rep in v5_reps:
            if v5_rep.get('original_url') == v6_url:
                matching_v5 = v5_rep
                break

        if matching_v5:
            if matching_v5.get('result') != v6_rep.get('result'):
                stats['result_differences'] += 1
                differences.append({
                    'folder': folder_name,
                    'type': 'result_mismatch',
                    'original_url': v6_url,
                    'v5_result': matching_v5.get('result'),
                    'v6_result': v6_rep.get('result')
                })

print("=" * 70)
print("SUMMARY STATISTICS")
print("=" * 70)
for key, value in stats.items():
    print(f"{key:30s}: {value}")

print("\n" + "=" * 70)
print("KEY FINDINGS")
print("=" * 70)

# Calculate percentages
total = stats['total_folders'] - stats['v5_missing_result'] - stats['v6_missing_result']
if total > 0:
    print(f"\nExplanation field presence:")
    print(f"  v5: {stats['v5_has_explanation']}/{total} ({100*stats['v5_has_explanation']/total:.1f}%)")
    print(f"  v6: {stats['v6_has_explanation']}/{total} ({100*stats['v6_has_explanation']/total:.1f}%)")

    print(f"\nP-value tails field populated:")
    print(f"  v5: {stats['v5_has_p_value_tails']}/{total} ({100*stats['v5_has_p_value_tails']/total:.1f}%)")
    print(f"  v6: {stats['v6_has_p_value_tails']}/{total} ({100*stats['v6_has_p_value_tails']/total:.1f}%)")

print(f"\n# of replications extracted:")
print(f"  v5 extracted more: {stats['v5_more_replications']}")
print(f"  v6 extracted more: {stats['v6_more_replications']}")
print(f"  Same count: {stats['same_num_replications']}")

print(f"\nResult classification differences: {stats['result_differences']}")

if differences:
    print("\n" + "=" * 70)
    print("DETAILED DIFFERENCES (first 10)")
    print("=" * 70)
    for diff in differences[:10]:
        if diff['type'] == 'count':
            print(f"\n{diff['folder']}:")
            print(f"  v5 extracted {diff['v5_count']} replications")
            print(f"  v6 extracted {diff['v6_count']} replications")
        elif diff['type'] == 'result_mismatch':
            print(f"\n{diff['folder']}:")
            print(f"  Original: {diff['original_url']}")
            print(f"  v5 result: {diff['v5_result']}")
            print(f"  v6 result: {diff['v6_result']}")
