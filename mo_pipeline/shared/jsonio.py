"""Crash-safe JSON writes for checkpoints and caches."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def atomic_write_json(path: Path, obj, indent: int = 2) -> None:
    """Write `obj` as JSON via a temp file + rename, so a run killed mid-write
    leaves the previous file intact instead of a truncated checkpoint."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=indent)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
