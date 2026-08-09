#!/usr/bin/env python3
"""Tag replication rows with discipline / subdiscipline from the topic ontology.

Two passes:
  1. Deterministic canonicalization against the ontology (exact discipline
     match, subdiscipline-used-as-discipline correction, small alias map for
     known source-dataset labels).
  2. AI classification via the headless `claude` CLI for rows still missing a
     discipline, or missing a subdiscipline where the ontology has candidates.
     Original-study abstracts are fetched from OpenAlex (cached locally) to
     give the classifier more signal than titles alone.

`field` is not set here: the ingestor's populate_field_from_discipline fills it
from the canonical discipline.

Usage:
  python -m mo_pipeline.ingest.tag_disciplines --input path/to/prep.csv
  python -m mo_pipeline.ingest.tag_disciplines --backfill
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from urllib.parse import quote, unquote

import pandas as pd

from mo_pipeline import config as _cfg
from mo_pipeline.extract.extract import (
    VALID_DISCIPLINES,
    _SUBDISCIPLINE_TO_DISCIPLINE,
    _render_discipline_block,
)
from mo_pipeline.shared.fetch_metadata_from_doi import (
    OPENALEX_API_KEY,
    _request_with_retry,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ABSTRACT_CACHE_PATH = os.path.join(SCRIPT_DIR, "abstract_cache.json")
DATA_DIR = str(_cfg.WEBSITE_DATA_DIR)
VERSION_HISTORY_PATH = str(_cfg.VERSION_HISTORY_PATH)

DISC_CANON = {d.lower(): d for d in VALID_DISCIPLINES}
# Last-wins on duplicate subdiscipline names, matching extract.py's behavior.
SUB_CANON = {s.lower(): (d, s) for s, d in _SUBDISCIPLINE_TO_DISCIPLINE.items()}

# Source-dataset labels that match nothing in the ontology but have a clear home.
ALIAS = {
    "marketing/org behavior": ("psychology", "consumer psychology / marketing"),
    "marketing": ("psychology", "consumer psychology / marketing"),
    "medicine": ("medical fields", ""),
}


def canonicalize(disc_raw, sub_raw):
    """Resolve raw labels to (discipline, subdiscipline, hint).

    hint is the unresolved source label ('' when resolved) — kept so the AI
    pass can use it as context.
    """
    disc = str(disc_raw or "").strip()
    sub = str(sub_raw or "").strip()
    v = disc.lower()

    def valid_sub(d, s):
        for cand in VALID_DISCIPLINES.get(d, []):
            if cand.lower() == s.lower():
                return cand
        return ""

    if v in DISC_CANON:
        d = DISC_CANON[v]
        return d, valid_sub(d, sub), ""
    if v in SUB_CANON:
        d, canonical_sub = SUB_CANON[v]
        return d, valid_sub(d, sub) or canonical_sub, ""
    if v in ALIAS:
        d, aliased_sub = ALIAS[v]
        return d, valid_sub(d, sub) or aliased_sub, ""
    return "", valid_sub_any(sub), disc


def valid_sub_any(sub):
    """Canonical form of a subdiscipline regardless of discipline, else ''."""
    hit = SUB_CANON.get(str(sub or "").strip().lower())
    return hit[1] if hit else ""


def needs_ai(disc, sub):
    if not disc:
        return True
    return not sub and bool(VALID_DISCIPLINES.get(disc))


# ---------------------------------------------------------------------------
# Abstracts (OpenAlex)
# ---------------------------------------------------------------------------

def _load_abstract_cache():
    if os.path.exists(ABSTRACT_CACHE_PATH):
        try:
            with open(ABSTRACT_CACHE_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_abstract_cache(cache):
    with open(ABSTRACT_CACHE_PATH, "w") as f:
        json.dump(cache, f)


def _invert_abstract(inv):
    if not isinstance(inv, dict) or not inv:
        return ""
    positions = {}
    for word, idxs in inv.items():
        for i in idxs:
            positions[i] = word
    return " ".join(positions[i] for i in sorted(positions))


def _doi_from_url(url):
    m = re.search(r"doi\.org/(10\..+)$", str(url or "").strip(), flags=re.I)
    return unquote(m.group(1)).lower().rstrip("/. ") if m else None


def fetch_abstracts(dois):
    """Return {doi: abstract_or_''} using OpenAlex, with a local JSON cache."""
    cache = _load_abstract_cache()
    todo = [d for d in dict.fromkeys(dois) if d and d not in cache]
    if todo:
        print(f"Fetching {len(todo)} abstracts from OpenAlex "
              f"({len(dois) - len(todo)} cached)...")
    fetched = 0
    # DOIs with filter-syntax characters must be fetched individually.
    clean = [d for d in todo if not re.search(r"[|,]", d)]
    dirty = [d for d in todo if re.search(r"[|,]", d)]
    for i in range(0, len(clean), 40):
        chunk = clean[i:i + 40]
        url = ("https://api.openalex.org/works?filter=doi:"
               + "|".join(chunk)
               + "&per-page=50&select=doi,abstract_inverted_index")
        if OPENALEX_API_KEY:
            url += f"&api_key={OPENALEX_API_KEY}"
        r = _request_with_retry(url, timeout=30)
        got = {}
        if r is not None:
            for work in r.json().get("results", []):
                d = _doi_from_url(work.get("doi"))
                if d:
                    got[d] = _invert_abstract(work.get("abstract_inverted_index"))
        for d in chunk:
            cache[d] = got.get(d, "")
        fetched += len(chunk)
        print(f"  {fetched}/{len(todo)}")
        _save_abstract_cache(cache)
        time.sleep(0.15)
    for d in dirty:
        url = (f"https://api.openalex.org/works/https://doi.org/{quote(d, safe='')}"
               f"?select=abstract_inverted_index")
        if OPENALEX_API_KEY:
            url += f"&api_key={OPENALEX_API_KEY}"
        r = _request_with_retry(url, timeout=30)
        cache[d] = _invert_abstract(r.json().get("abstract_inverted_index")) if r is not None else ""
        fetched += 1
        _save_abstract_cache(cache)
        time.sleep(0.15)
    return {d: cache.get(d, "") for d in dois if d}


# ---------------------------------------------------------------------------
# AI classification (headless claude CLI)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_TEMPLATE = """You classify scientific replication studies into a fixed topic ontology.

