"""grobid: the three states, and that a stage never stops a shared container.

Run with:  MO_STATE_DIR=/tmp/mo_test python -m pytest server/tests -q
"""
from server.app import grobid


def _fake_docker(calls, running=True, exists=True):
    """Stand in for `docker`, recording what would have been run."""
    def run(*args, timeout=15):
        calls.append(args)
        if args[:2] == ("inspect", "-f"):
            if args[2] == "{{.State.Running}}":
                return (0, "true" if running else "false") if exists else (1, "")
            return (0, "deadbeef") if exists else (1, "")
        return 0, ""
    return run


def test_answering_when_the_service_replies(monkeypatch):
    monkeypatch.setattr(grobid, "is_up", lambda timeout=1.0: True)
    assert grobid.state() == grobid.ANSWERING


def test_running_but_not_answering_is_its_own_state(monkeypatch):
    """The state that cost hours on 2026-09-03: `docker ps` says Up, every
    host-side request times out, and a bool cannot tell you which."""
    calls = []
    monkeypatch.setattr(grobid, "is_up", lambda timeout=1.0: False)
    monkeypatch.setattr(grobid, "_docker", _fake_docker(calls, running=True))

    assert grobid.state() == grobid.RUNNING_NOT_ANSWERING


def test_stopped_when_the_container_is_not_running(monkeypatch):
    calls = []
    monkeypatch.setattr(grobid, "is_up", lambda timeout=1.0: False)
    monkeypatch.setattr(grobid, "_docker", _fake_docker(calls, running=False))

    assert grobid.state() == grobid.STOPPED


def test_start_is_a_no_op_when_it_is_already_answering(monkeypatch):
    """A stage must never restart a container other sessions are using."""
    calls = []
    monkeypatch.setattr(grobid, "is_up", lambda timeout=1.0: True)
    monkeypatch.setattr(grobid, "_docker", _fake_docker(calls))

    assert grobid.start() == grobid.ANSWERING
    assert calls == [], "no docker command should have been issued"


def test_start_starts_an_existing_container_rather_than_creating_one(monkeypatch):
    """A container someone configured by hand is respected, not replaced."""
    calls = []
    seen = {"n": 0}

    def is_up(timeout=1.0):
        seen["n"] += 1
        return seen["n"] > 1          # down on the first check, up after start

    monkeypatch.setattr(grobid, "is_up", is_up)
    monkeypatch.setattr(grobid, "_docker", _fake_docker(calls, running=False, exists=True))

    assert grobid.start(timeout=5) == grobid.ANSWERING
    assert ("start", grobid.GROBID_CONTAINER) in [c[:2] for c in calls]
    assert not any(c[0] == "run" for c in calls), "must not create a second container"


def test_a_missing_container_is_created_on_the_host_network(monkeypatch):
    """Publishing a port on this host yields a container reachable from nowhere;
    --network host removes iptables from the path. See grobid.py's docstring."""
    calls = []
    seen = {"n": 0}

    def is_up(timeout=1.0):
        seen["n"] += 1
        return seen["n"] > 1

    monkeypatch.setattr(grobid, "is_up", is_up)
    monkeypatch.setattr(grobid, "_docker", _fake_docker(calls, exists=False))

    assert grobid.start(timeout=5) == grobid.ANSWERING
    run = next(c for c in calls if c[0] == "run")
    assert "--network" in run and run[run.index("--network") + 1] == "host"
    # restart=no is why an unrelated `systemctl restart docker` killed it before.
    assert run[run.index("--restart") + 1] == "unless-stopped"


def test_start_reports_the_real_state_when_it_never_comes_up(monkeypatch):
    calls = []
    monkeypatch.setattr(grobid, "is_up", lambda timeout=1.0: False)
    monkeypatch.setattr(grobid, "_docker", _fake_docker(calls, running=True))

    assert grobid.start(timeout=0.1) == grobid.RUNNING_NOT_ANSWERING
