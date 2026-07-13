#!/usr/bin/env python3
"""
Compare token usage, costs, and time between v5 and v6 extractions.
"""
import json
from pathlib import Path
import statistics

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

v5_data = []
v6_data = []

for folder in folders_with_both:
    folder_name = folder.name
    v5_debug = folder / "sonnetv5" / "debug_log.json"
    v6_debug = folder / "sonnetv6" / "debug_log.json"

    # Load v5 debug
    if v5_debug.exists():
        try:
            with open(v5_debug) as f:
                debug = json.load(f)
                usage = debug.get('usage', {})
                if usage:
                    v5_data.append({
                        'folder': folder_name,
                        'cost': usage.get('cost_usd', 0),
                        'duration_ms': usage.get('duration_ms', 0),
                        'wall_time_ms': usage.get('wall_time_ms', 0),
                        'input_tokens': usage.get('input_tokens', 0),
                        'output_tokens': usage.get('output_tokens', 0),
                        'cache_creation_tokens': usage.get('cache_creation_tokens', 0),
                        'cache_read_tokens': usage.get('cache_read_tokens', 0),
                        'num_turns': usage.get('num_turns', 0),
                    })
        except:
            pass

    # Load v6 debug
    if v6_debug.exists():
        try:
            with open(v6_debug) as f:
                debug = json.load(f)
                usage = debug.get('usage', {})
                if usage:
                    v6_data.append({
                        'folder': folder_name,
                        'cost': usage.get('cost_usd', 0),
                        'duration_ms': usage.get('duration_ms', 0),
                        'wall_time_ms': usage.get('wall_time_ms', 0),
                        'input_tokens': usage.get('input_tokens', 0),
                        'output_tokens': usage.get('output_tokens', 0),
                        'cache_creation_tokens': usage.get('cache_creation_tokens', 0),
                        'cache_read_tokens': usage.get('cache_read_tokens', 0),
                        'num_turns': usage.get('num_turns', 0),
                    })
        except:
            pass

print(f"Successfully loaded: v5={len(v5_data)}, v6={len(v6_data)}")

if not v5_data or not v6_data:
    print("Error: No data loaded")
    exit(1)

# Calculate statistics
def calc_stats(data, field):
    values = [d[field] for d in data if d[field] > 0]
    if not values:
        return {'mean': 0, 'median': 0, 'min': 0, 'max': 0, 'total': 0}
    return {
        'mean': statistics.mean(values),
        'median': statistics.median(values),
        'min': min(values),
        'max': max(values),
        'total': sum(values),
        'count': len(values)
    }

print("\n" + "=" * 80)
print("COST ANALYSIS (USD)")
print("=" * 80)

v5_cost = calc_stats(v5_data, 'cost')
v6_cost = calc_stats(v6_data, 'cost')

print(f"\n{'Metric':<20} {'v5':<25} {'v6':<25} {'v6/v5 Ratio'}")
print("-" * 80)
print(f"{'Mean cost/paper':<20} ${v5_cost['mean']:.4f}             ${v6_cost['mean']:.4f}             {v6_cost['mean']/v5_cost['mean']:.2f}x")
print(f"{'Median cost/paper':<20} ${v5_cost['median']:.4f}             ${v6_cost['median']:.4f}             {v6_cost['median']/v5_cost['median']:.2f}x")
print(f"{'Min cost/paper':<20} ${v5_cost['min']:.4f}             ${v6_cost['min']:.4f}             {v6_cost['min']/v5_cost['min']:.2f}x")
print(f"{'Max cost/paper':<20} ${v5_cost['max']:.4f}             ${v6_cost['max']:.4f}             {v6_cost['max']/v5_cost['max']:.2f}x")
print(f"{'Total cost':<20} ${v5_cost['total']:.2f}               ${v6_cost['total']:.2f}               {v6_cost['total']/v5_cost['total']:.2f}x")

print("\n" + "=" * 80)
print("TIME ANALYSIS (seconds)")
print("=" * 80)

v5_time = calc_stats(v5_data, 'duration_ms')
v6_time = calc_stats(v6_data, 'duration_ms')

# Convert to seconds
for stat in ['mean', 'median', 'min', 'max', 'total']:
    v5_time[stat] /= 1000
    v6_time[stat] /= 1000

print(f"\n{'Metric':<20} {'v5':<25} {'v6':<25} {'v6/v5 Ratio'}")
print("-" * 80)
print(f"{'Mean time/paper':<20} {v5_time['mean']:.1f}s                  {v6_time['mean']:.1f}s                  {v6_time['mean']/v5_time['mean']:.2f}x")
print(f"{'Median time/paper':<20} {v5_time['median']:.1f}s                  {v6_time['median']:.1f}s                  {v6_time['median']/v5_time['median']:.2f}x")
print(f"{'Min time/paper':<20} {v5_time['min']:.1f}s                  {v6_time['min']:.1f}s                  {v6_time['min']/v5_time['min']:.2f}x")
print(f"{'Max time/paper':<20} {v5_time['max']:.1f}s                  {v6_time['max']:.1f}s                  {v6_time['max']/v5_time['max']:.2f}x")
print(f"{'Total time':<20} {v5_time['total']/60:.1f} min               {v6_time['total']/60:.1f} min               {v6_time['total']/v5_time['total']:.2f}x")

