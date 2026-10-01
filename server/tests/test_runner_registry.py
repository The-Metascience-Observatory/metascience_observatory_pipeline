"""Regression tests for the runner lifecycle, probes, and mutex logic.

Run: cd mo_pipeline && python -m pytest server/tests -q   (conftest.py isolates state)
"""
import time

from server.app import runner, registry
from server.app.main import _running_mutex_groups


def test_runner_lifecycle():
    sid = "pytest_fake"
    runner.launch(sid, ["bash", "-c", "echo hi; sleep 10"])
    time.sleep(0.6)
    assert runner.is_running(sid)
    assert runner.status(sid)["state"] == "running"
    assert "hi" in runner.tail_log(sid, 5)
    assert runner.stop(sid)["stopped"] is True
    time.sleep(1.0)
    assert not runner.is_running(sid)
    assert runner.status(sid)["state"] == "stopped"


def test_pid_reuse_guard():
    # A pidfile pointing at a live process WITHOUT our marker is not "running".
    sid = "pytest_marker"
    runner.launch(sid, ["bash", "-c", "sleep 5"])
    time.sleep(0.3)
    rec = runner._read_json(runner._pid_file(sid))
    assert rec["marker"] in runner._cmdline(rec["pid"])
    runner.stop(sid)


def test_claude_cli_mutex():
    runner.launch("extract", ["bash", "-c", "sleep 8"])
    time.sleep(0.4)
    try:
        active = _running_mutex_groups(exclude="classify")
        assert active.get("claude_cli") == "extract"
        assert "self" not in active  # self is a per-stage singleton, not shared
        classify = registry.BY_ID["classify"]
        assert "claude_cli" in [g for g in classify.mutex_groups if g in active]
        # dedup only shares heavy_ram, not claude_cli
        dedup = registry.BY_ID["dedup"]
        assert not [g for g in dedup.mutex_groups if g in active]
    finally:
        runner.stop("extract")


def test_state_dir_is_isolated():
    from server.app import settings
    assert "mo_server_tests_" in str(settings.STATE_DIR)


def test_finished_and_failed_come_from_the_exit_code():
    runner.launch("pytest_ok", ["bash", "-c", "true"])
    runner.launch("pytest_bad", ["bash", "-c", "exit 3"])
    time.sleep(0.6)
    assert runner.status("pytest_ok")["state"] == "finished"
    st = runner.status("pytest_bad")
    assert st["state"] == "failed" and st["exit_code"] == 3


def test_killed_from_outside_is_not_finished():
    import os, signal
    sid = "pytest_killed"
    rec = runner.launch(sid, ["bash", "-c", "sleep 10"])
    time.sleep(0.4)
    os.killpg(rec["pgid"], signal.SIGKILL)   # no exit file, no stop() mark
    time.sleep(0.6)
    assert runner.status(sid)["state"] == "unknown"


def test_download_and_convert_share_the_inbox_lock():
    shared = set(registry.BY_ID["download"].mutex_groups) & set(registry.BY_ID["convert"].mutex_groups)
    assert "inbox" in shared


def test_doi_run_slugs_cannot_leave_the_runs_dir():
    import pytest
    from mo_pipeline.discover import doi_runs
    for bad in ("..", ".", "a/b", "", "../x", "A"):
        with pytest.raises(FileNotFoundError):
            doi_runs.delete_run(bad)


def test_all_probes_return_state(monkeypatch):
    from server.app import grobid
    monkeypatch.setattr(grobid, "state", lambda *a, **k: grobid.ANSWERING)
    for s in registry.STAGES:
        pr = s.probe({}) if s.probe else {}
        assert "state" in pr, f"{s.id} probe missing state"
