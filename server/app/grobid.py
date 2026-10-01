"""GROBID's lifecycle, in one place.

Stage 7 cannot run without GROBID at :8070, and until now nothing in this repo
started it or told you honestly whether it was working -- `registry._grobid_up`
reported a bool and the container was left to a human. On 2026-09-03 that cost
hours: the container sat "Up" for an hour while every host-side request timed
out, and two separate sessions independently concluded GROBID had wedged.

It had not. Queried from inside its own network namespace it answered instantly:

    docker run --rm --network container:grobid-crf <any image> \\
      bash -c 'exec 3<>/dev/tcp/127.0.0.1/8070 && \\
               printf "GET /api/isalive HTTP/1.0\\r\\n\\r\\n" >&3 && cat <&3'
    -> HTTP/1.1 200 OK ... true

The fault was docker's host->container published-port path. The DNAT rule was
present and correct; this host has `iptables-nft` active *and* rules in the
legacy backend, with ufw enabled, and the packet died after translation. Hence
`--network host` below: it removes iptables from the path rather than untangling
it, and GROBID then answers from the host in about ten seconds.

Two things lie to you about GROBID's state, which is why `state()` returns three
values rather than a bool:

* `docker ps` reports "unhealthy" for `grobid/grobid:0.8.2-crf` no matter what.
  Its healthcheck runs `python3`, which the image does not contain, so the check
  exits 127 forever. Ignore that column.
* A host-side probe cannot tell a wedged service from a broken bridge. Both look
  like a timeout. `running_not_answering` is the state that names that gap.
"""
from __future__ import annotations

import os
import subprocess
import time
import urllib.request

#: Env-overridable, like every path in `mo_pipeline.config` (invariant 1) -- the
#: container name and image are host-specific and must not be baked into a stage.
GROBID_URL = os.environ.get("MO_GROBID_URL", "http://localhost:8070")
GROBID_CONTAINER = os.environ.get("MO_GROBID_CONTAINER", "grobid-crf")
GROBID_IMAGE = os.environ.get("MO_GROBID_IMAGE", "grobid/grobid:0.8.2-crf")

#: Long enough for a cold start (~10s observed, models preloaded by default),
#: short enough that a stage fails fast rather than hanging a dashboard request.
START_TIMEOUT = float(os.environ.get("MO_GROBID_START_TIMEOUT", "120"))

ANSWERING = "answering"
RUNNING_NOT_ANSWERING = "running_not_answering"
STOPPED = "stopped"


def is_up(timeout: float = 1.0) -> bool:
    """True when GROBID answers /api/isalive with 200.

    The default timeout suits a status probe. A wait loop should pass a longer
    one: a service still loading its models accepts the connection and then
    takes its time.
    """
    try:
        with urllib.request.urlopen(f"{GROBID_URL}/api/isalive", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _docker(*args: str, timeout: float = 15) -> tuple[int, str]:
    """(returncode, stdout) for a docker command. Never raises."""
    try:
        p = subprocess.run(["docker", *args], capture_output=True, text=True,
                           timeout=timeout)
        return p.returncode, (p.stdout or "").strip()
    except Exception:
        return 1, ""


def container_running() -> bool:
    code, out = _docker("inspect", "-f", "{{.State.Running}}", GROBID_CONTAINER)
    return code == 0 and out == "true"


def container_exists() -> bool:
    code, _ = _docker("inspect", "-f", "{{.Id}}", GROBID_CONTAINER)
    return code == 0


def state() -> str:
    """One of ANSWERING / RUNNING_NOT_ANSWERING / STOPPED.

    The middle value is the whole point: a container that is up while the
    service is unreachable is the failure that goes unnoticed, because every
    casual check -- `docker ps`, an open port -- says it is fine.
    """
    if is_up():
        return ANSWERING
    return RUNNING_NOT_ANSWERING if container_running() else STOPPED


def start(timeout: float | None = None) -> str:
    """Bring GROBID up if it is not already, and wait for it to answer.

    Returns the resulting `state()`. Starts an existing container in preference
    to creating one, so a container someone configured by hand is respected.
    Never stops or replaces a running container -- other stages and other
    sessions share this service.
    """
    timeout = START_TIMEOUT if timeout is None else timeout
    if is_up():
        return ANSWERING

    if container_exists():
        if not container_running():
            _docker("start", GROBID_CONTAINER, timeout=60)
    else:
        # --network host: see the module docstring. Publishing a port on this
        #   host produces a container that is reachable from nowhere.
        # --restart unless-stopped: the container this replaced had policy `no`,
        #   so an unrelated `systemctl restart docker` silently killed it and
        #   stage 7 failed hours later for no visible reason.
        # --ulimit core=0: GROBID's own advice; its C++ PDF parser can crash and
        #   dump core inside the container.
        _docker("run", "-d", "--name", GROBID_CONTAINER,
                "--restart", "unless-stopped", "--network", "host",
                "--ulimit", "core=0", "--init", GROBID_IMAGE, timeout=120)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_up(timeout=5):
            return ANSWERING
        time.sleep(2)
    return state()


def stop() -> str:
    """Stop the container. Only ever called explicitly -- never by a stage."""
    _docker("stop", GROBID_CONTAINER, timeout=60)
    return state()


_HINTS = {
    ANSWERING: "",
    RUNNING_NOT_ANSWERING: (
        "  container is up but unreachable — this host's docker port publishing "
        "is broken; recreate it on --network host (see server/app/grobid.py)"),
    STOPPED: "  run `./dev.sh grobid up`",
}


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = sys.argv[1:] if argv is None else argv
    cmd = (argv[0] if argv else "status").lower()
    if cmd == "up":
        result = start()
    elif cmd == "down":
        result = stop()
    elif cmd == "status":
        result = state()
    else:
        print(f"usage: {sys.argv[0]} up|down|status", file=sys.stderr)
        return 2
    print(f"grobid: {result} ({GROBID_URL})")
    hint = _HINTS.get(result, "")
    if hint:
        print(hint)
    return 0 if result == ANSWERING or cmd == "down" else 1


if __name__ == "__main__":
    raise SystemExit(main())
