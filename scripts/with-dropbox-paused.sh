#!/usr/bin/env bash
# Run a command with the Dropbox daemon paused, then restart it afterward
# (even if the command fails). Dropbox racing the .next/node_modules folders
# corrupts builds and installs, so we pause it for local builds.
set -u

is_running() { pgrep -f 'dropbox-lnx' >/dev/null 2>&1; }

DROPBOX_WAS_RUNNING=0
if is_running; then
  DROPBOX_WAS_RUNNING=1
  echo "[with-dropbox-paused] stopping Dropbox..."
  dropbox stop >/dev/null 2>&1 || true
  for _ in $(seq 1 10); do is_running || break; sleep 1; done
  is_running && { pkill -f 'dropbox-lnx' >/dev/null 2>&1 || true; sleep 2; }
fi

restart_dropbox() {
  if [ "$DROPBOX_WAS_RUNNING" = "1" ] && ! is_running; then
    echo "[with-dropbox-paused] restarting Dropbox..."
    dropbox start >/dev/null 2>&1 || true
  fi
}
trap restart_dropbox EXIT

"$@"
