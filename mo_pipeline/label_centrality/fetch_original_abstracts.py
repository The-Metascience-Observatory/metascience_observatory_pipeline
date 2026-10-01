"""Fetch original-paper abstracts from OpenAlex for the pilot sample.

Reads pilot_manifest.json, fetches title/year/abstract for each unique
original DOI (batched OR-filters, ~<=4 requests for 150 rows), reconstructs
plain-text abstracts from abstract_inverted_index, and caches to
abstracts_cache.json.

Falls back to the anonymous/polite pool automatically on a 429 "insufficient
budget" reply (the API key's daily quota is small relative to full pipeline
runs — see 2026-07-13 incident).

Usage:
    python -m mo_pipeline.label_centrality.fetch_original_abstracts
"""
from __future__ import annotations

import json
import time

import requests

from mo_pipeline import config
from mo_pipeline.label_centrality import common
from mo_pipeline.corpus.models import normalize_doi

OPENALEX_BASE = "https://api.openalex.org/works"
BATCH = 50


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for env_file in (config.ENV_FILE,
                     config.WEBSITE_ROOT / ".env.local"):
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def deinvert(inv: dict | None) -> str:
    if not inv:
        return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def get(params: dict, api_key: str | None, mailto: str | None) -> dict:
    merged = dict(params)
    if mailto:
        merged["mailto"] = mailto
    if api_key:
        merged["api_key"] = api_key
    for attempt in range(5):
        r = requests.get(OPENALEX_BASE, params=merged, timeout=60)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429 and api_key and "budget" in r.text.lower():
            print("  API key budget exhausted -> retrying via polite pool")
            merged.pop("api_key", None)
            api_key = None
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(min(2 ** attempt, 20))
            continue
        r.raise_for_status()
    raise RuntimeError(f"OpenAlex failed after retries: {params}")


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", default="pilot")
    args = ap.parse_args()

    manifest = common.Paths(args.run).load_manifest()
    dois = sorted({u["original_doi"] for u in manifest["units"] if u["original_doi"]})
    print(f"{len(dois)} unique original DOIs")

    env = load_env()
    api_key = env.get("OPENALEXAPIKEY")
    mailto = env.get("CONTACT_EMAIL") or config.ENTREZ_EMAIL

    cache: dict[str, dict] = {}
    if common.ABSTRACTS_PATH.exists():
        cache = json.loads(common.ABSTRACTS_PATH.read_text())
    todo = [d for d in dois if d not in cache]

    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        data = get({
            "filter": "doi:" + "|".join(f"https://doi.org/{d}" for d in batch),
            "select": "doi,title,publication_year,abstract_inverted_index",
            "per-page": str(BATCH),
        }, api_key, mailto)
        for work in data.get("results", []):
            doi = normalize_doi(work.get("doi") or "")
            if doi:
                cache[doi] = {
                    "title": work.get("title") or "",
                    "year": work.get("publication_year"),
                    "abstract": deinvert(work.get("abstract_inverted_index")),
                }
        time.sleep(config.OPENALEX_DELAY)
        print(f"  batch {i // BATCH + 1}: cache now {len(cache)}")

    for d in todo:
        cache.setdefault(d, {"title": "", "year": None, "abstract": ""})

    common.ABSTRACTS_PATH.write_text(json.dumps(cache, indent=1, ensure_ascii=False))
    with_abs = sum(1 for d in dois if cache.get(d, {}).get("abstract"))
    print(f"wrote {common.ABSTRACTS_PATH}: {with_abs}/{len(dois)} DOIs have an abstract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
