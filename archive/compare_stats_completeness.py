#!/usr/bin/env python3
"""
Compare statistical completeness between v5 and v6 extractions.
"""
import json
from pathlib import Path

base_dir = Path("/home/dan/metascience_observatory_pdfs/potential_replication_studies_identified_by_fred_team_not_in_ground_truth_need_to_be_ingested")

# Find all folders with both v5 and v6
folders_with_both = []
for folder in base_dir.iterdir():
    if folder.is_dir():
        v5_dir = folder / "sonnetv5"
        v6_dir = folder / "sonnetv6"
        if v5_dir.exists() and v6_dir.exists():
            folders_with_both.append(folder)

print(f"Analyzing {len(folders_with_both)} folders with both v5 and v6 results\n")

# Track statistics completeness
v5_stats = {
    'total_replications': 0,
    'has_replication_n': 0,
    'has_replication_es': 0,
    'has_replication_p_value': 0,
    'has_p_value_tails': 0,
    'has_original_es': 0,
    'has_original_n': 0,
}

v6_stats = {
    'total_replications': 0,
    'has_replication_n': 0,
    'has_replication_es': 0,
    'has_replication_p_value': 0,
    'has_p_value_tails': 0,
    'has_original_es': 0,
    'has_original_n': 0,
}

def has_value(field_value):
    """Check if a field has a meaningful value."""
    if field_value is None:
        return False
    if isinstance(field_value, str):
        return field_value.strip() != ""
    return True

for folder in folders_with_both:
    folder_name = folder.name
    v5_file = folder / "sonnetv5" / f"{folder_name}_result_full.json"
    v6_file = folder / "sonnetv6" / f"{folder_name}_result_full.json"

    # Load v5
    if v5_file.exists():
        try:
            with open(v5_file) as f:
                v5_data = json.load(f)
                for rep in v5_data.get('replications', []):
                    v5_stats['total_replications'] += 1
                    if has_value(rep.get('replication_n')):
                        v5_stats['has_replication_n'] += 1
                    if has_value(rep.get('replication_es')):
                        v5_stats['has_replication_es'] += 1
                    if has_value(rep.get('replication_p_value')):
                        v5_stats['has_replication_p_value'] += 1
                    if has_value(rep.get('replication_p_value_tails')):
                        v5_stats['has_p_value_tails'] += 1
                    if has_value(rep.get('original_es')):
                        v5_stats['has_original_es'] += 1
                    if has_value(rep.get('original_n')):
                        v5_stats['has_original_n'] += 1
        except:
            pass

    # Load v6
    if v6_file.exists():
        try:
            with open(v6_file) as f:
                v6_data = json.load(f)
                for rep in v6_data.get('replications', []):
                    v6_stats['total_replications'] += 1
                    if has_value(rep.get('replication_n')):
                        v6_stats['has_replication_n'] += 1
                    if has_value(rep.get('replication_es')):
                        v6_stats['has_replication_es'] += 1
                    if has_value(rep.get('replication_p_value')):
                        v6_stats['has_replication_p_value'] += 1
                    if has_value(rep.get('replication_p_value_tails')):
                        v6_stats['has_p_value_tails'] += 1
                    if has_value(rep.get('original_es')):
                        v6_stats['has_original_es'] += 1
                    if has_value(rep.get('original_n')):
                        v6_stats['has_original_n'] += 1
        except:
            pass

print("=" * 70)
print("STATISTICAL COMPLETENESS COMPARISON")
print("=" * 70)
print(f"\n{'Statistic':<30} {'v5':<20} {'v6':<20} {'Winner'}")
print("-" * 70)

def print_stat(name, v5_count, v6_count, v5_total, v6_total):
    v5_pct = 100 * v5_count / v5_total if v5_total > 0 else 0
    v6_pct = 100 * v6_count / v6_total if v6_total > 0 else 0
    v5_str = f"{v5_count}/{v5_total} ({v5_pct:.1f}%)"
    v6_str = f"{v6_count}/{v6_total} ({v6_pct:.1f}%)"
    winner = "v6" if v6_pct > v5_pct else ("v5" if v5_pct > v6_pct else "tie")
    print(f"{name:<30} {v5_str:<20} {v6_str:<20} {winner}")

print(f"{'Total replications extracted':<30} {v5_stats['total_replications']:<20} {v6_stats['total_replications']:<20}")
print()

print_stat(
    "Replication sample size (n)",
    v5_stats['has_replication_n'],
    v6_stats['has_replication_n'],
    v5_stats['total_replications'],
    v6_stats['total_replications']
)

print_stat(
    "Replication effect size",
    v5_stats['has_replication_es'],
    v6_stats['has_replication_es'],
    v5_stats['total_replications'],
    v6_stats['total_replications']
)

print_stat(
    "Replication p-value",
    v5_stats['has_replication_p_value'],
    v6_stats['has_replication_p_value'],
    v5_stats['total_replications'],
    v6_stats['total_replications']
)

print_stat(
    "P-value tails",
    v5_stats['has_p_value_tails'],
    v6_stats['has_p_value_tails'],
    v5_stats['total_replications'],
    v6_stats['total_replications']
)

print_stat(
    "Original effect size",
    v5_stats['has_original_es'],
    v6_stats['has_original_es'],
    v5_stats['total_replications'],
    v6_stats['total_replications']
)

print_stat(
    "Original sample size (n)",
    v5_stats['has_original_n'],
    v6_stats['has_original_n'],
    v5_stats['total_replications'],
    v6_stats['total_replications']
)

print("\n" + "=" * 70)
