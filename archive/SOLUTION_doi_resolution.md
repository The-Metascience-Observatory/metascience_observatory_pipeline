# Solution: Automated DOI Resolution for Missing URLs

## Problem
- 40% of replications have no `original_url` (DOI)
- All have complete bibliographic info (title, authors, journal, year)
- `extract.py` doesn't call DOI resolution despite having the data needed

## Solution: Add Post-Processing to extract.py

### Option 1: Immediate Fix (Add to extract_paper function)

After line 356 in `extract.py`, add DOI resolution for missing URLs:

```python
# After injecting replication_url and version (line 355)
from fetch_missing_doi import fetch_metadata_from_title
from fetch_metadata_from_doi import fetch_metadata_from_doi

for rep in data.get("replications", []):
    rep["replication_url"] = replication_url
    rep["version"] = version

    # Resolve missing DOI if we have bibliographic info
    if not rep.get("original_url") or rep["original_url"] == "":
        title = rep.get("original_title", "")
        if title:
            logger.info(f"Resolving DOI for: {title[:60]}...")
            try:
                # Try to fetch DOI from title
                doi_data = fetch_metadata_from_title(title)
                if doi_data and doi_data.get("doi"):
                    doi = doi_data["doi"]
                    # Validate and enrich with full metadata
                    validated = fetch_metadata_from_doi(doi)
                    if validated:
                        rep["original_url"] = f"https://doi.org/{doi}"
                        # Optionally update other fields if better data found
                        if not rep.get("original_journal"):
                            rep["original_journal"] = validated.get("journal", "")
                        if not rep.get("original_year"):
                            rep["original_year"] = validated.get("year", "")
                        logger.info(f"  ✓ Resolved to: {doi}")
                    else:
                        logger.warning(f"  ✗ DOI {doi} failed validation")
                else:
                    logger.warning(f"  ✗ No DOI found")
            except Exception as e:
                logger.warning(f"  ✗ Error resolving DOI: {e}")
```

### Option 2: Batch Post-Processing (Add to collate_results)

After generating the CSV at line 630, add a DOI resolution pass:

```python
def resolve_missing_dois_in_csv(csv_path: Path) -> Path:
    """Post-process CSV to resolve missing DOIs using bibliographic info."""
    from fetch_missing_doi import fetch_metadata_from_title
    from fetch_metadata_from_doi import fetch_metadata_from_doi

    df = pd.read_csv(csv_path)
    resolved_count = 0

    for idx, row in df.iterrows():
        if not row['contains_replications']:
            continue

        # Skip if URL already exists
        if pd.notna(row['original_url']) and row['original_url']:
            continue

        # Try to resolve from title
        title = row.get('original_title', '')
        if not title:
            continue

        try:
            doi_data = fetch_metadata_from_title(title)
            if doi_data and doi_data.get('doi'):
                doi = doi_data['doi']
                validated = fetch_metadata_from_doi(doi)
                if validated:
                    df.at[idx, 'original_url'] = f"https://doi.org/{doi}"
                    resolved_count += 1
                    print(f"Resolved {resolved_count}: {title[:60]} -> {doi}", file=sys.stderr)
        except Exception as e:
            continue

    # Save updated CSV
    df.to_csv(csv_path, index=False)
    print(f"\nResolved {resolved_count} missing DOIs", file=sys.stderr)
    return csv_path

# Call after writing CSV (after line 630)
resolve_missing_dois_in_csv(out_path)
```

### Option 3: Separate Script (Recommended for large batches)

Create a new script `resolve_missing_dois.py` that:
1. Reads the collated CSV
2. Identifies rows with missing `original_url`
3. Calls `fetch_missing_doi.py` for each
4. Updates the CSV with resolved DOIs

This allows:
- Running DOI resolution independently
- Easier error handling and retries
- Better rate limiting for API calls

## Recommendation

**Use Option 2** (Batch Post-Processing) because:
- Doesn't slow down individual extractions
- Batches API calls for efficiency
- Easy to re-run if it fails
- Can add rate limiting/retries
- Separates concerns: extraction vs. enrichment

## Expected Impact

Based on our analysis:
- 114/287 replications (40%) currently missing URLs
- All have sufficient bibliographic info for DOI resolution
- Expected success rate: 70-80% (based on fetch_missing_doi.py multi-API approach)
- Should improve URL coverage from 59% to **85-90%**

## Additional Improvements

1. **Prompt enhancement**: Add instruction to search body.md for DOIs even when not in references.json
2. **Reference matching**: Improve Grep search in references.json to catch variations (author name order, journal abbreviations)
3. **Confidence tracking**: Add field to track DOI resolution source (references.json vs. API resolution)
