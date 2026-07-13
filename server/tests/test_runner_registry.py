"""Regression tests for the runner lifecycle, probes, and mutex logic.

Run: cd mo_pipeline && MO_STATE_DIR=/tmp/mo_test python -m pytest server/tests -q
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
    assert runner.status(sid)["state"] in ("finished", "failed")


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


def test_all_probes_return_state():
    for s in registry.STAGES:
        pr = s.probe({}) if s.probe else {}
        assert "state" in pr, f"{s.id} probe missing state"
