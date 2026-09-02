"""What produced this row? The pipeline's version surface, in one place.

An extraction is only interpretable if you can say which code and which prompt
made it. Four things move independently, so each carries its own version:

    package         `mo_pipeline.__version__`        the pipeline code
    prompt          `prompts/version.txt`            the extraction prompt family
    matcher prompt  `prompts/version_match.txt`      the benchmark's LLM judge
    harness         `benchmarking.harness.HARNESS_VERSION`   the evaluator

**A git commit alone is not enough here.** This repo is routinely edited by
several sessions at once and is often left uncommitted for a day, so a run's
HEAD commit can predate the code that actually ran: every extraction on
2026-09-02 recorded commit 6774905, which contains none of that day's work.
Provenance therefore records content hashes as well as the commit --
`prompt_sha256` pins the exact prompt text and `code_fingerprint()` pins the
extractor sources -- so a run made on a dirty tree stays reconstructible.

    python -m mo_pipeline.version     # print the manifest and check for drift
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from mo_pipeline import __version__
from mo_pipeline import config

#: Sources whose content changes what an extraction produces. A change to any of
#: them changes `code_fingerprint()`, which is what identifies a run whose tree
#: was dirty. Deliberately not the whole package: dashboard, corpus maintenance
#: and discover stages do not alter an extraction's output.
EXTRACT_SOURCES = (
    "mo_pipeline/extract/extract.py",
    "mo_pipeline/extract/extract_core.py",
    "mo_pipeline/config.py",
    "mo_pipeline/corpus/models.py",
    "mo_pipeline/discover/screening_backend.py",
    "mo_pipeline/shared/fetch_metadata_from_doi.py",
    "mo_pipeline/shared/fetch_metadata_from_title.py",
)

PROMPT_CHANGELOG = config.PROMPTS_DIR / "CHANGELOG.md"
_CACHE: dict = {}


def sha256_file(path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              timeout=15, cwd=config.REPO_ROOT).stdout.strip()
    except Exception:
        return ""


def prompt_file_sha256() -> dict:
    """{prompt file name -> sha256}: the shared core plus every mode file."""
    files = {"prompt_shared_core.md": config.PROMPT_SHARED_CORE,
             **{p.name: p for p in config.PROMPT_FILES.values()}}
    return {name: sha256_file(p) for name, p in sorted(files.items())}


def code_fingerprint() -> str:
    """Short stable hash of the extractor sources, for dirty-tree runs.

    Hashes each file's own digest with its path, so a moved file is a different
    fingerprint and the order of `EXTRACT_SOURCES` cannot change the answer.
    """
    if "code" not in _CACHE:
        blob = "".join(f"{rel}:{sha256_file(config.REPO_ROOT / rel)}\n"
                       for rel in sorted(EXTRACT_SOURCES))
        _CACHE["code"] = hashlib.sha256(blob.encode()).hexdigest()[:16]
    return _CACHE["code"]


def _read_version(path: Path, default: str = "") -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return default


def manifest() -> dict:
    """Everything needed to identify the code and prompts behind a run.

    Cached per process: the hashes cannot change while a batch is running, and
    a batch calls this once per paper.
    """
    if "manifest" not in _CACHE:
        _CACHE["manifest"] = {
            "pipeline_version": __version__,
            "prompt_version": _read_version(config.EXTRACTOR_VERSION_FILE),
            "code_fingerprint": code_fingerprint(),
            "git_commit": _git("rev-parse", "HEAD"),
            "git_dirty_pipeline": bool(_git("status", "--porcelain", "mo_pipeline/")),
            "git_dirty_prompts": bool(_git("status", "--porcelain", "prompts/")),
        }
    return dict(_CACHE["manifest"])


# ── changelog drift check ────────────────────────────────────────────────────

def changelog_shas(version: str) -> dict:
    """{file -> sha256} recorded in prompts/CHANGELOG.md for one prompt version.

    The changelog records a `| file | sha256 |` table per version, so editing a
    prompt without bumping `version.txt` is detectable rather than silent.
    """
    try:
        text = PROMPT_CHANGELOG.read_text()
    except OSError:
        return {}
    out, in_section = {}, False
    for line in text.splitlines():
        if line.startswith("## "):
            in_section = line.startswith(f"## Prompt {version} ")
            continue
        if in_section and line.startswith("| prompt_") and "|" in line[1:]:
            parts = [c.strip() for c in line.strip("|").split("|")]
            if len(parts) >= 2 and len(parts[1]) == 64:
                out[parts[0]] = parts[1]
    return out


def prompt_drift() -> list[str]:
    """Prompt files whose content no longer matches the changelog entry for the
    current version. Empty when they agree, or when nothing is recorded yet."""
    version = _read_version(config.EXTRACTOR_VERSION_FILE)
    recorded = changelog_shas(version)
    if not recorded:
        return []
    now = prompt_file_sha256()
    return sorted(name for name, sha in recorded.items() if now.get(name, "") != sha)


def _self_check() -> int:
    m = manifest()
    print("Versions")
    print(f"  pipeline           {m['pipeline_version']}   (mo_pipeline.__version__)")
    print(f"  extraction prompt  {m['prompt_version']}   (prompts/version.txt)")
    print(f"  matcher prompt     {_read_version(config.MATCH_VERSION_FILE, '?')}   (prompts/version_match.txt)")
    print(f"  centrality prompt  {_read_version(config.PROMPTS_DIR / 'version_centrality.txt', '?')}")
    try:
        import sys
        sys.path.insert(0, str(config.BENCHMARKING_DIR))
        from harness import HARNESS_VERSION
        print(f"  benchmark harness  {HARNESS_VERSION}   (benchmarking/harness.py)")
    except Exception as e:
        print(f"  benchmark harness  unavailable ({type(e).__name__})")
    print("\nCode identity")
    print(f"  code_fingerprint   {m['code_fingerprint']}   ({len(EXTRACT_SOURCES)} extractor sources)")
    print(f"  git_commit         {m['git_commit'][:10] or '(none)'}")
    dirty = [a for a in ("pipeline", "prompts") if m[f"git_dirty_{a}"]]
    print(f"  git dirty          {', '.join(dirty) if dirty else 'no'}")
    if dirty:
        print("    ! The commit above does NOT describe what would run now. Rows produced")
        print("    ! while dirty are identified by code_fingerprint and prompt_sha256 instead.")
    drift = prompt_drift()
    print("\nPrompt changelog")
    if not PROMPT_CHANGELOG.exists():
        print(f"  ! {PROMPT_CHANGELOG} is missing")
        return 1
    if not changelog_shas(m["prompt_version"]):
        print(f"  ! no entry for prompt {m['prompt_version']} — add one before running a benchmark")
        return 1
    if drift:
        print(f"  ! {len(drift)} file(s) changed since {m['prompt_version']} was recorded: {', '.join(drift)}")
        print(f"  ! bump prompts/version.txt and add a CHANGELOG entry, or restore the files")
        return 1
    print(f"  prompt {m['prompt_version']} matches the recorded hashes")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_check())
