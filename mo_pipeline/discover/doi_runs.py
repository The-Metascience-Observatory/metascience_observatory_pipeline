"""
Named DOI-list runs: feed an arbitrary CSV of DOIs straight into the
download → convert → extract stages, bypassing search/dedup/prefilter/classify.

A run lives at config.DOI_RUNS_DIR/<slug>/ :
  dois.csv          one normalized DOI per row (header: doi) — download --doi-csv input
  include_list.txt  paper folder names (doi_to_folder) — extract --include-list input
  meta.json         name, created, source, counts

The CSV parser auto-detects which column holds the replication DOIs (bare DOIs
or doi.org URLs), preferring columns named replication_doi > doi >
replication_url. A pasted plain list of DOIs parses as a single-column CSV, so
the same path handles both.
"""
from __future__ import annotations

import csv
import io
import json
import re
import shutil
import time
from pathlib import Path

from mo_pipeline import config
from mo_pipeline.corpus.models import doi_to_folder

csv.field_size_limit(2**31 - 1)

_DOI_RE = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)
_PREFERRED_COLUMNS = ["replication_doi", "doi", "replication_url", "doi_url", "url"]
_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def normalize_doi(value: str) -> str | None:
    """'https://doi.org/10.1037/A0025140.' -> '10.1037/a0025140' (None if not a DOI)."""
    if not value:
        return None
    v = value.strip().lower()
    v = re.sub(r"^https?://(dx\.)?doi\.org/", "", v)
    m = _DOI_RE.match(v)
    if not m:
        return None
    return m.group(0).rstrip(".,;)]}>\"'")


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug[:64]


def extract_dois_from_text(text: str) -> dict:
    """Parse CSV/plain text, pick the column with the most DOIs, return the list.

    Returns {dois, column, n_rows, n_invalid}. Raises ValueError if no DOIs found.
    """
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("input is empty")

    width = max(len(r) for r in rows)
    counts = [0] * width
    for r in rows:
        for i, cell in enumerate(r):
            if normalize_doi(cell):
                counts[i] += 1
    if not any(counts):
        raise ValueError("no DOIs found in any column")

    # Header names (only meaningful if the first row isn't itself DOIs).
    header = [c.strip().lower() for c in rows[0]]
    has_header = not any(normalize_doi(c) for c in rows[0])

    def rank(i: int) -> tuple:
        name = header[i] if has_header and i < len(header) else ""
        pref = _PREFERRED_COLUMNS.index(name) if name in _PREFERRED_COLUMNS else len(_PREFERRED_COLUMNS)
        return (pref, -counts[i])

    best = min(range(width), key=rank)
    data_rows = rows[1:] if has_header else rows

    dois, seen, invalid = [], set(), 0
    for r in data_rows:
        cell = r[best] if best < len(r) else ""
        d = normalize_doi(cell)
        if d is None:
            if cell.strip():
                invalid += 1
            continue
        if d not in seen:
            seen.add(d)
            dois.append(d)
    if not dois:
        raise ValueError("no DOIs found in the selected column")

    column = header[best] if has_header else f"column {best + 1}"
    return {"dois": dois, "column": column, "n_rows": len(data_rows), "n_invalid": invalid}


# ── run CRUD ─────────────────────────────────────────────────────────────────
def _run_dir(slug: str) -> Path:
    # Every slug that reaches here from the API is untrusted: only a canonical
    # slug (slugify's alphabet, so no '.' or '/') names a run, so '..' or any
    # other path segment can never resolve outside DOI_RUNS_DIR.
    if not re.fullmatch(r"[a-z0-9_-]{1,64}", slug or ""):
        raise FileNotFoundError(f"no such run: {slug!r}")
    return config.DOI_RUNS_DIR / slug


