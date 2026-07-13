#!/usr/bin/env bash
# Lifecycle for the MO pipeline dashboard: FastAPI (api, :8090) + Next.js (dashboard, :3010).
#
#   ./dev.sh up        start api + dashboard
#   ./dev.sh down      stop api + dashboard (NEVER touches running pipeline stages)
#   ./dev.sh restart   down then up
#   ./dev.sh status    show api/dashboard + any running stages
#   ./dev.sh logs [api|dashboard]   tail a service log
#
# Running pipeline stages are deliberately detached (see server/app/runner.py):
# they live under ~/.local/state/mo_pipeline and survive api restarts, so `down`
# stops only the api/dashboard services, never the work.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="$ROOT/.run"; mkdir -p "$RUN"
API_PORT="${MO_API_PORT:-8090}"
DASH_PORT="${MO_DASH_PORT:-3010}"
# Keep Next.js build cache out of Dropbox.
export NEXT_DIST_DIR="${NEXT_DIST_DIR:-$HOME/.cache/mo_pipeline/next}"

_pid_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }
_port_serving() { curl -sf -m 2 "http://127.0.0.1:$1/" >/dev/null 2>&1; }

start_api() {
  if _pid_alive "$(cat "$RUN/api.pid" 2>/dev/null)"; then echo "api already up (managed)"; return; fi
  if _port_serving "$API_PORT"; then echo "api already up on :$API_PORT (external) — leaving it"; return; fi
  echo "starting api on :$API_PORT"
  ( cd "$ROOT" && nohup python -m uvicorn server.app.main:app \
      --host 127.0.0.1 --port "$API_PORT" > "$RUN/api.log" 2>&1 & echo $! > "$RUN/api.pid" )
}

start_dashboard() {
  if _pid_alive "$(cat "$RUN/dashboard.pid" 2>/dev/null)"; then echo "dashboard already up (managed)"; return; fi
  # Tolerate a dashboard you started by hand (npm run dev) — don't collide on the port.
  if _port_serving "$DASH_PORT"; then echo "dashboard already up on :$DASH_PORT (external) — leaving it"; return; fi
  if [ ! -d "$ROOT/dashboard/node_modules" ]; then
    echo "installing dashboard deps (Dropbox paused)..."
    ( cd "$ROOT/dashboard" && "$ROOT/scripts/with-dropbox-paused.sh" npm install )
  fi
  echo "starting dashboard on :$DASH_PORT"
  ( cd "$ROOT/dashboard" && MO_API_PORT="$API_PORT" nohup npm run dev -- --port "$DASH_PORT" \
      > "$RUN/dashboard.log" 2>&1 & echo $! > "$RUN/dashboard.pid" )
}

stop_svc() {
  local name="$1"; local pid; pid="$(cat "$RUN/$name.pid" 2>/dev/null || true)"
  if _pid_alive "$pid"; then echo "stopping $name ($pid)"; kill "$pid" 2>/dev/null || true; fi
  rm -f "$RUN/$name.pid"
}

case "${1:-status}" in
  up)      start_api; start_dashboard; echo "→ http://localhost:$DASH_PORT" ;;
  down)    stop_svc dashboard; stop_svc api ;;
  restart) stop_svc dashboard; stop_svc api; sleep 1; start_api; start_dashboard ;;
  status)
    for s in api dashboard; do
      pid="$(cat "$RUN/$s.pid" 2>/dev/null || true)"
      if _pid_alive "$pid"; then echo "$s: UP (pid $pid)"; else echo "$s: down"; fi
    done
    echo "--- running pipeline stages ---"
    ls "$HOME/.local/state/mo_pipeline/pids/" 2>/dev/null | sed 's/\.json$//' | while read -r sid; do
      [ -n "$sid" ] && ( cd "$ROOT" && python -c "from server.app import runner; \
        print('  ', '$sid', runner.status('$sid')['state'])" 2>/dev/null )
    done ;;
  logs)    tail -f "$RUN/${2:-api}.log" ;;
  *) echo "usage: $0 {up|down|restart|status|logs [api|dashboard]}"; exit 1 ;;
esac
