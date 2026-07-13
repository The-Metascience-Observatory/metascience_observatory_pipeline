"""Server settings: where runtime state lives, ports, and resource guards.

Stage run-state (logs, pid files, exit codes, batch/tag) lives OUTSIDE Dropbox
at ~/.local/state/mo_pipeline/ so Dropbox never syncs churning log files and
stages survive across API restarts.
"""
from __future__ import annotations

import os
from pathlib import Path

STATE_DIR = Path(os.environ.get(
    "MO_STATE_DIR", Path.home() / ".local" / "state" / "mo_pipeline"))
LOGS_DIR = STATE_DIR / "logs"
PIDS_DIR = STATE_DIR / "pids"
STATE_FILE = STATE_DIR / "state.json"     # current batch + tag

API_PORT = int(os.environ.get("MO_API_PORT", "8090"))

# Resource guards (64 GB box, no swap).
CONVERT_MIN_MEM_GB = 20.0     # refuse pdf4llm convert below this MemAvailable
CONVERT_MAX_WORKERS = 4       # hard cap for convert workers


def ensure_dirs() -> None:
    for d in (STATE_DIR, LOGS_DIR, PIDS_DIR):
        d.mkdir(parents=True, exist_ok=True)