print("\n" + "=" * 80)
print("TOKEN USAGE ANALYSIS")
print("=" * 80)

v5_input = calc_stats(v5_data, 'input_tokens')
v6_input = calc_stats(v6_data, 'input_tokens')
v5_output = calc_stats(v5_data, 'output_tokens')
v6_output = calc_stats(v6_data, 'output_tokens')
v5_cache_read = calc_stats(v5_data, 'cache_read_tokens')
v6_cache_read = calc_stats(v6_data, 'cache_read_tokens')
v5_cache_create = calc_stats(v5_data, 'cache_creation_tokens')
v6_cache_create = calc_stats(v6_data, 'cache_creation_tokens')

print(f"\n{'Token Type':<25} {'v5 Mean':<20} {'v6 Mean':<20} {'v6/v5 Ratio'}")
print("-" * 80)
print(f"{'Input tokens':<25} {v5_input['mean']:>15,.0f}   {v6_input['mean']:>15,.0f}   {v6_input['mean']/v5_input['mean']:>10.2f}x")
print(f"{'Output tokens':<25} {v5_output['mean']:>15,.0f}   {v6_output['mean']:>15,.0f}   {v6_output['mean']/v5_output['mean']:>10.2f}x")
print(f"{'Cache creation tokens':<25} {v5_cache_create['mean']:>15,.0f}   {v6_cache_create['mean']:>15,.0f}   {v6_cache_create['mean']/v5_cache_create['mean']:>10.2f}x")
print(f"{'Cache read tokens':<25} {v5_cache_read['mean']:>15,.0f}   {v6_cache_read['mean']:>15,.0f}   {v6_cache_read['mean']/v5_cache_read['mean']:>10.2f}x")

print("\n" + "=" * 80)
print("TURNS ANALYSIS")
print("=" * 80)

v5_turns = calc_stats(v5_data, 'num_turns')
v6_turns = calc_stats(v6_data, 'num_turns')

print(f"\n{'Metric':<20} {'v5':<25} {'v6':<25} {'v6/v5 Ratio'}")
print("-" * 80)
print(f"{'Mean turns/paper':<20} {v5_turns['mean']:.1f}                   {v6_turns['mean']:.1f}                   {v6_turns['mean']/v5_turns['mean']:.2f}x")
print(f"{'Median turns/paper':<20} {v5_turns['median']:.0f}                     {v6_turns['median']:.0f}                     {v6_turns['median']/v5_turns['median']:.2f}x")

print("\n" + "=" * 80)
print("EFFICIENCY ANALYSIS")
print("=" * 80)

# Cost per replication extracted
v5_replications = 56  # from earlier analysis
v6_replications = 62

print(f"\n{'Metric':<30} {'v5':<20} {'v6':<20} {'v6/v5 Ratio'}")
print("-" * 80)
print(f"{'Total replications extracted':<30} {v5_replications:<20} {v6_replications:<20} {v6_replications/v5_replications:.2f}x")
print(f"{'Cost per replication':<30} ${v5_cost['total']/v5_replications:.4f}          ${v6_cost['total']/v6_replications:.4f}          {(v6_cost['total']/v6_replications)/(v5_cost['total']/v5_replications):.2f}x")
print(f"{'Time per replication (sec)':<30} {v5_time['total']/v5_replications:.1f}               {v6_time['total']/v6_replications:.1f}               {(v6_time['total']/v6_replications)/(v5_time['total']/v5_replications):.2f}x")

print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)

print(f"""
v6 vs v5:
- Cost: {v6_cost['mean']/v5_cost['mean']:.2f}x more expensive per paper (${v6_cost['mean']:.4f} vs ${v5_cost['mean']:.4f})
- Time: {v6_time['mean']/v5_time['mean']:.2f}x slower per paper ({v6_time['mean']:.1f}s vs {v5_time['mean']:.1f}s)
- Turns: {v6_turns['mean']/v5_turns['mean']:.2f}x more API calls per paper ({v6_turns['mean']:.1f} vs {v5_turns['mean']:.1f})
- Output: {v6_output['mean']/v5_output['mean']:.2f}x more output tokens ({v6_output['mean']:.0f} vs {v5_output['mean']:.0f})

BUT:
- Extracts {v6_replications/v5_replications:.2f}x more replications ({v6_replications} vs {v5_replications})
- Has explanations in 88.9% of papers (vs 0%)
- Cost per replication: {(v6_cost['total']/v6_replications)/(v5_cost['total']/v5_replications):.2f}x higher

For {len(v5_data)} papers:
- v5 total: ${v5_cost['total']:.2f} in {v5_time['total']/60:.1f} minutes
- v6 total: ${v6_cost['total']:.2f} in {v6_time['total']/60:.1f} minutes
- Difference: ${v6_cost['total']-v5_cost['total']:.2f} more, {(v6_time['total']-v5_time['total'])/60:.1f} minutes longer
""")

print("\n" + "=" * 80)
print("TOP 5 MOST EXPENSIVE PAPERS (v6)")
print("=" * 80)

v6_sorted = sorted(v6_data, key=lambda x: x['cost'], reverse=True)[:5]
for i, paper in enumerate(v6_sorted, 1):
    print(f"{i}. {paper['folder'][:60]}")
    print(f"   Cost: ${paper['cost']:.4f}, Time: {paper['duration_ms']/1000:.1f}s, Turns: {paper['num_turns']}")
    print()
