"""
Stage process runner: detached subprocesses tracked by PID files.

Stages are launched in their own session (setsid) with stdout/stderr redirected
to a log file and the exit code written to a sentinel file. This means a running
stage survives API restarts, and after a restart we re-attach to it by reading
its PID file. No Redis / job queue — the filesystem is the source of truth.

PID file (JSON) per stage id, at settings.PIDS_DIR/<stage_id>.json:
    {pid, pgid, argv, cwd, log, exit_file, started_at, tag, params}

Liveness = the PID is alive AND /proc/<pid>/cmdline still matches the stage's
launch marker (guards against PID reuse). A finished stage leaves its exit_file;
we read the code from there. A stage that left no exit_file did not finish: it
is `stopped` if stop() was asked to end it, else `unknown` (killed from outside,
machine rebooted). It used to read as `finished`, so a stopped 6-hour run showed green.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

from .settings import PIDS_DIR, LOGS_DIR, ensure_dirs


def _pid_file(stage_id: str) -> Path:
    return PIDS_DIR / f"{stage_id}.json"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _proc_exists(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists()


def _cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="ignore")
    except Exception:
        return ""


def is_running(stage_id: str) -> bool:
    """True iff the stage's PID is alive and still the same process."""
    rec = _read_json(_pid_file(stage_id))
    if not rec:
        return False
    pid = rec.get("pid")
    marker = rec.get("marker", "")
    if not pid or not _proc_exists(pid):
        return False
    # Guard PID reuse: the live process must still carry our launch marker.
    return marker in _cmdline(pid)


def status(stage_id: str) -> dict:
    """Return {state, pid, exit_code, log, started_at, tag, params}."""
    rec = _read_json(_pid_file(stage_id))
    if not rec:
        return {"state": "idle"}
    if is_running(stage_id):
        return {"state": "running", "pid": rec.get("pid"), "log": rec.get("log"),
                "started_at": rec.get("started_at"),
                "tag": rec.get("tag"), "params": rec.get("params", {})}
    # Not running — read exit code sentinel if present.
    exit_code = None
    ef = rec.get("exit_file")
    if ef and Path(ef).exists():
        try:
            exit_code = int(Path(ef).read_text().strip() or "-1")
        except Exception:
            exit_code = -1
    if rec.get("stop_requested"):
        state = "stopped"
    elif exit_code is None:
        state = "unknown"
    else:
        state = "finished" if exit_code == 0 else "failed"
    return {"state": state,
            "exit_code": exit_code, "log": rec.get("log"),
            "started_at": rec.get("started_at"),
            "tag": rec.get("tag"), "params": rec.get("params", {})}


def launch(stage_id: str, argv: list[str], *, cwd: str | None = None,
           tag: str | None = None,
           params: dict | None = None, env: dict | None = None) -> dict:
    """Launch a stage detached. Raises RuntimeError if already running."""
    ensure_dirs()
    if is_running(stage_id):
        raise RuntimeError(f"stage {stage_id} already running")

    ts = time.strftime("%Y%m%d-%H%M%S")
    log = LOGS_DIR / f"{stage_id}.{ts}.log"
    exit_file = LOGS_DIR / f"{stage_id}.{ts}.exit"
    marker = f"MO_STAGE={stage_id}"

    full_env = dict(os.environ)
    full_env["MO_STAGE"] = stage_id
    if env:
        full_env.update(env)

    # start_new_session=True puts the child in its own session/process group, so
    # proc.pid IS the group leader (killpg(pid) reaches the whole tree). The
    # leading ':' no-op embeds the marker into the process cmdline so is_running
    # can distinguish our process from a reused PID. Exit code -> sentinel file.
    inner = " ".join(_shquote(a) for a in argv)
    # The traps record a signal death (130 INT, 143 TERM) in the sentinel; bash
    # runs them once the foreground child returns. SIGKILL cannot be trapped, so
    # status() also consults the stop_requested mark that stop() writes.
    ef = _shquote(str(exit_file))
    wrapped = (f": {marker}; trap 'echo 130 > {ef}; exit 130' INT; "
               f"trap 'echo 143 > {ef}; exit 143' TERM; {inner}; echo $? > {ef}")
    proc = subprocess.Popen(
        ["bash", "-c", wrapped],
        cwd=cwd, env=full_env,
        stdout=open(log, "w"), stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        pgid = os.getpgid(proc.pid)
    except Exception:
        pgid = proc.pid
    rec = {"pid": proc.pid, "pgid": pgid, "argv": argv, "marker": marker,
           "cwd": cwd, "log": str(log), "exit_file": str(exit_file),
           "started_at": ts, "tag": tag, "params": params or {}}
    _pid_file(stage_id).write_text(json.dumps(rec, indent=2))
    return rec


def stop(stage_id: str, force: bool = False) -> dict:
    """Send SIGINT (or SIGKILL if force) to the stage's process group."""
    rec = _read_json(_pid_file(stage_id))
    if not rec or not is_running(stage_id):
        return {"stopped": False, "reason": "not running"}
    pgid = rec.get("pgid") or rec.get("pid")
    sig = signal.SIGKILL if force else signal.SIGINT
    rec["stop_requested"] = {"signal": sig.name, "at": time.strftime("%Y%m%d-%H%M%S")}
    _pid_file(stage_id).write_text(json.dumps(rec, indent=2))
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return {"stopped": False, "reason": "process gone"}
    return {"stopped": True, "signal": sig.name}


def tail_log(stage_id: str, lines: int = 200) -> str:
    rec = _read_json(_pid_file(stage_id))
    if not rec or not rec.get("log") or not Path(rec["log"]).exists():
        return ""
    data = Path(rec["log"]).read_text(errors="ignore").splitlines()
    return "\n".join(data[-lines:])


def _shquote(s: str) -> str:
    import shlex
    return shlex.quote(s)
