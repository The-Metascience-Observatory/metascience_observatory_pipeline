"""Shared pipeline state: the current extraction tag, persisted to disk."""
from __future__ import annotations

import json

from .settings import STATE_FILE, ensure_dirs


def load() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"tag": None}


def save(patch: dict) -> dict:
    ensure_dirs()
    cur = load()
    cur.update({k: v for k, v in patch.items() if v is not None})
    STATE_FILE.write_text(json.dumps(cur, indent=2))
    return cur