def create_run(name: str, csv_text: str | None = None, source_path: str | None = None) -> dict:
    """Create a run from pasted/uploaded CSV text or a server-side CSV path."""
    slug = slugify(name)
    if not slug:
        raise ValueError("run name must contain letters or digits")
    rd = _run_dir(slug)
    if rd.exists():
        raise FileExistsError(f"run '{slug}' already exists")

    if csv_text is None:
        if not source_path:
            raise ValueError("provide csv_text or source_path")
        p = Path(source_path).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"no such file: {p}")
        csv_text = p.read_text(errors="replace")

    parsed = extract_dois_from_text(csv_text)
    dois = parsed["dois"]

    rd.mkdir(parents=True)
    with open(rd / "dois.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doi"])
        w.writerows([d] for d in dois)
    (rd / "include_list.txt").write_text(
        "\n".join(doi_to_folder(d) for d in dois) + "\n")
    meta = {
        "name": name.strip(), "slug": slug,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source_path or "pasted/uploaded CSV",
        "doi_column": parsed["column"],
        "n_dois": len(dois), "n_rows": parsed["n_rows"], "n_invalid": parsed["n_invalid"],
    }
    (rd / "meta.json").write_text(json.dumps(meta, indent=2))
    return meta


def delete_run(slug: str) -> None:
    rd = _run_dir(slug)
    if not (rd.is_dir() and (rd / "meta.json").exists()):
        raise FileNotFoundError(f"no such run: {slug}")
    shutil.rmtree(rd)


def list_runs() -> list[dict]:
    if not config.DOI_RUNS_DIR.exists():
        return []
    runs = []
    for rd in sorted(config.DOI_RUNS_DIR.iterdir()):
        meta = rd / "meta.json"
        if meta.exists():
            try:
                runs.append(json.loads(meta.read_text()))
            except Exception:
                continue
    runs.sort(key=lambda m: m.get("created", ""), reverse=True)
    return runs


def load_dois(slug: str) -> list[str]:
    path = _run_dir(slug) / "dois.csv"
    with open(path, newline="") as f:
        return [r["doi"] for r in csv.DictReader(f) if r.get("doi")]


# ── status probe (cheap: catalog query + one inbox listdir, no papers/ walk) ─
def run_status(slug: str) -> dict:
    dois = load_dois(slug)
    folders = [doi_to_folder(d) for d in dois]
    folder_set = set(folders)

    in_corpus = extracted = converted = 0
    tag_needle = f",{slug},"
    if config.CATALOG_PATH.exists():
        from mo_pipeline.corpus import catalog
        conn = catalog.connect()
        try:
            # The catalog's folder column holds absolute paths; join on the
            # DOI primary key instead (load_dois returns normalized lowercase).
            for chunk_start in range(0, len(dois), 500):
                chunk = dois[chunk_start:chunk_start + 500]
                q = ",".join("?" * len(chunk))
                for doi, status, tags in conn.execute(
                        f"SELECT doi, status, tags FROM papers WHERE lower(doi) IN ({q})", chunk):
                    in_corpus += 1
                    if status in ("converted", "screened", "extracted", "ingested"):
                        converted += 1
                    if tag_needle in f",{tags or ''},":
                        extracted += 1
        finally:
            conn.close()

    inbox_pending = 0
    if config.INBOX_DIR.exists():
        # One folder per record (inbox/{doi}/{doi}.pdf); the root glob covers
        # flat leftovers from before `corpus inbox-subfolders`.
        inbox_stems = {p.stem.lower() for p in config.INBOX_DIR.glob("*/*.pdf")}
        inbox_stems |= {p.stem.lower() for p in config.INBOX_DIR.glob("*.pdf")}
        inbox_pending = sum(1 for fo in folder_set if fo.lower() in inbox_stems)

    collated = config.PAPERS_DIR / f"collated_results_{slug}.csv"
    return {
        "n_dois": len(dois),
        "in_corpus": in_corpus,
        "converted": converted,
        "extracted_with_tag": extracted,
        "inbox_pending": inbox_pending,
        "missing": len(dois) - in_corpus - inbox_pending,
        "collated_csv": str(collated) if collated.exists() else None,
        "paths": {
            "dois_csv": str(_run_dir(slug) / "dois.csv"),
            "include_list": str(_run_dir(slug) / "include_list.txt"),
        },
    }