ONTOLOGY — one discipline per line, allowed subdisciplines in brackets:
{ontology}

For every item in the user message, output:
- "discipline": exactly one discipline name from the ontology, copied verbatim.
- "subdiscipline": one entry from that discipline's bracketed list, copied verbatim, or "" if none fits or the list is empty.

Rules:
- If an item has a non-empty "current_discipline", keep exactly that discipline and only choose its subdiscipline.
- Classify by the topic of the original study (title, abstract, journal), not its methodology.
- "source_label" is the topic label used by the source dataset; treat it as a strong hint.
- Never invent names that are not in the ontology.

Respond with ONLY a JSON array, one object per input item:
[{{"id": 0, "discipline": "...", "subdiscipline": "..."}}, ...]
No markdown fences, no commentary."""


def _extract_json_array(text):
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON array in response: {text[:200]}")
    return json.loads(text[start:end + 1])


def classify_batch(items, model):
    """items: list of dicts with id/current_discipline/source_label/titles/etc.
    Returns {id: (discipline, subdiscipline)} for valid answers."""
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(ontology=_render_discipline_block())
    user_prompt = json.dumps(items, ensure_ascii=False)
    cmd = [
        "claude", "--print",
        "--output-format", "json",
        "--model", model,
        "--system-prompt", system_prompt,
        user_prompt,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {(proc.stderr or proc.stdout)[:500]}")
    envelope = json.loads(proc.stdout)
    answers = _extract_json_array(envelope.get("result", ""))

    by_id = {}
    for ans in answers:
        if not isinstance(ans, dict) or "id" not in ans:
            continue
        disc = DISC_CANON.get(str(ans.get("discipline", "")).strip().lower())
        if not disc:
            continue
        sub_raw = str(ans.get("subdiscipline", "")).strip().lower()
        sub = ""
        for cand in VALID_DISCIPLINES.get(disc, []):
            if cand.lower() == sub_raw:
                sub = cand
                break
        by_id[ans["id"]] = (disc, sub)
    return by_id


def build_item(idx, row, current_disc, hint, abstract):
    def g(col):
        return str(row.get(col, "") or "").strip()
    return {
        "id": idx,
        "current_discipline": current_disc,
        "source_label": hint,
        "original_title": g("original_title")[:300],
        "original_journal": g("original_journal")[:150],
        "replication_title": g("replication_title")[:300],
        "description": g("description")[:400],
        "abstract": (abstract or "")[:900],
    }


def run_ai_pass(df, targets, model, batch_size, provenance_note):
    """targets: {df_index: (current_discipline, hint)}. Mutates df in place.
    Returns (tagged_count, unresolved_indices)."""
    if not targets:
        return 0, 0, []
    dois = {idx: _doi_from_url(df.at[idx, "original_url"]) for idx in targets}
    abstracts = fetch_abstracts([d for d in dois.values() if d])

    pending = list(targets.items())
    tagged = 0
    no_fit = 0
    unresolved = []
    for attempt in range(2):
        next_pending = []
        for i in range(0, len(pending), batch_size):
            chunk = pending[i:i + batch_size]
            items = [
                build_item(idx, df.loc[idx], disc, hint, abstracts.get(dois.get(idx), ""))
                for idx, (disc, hint) in chunk
            ]
            print(f"  classifying batch {i // batch_size + 1} "
                  f"({len(items)} rows, attempt {attempt + 1})...")
            try:
                by_id = classify_batch(items, model)
            except (RuntimeError, ValueError, json.JSONDecodeError,
                    subprocess.TimeoutExpired) as e:
                print(f"    batch failed: {e}")
                by_id = {}
            for idx, (disc, hint) in chunk:
                if idx not in by_id:
                    next_pending.append((idx, (disc, hint)))
                    continue
                new_disc, new_sub = by_id[idx]
                if disc and new_disc != disc:
                    # Constrained rows must keep their discipline; retry.
                    next_pending.append((idx, (disc, hint)))
                    continue
                changed = False
                if not disc:
                    df.at[idx, "discipline"] = new_disc
                    changed = True
                if new_sub:
                    df.at[idx, "subdiscipline"] = new_sub
                    changed = True
                if changed:
                    src = str(df.at[idx, "source"] or "").strip()
                    if provenance_note not in src:
                        df.at[idx, "source"] = (src + "; " if src else "") + provenance_note
                    tagged += 1
                else:
                    # Model kept the constrained discipline but says no
                    # subdiscipline fits — accepted, nothing to write.
                    no_fit += 1
        pending = next_pending
        if not pending:
            break
    unresolved = [idx for idx, _ in pending]
    return tagged, no_fit, unresolved


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def tag_frame(df, model, batch_size, limit=None):
    """Canonicalize + AI-tag a dataframe (must have canonical schema columns)."""
    for col in ("discipline", "subdiscipline", "source"):
        if col not in df.columns:
            df[col] = ""
    df[["discipline", "subdiscipline", "source"]] = (
        df[["discipline", "subdiscipline", "source"]].fillna("")
    )

    deterministic = 0
    targets = {}
    for idx, row in df.iterrows():
        disc, sub, hint = canonicalize(row["discipline"], row["subdiscipline"])
        if disc and (disc != row["discipline"] or sub != row["subdiscipline"]):
            deterministic += 1
        if disc:
            df.at[idx, "discipline"] = disc
            df.at[idx, "subdiscipline"] = sub
        if needs_ai(disc, sub):
            targets[idx] = (disc, hint)
    print(f"Deterministic pass: {deterministic} rows canonicalized; "
          f"{len(targets)} rows need AI classification")

    if limit is not None:
        targets = dict(list(targets.items())[:limit])
        print(f"  --limit: classifying only {len(targets)} rows")

    note = f"topics AI-tagged (claude {model})"
    tagged, no_fit, unresolved = run_ai_pass(df, targets, model, batch_size, note)
    print(f"AI pass: {tagged} rows tagged, {no_fit} no-subdiscipline-fits, "
          f"{len(unresolved)} unresolved")
    if unresolved:
        print(f"  unresolved row indices: {unresolved[:20]}"
              + (" ..." if len(unresolved) > 20 else ""))
    return df


def mode_input(path, model, batch_size, limit):
    df = pd.read_csv(path, dtype=str).fillna("")
    df = tag_frame(df, model, batch_size, limit)
    df.to_csv(path, index=False)
    print(f"✓ Wrote tagged file back to {path}")


def mode_backfill(model, batch_size, limit):
    from mo_pipeline.ingest.data_ingestor import get_latest_master_database

    filename = get_latest_master_database()
    if not filename:
        sys.exit("Could not locate latest master database via version_history.txt")
    path = os.path.join(DATA_DIR, filename)
    df = pd.read_csv(path, dtype=str).fillna("")
    print(f"Backfill: loaded {filename} ({len(df)} rows)")

    targets = {}
    for idx, row in df.iterrows():
        disc = str(row["discipline"]).strip()
        if row["subdiscipline"].strip():
            continue
        if disc in VALID_DISCIPLINES and VALID_DISCIPLINES[disc]:
            targets[idx] = (disc, "")
    print(f"{len(targets)} rows missing subdiscipline with ontology candidates")
    if limit is not None:
        targets = dict(list(targets.items())[:limit])
        print(f"  --limit: classifying only {len(targets)} rows")

    note = f"subdiscipline AI-tagged (claude {model})"
    tagged, no_fit, unresolved = run_ai_pass(df, targets, model, batch_size, note)
    print(f"AI pass: {tagged} rows tagged, {no_fit} no-subdiscipline-fits, "
          f"{len(unresolved)} unresolved")

    if tagged == 0:
        print("Nothing tagged; not writing a new database version.")
        return
    timestamp = datetime.now().strftime("%Y_%m_%d_%H%M%S")
    output_filename = f"replications_database_{timestamp}.csv"
    output_path = os.path.join(DATA_DIR, output_filename)
    df.to_csv(output_path, index=False)
    with open(VERSION_HISTORY_PATH, "r") as f:
        content = f.read()
    with open(VERSION_HISTORY_PATH, "w") as f:
        f.write(content.rstrip() + "\n" + output_filename
                + " # subdiscipline backfill (tag_disciplines.py)\n")
    print(f"✓ Saved {output_path} and updated version_history.txt")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", help="Prep CSV (canonical schema) to tag in place")
    group.add_argument("--backfill", action="store_true",
                       help="Fill subdiscipline gaps in the latest master database")
    parser.add_argument("--model", default="sonnet", help="claude CLI model (default: sonnet)")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--limit", type=int, default=None,
                        help="Only AI-classify the first N target rows (smoke testing)")
    args = parser.parse_args()

    if shutil.which("claude") is None:
        sys.exit("The `claude` CLI is required for the AI classification pass.")

    if args.input:
        mode_input(args.input, args.model, args.batch_size, args.limit)
    else:
        mode_backfill(args.model, args.batch_size, args.limit)


if __name__ == "__main__":
    main()
