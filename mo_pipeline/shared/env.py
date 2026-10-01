"""API keys and contact settings from the environment or a `.env.local` file.

The process environment wins; otherwise the key is read from the repo's
`.env.local` (config.ENV_FILE), then from any extra files a caller names. This
is the one loader: a hand-rolled copy once resolved the file from the package
directory instead of the repo root, so every key silently came back None and
every OpenAlex call ran anonymous (HTTP 429 once OpenAlex moved to budgets).
"""
from __future__ import annotations

import os
from pathlib import Path

from mo_pipeline import config


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=value pairs from one dotenv file ({} if missing); quotes stripped."""
    out: dict[str, str] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return out


def env_key(name: str, *extra_files: Path) -> str | None:
    """`name` from the environment, else config.ENV_FILE, else `extra_files`."""
    if os.environ.get(name):
        return os.environ[name]
    for path in (config.ENV_FILE, *extra_files):
        val = read_env_file(path).get(name)
        if val:
            return val
    return None
