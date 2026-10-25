#!/usr/bin/env bash
# The whole MVP in one terminal, for live demos and the walkthrough recording
# (OPS-04): Postgres, the ingest API, a local "inbox" that prints every alert
# email, this machine's agent, a 3-host container fleet, and the dashboard.
# Each run starts from an empty demo database, so nothing is left over from a
# rehearsal. Ctrl-C stops everything except Postgres.
#
# Usage: scripts/demo.sh [--no-fleet]
#   --no-fleet                skip the Docker container hosts (this machine only)
# Environment:
#   PULSE_SLACK_WEBHOOK_URL   also post alerts to this (real) Slack webhook
#   PULSE_DEMO_HOSTS          how many container hosts (default 3)
#
# Needs: Docker, ingest/.venv with dev deps, agent/.venv, dashboard/node_modules.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${XDG_RUNTIME_DIR:-/tmp}/pulse-demo-$(id -u)"
DB=pulse_demo
DB_URL="postgresql://pulse:pulse@localhost:5432/$DB"
API_KEY=demo-key
FLEET=1
[ "${1:-}" = "--no-fleet" ] && FLEET=0

PIDS=()
cleanup() {
  trap - INT TERM EXIT
  echo
  echo "Stopping the demo..."
  if [ "$FLEET" = 1 ]; then "$ROOT/scripts/test-fleet.sh" down >/dev/null 2>&1 || true; fi
  # (The +"..." form keeps macOS's bash 3.2 happy when the list is empty.)
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  echo "Stopped. Postgres is still running (scripts/dev-db.sh down to stop it)."
}
trap cleanup INT TERM EXIT

# start NAME DIR COMMAND...: run COMMAND from DIR in the background, logging
# to $RUN_DIR/NAME.log
start() {
  local name="$1" dir="$2"
  shift 2
  (cd "$dir" && exec "$@") > "$RUN_DIR/$name.log" 2>&1 &
  PIDS+=($!)
}

wait_for() {
  for _ in $(seq 120); do
    if curl -fs "$1" > /dev/null 2>&1; then return 0; fi
    sleep 0.5
  done
  echo "$2 didn't come up; its log is in $RUN_DIR" >&2
  exit 1
}

for port in 8000 5173 1025; do
  if (exec 3<> "/dev/tcp/127.0.0.1/$port") 2> /dev/null; then
    echo "Port $port is already in use; stop whatever is running there first." >&2
    exit 1
  fi
done

mkdir -p "$RUN_DIR" && chmod 700 "$RUN_DIR"
"$ROOT/scripts/dev-db.sh" up > /dev/null
docker exec pulse-postgres psql -U pulse -d pulse -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)" -c "CREATE DATABASE $DB" > /dev/null
PULSE_DATABASE_URL="$DB_URL" "$ROOT/ingest/.venv/bin/python" -m pulse_ingest.migrate > /dev/null
echo "Database: fresh $DB"

# -u: unbuffered, or emails only reach the log once a buffer's worth piles up.
start inbox "$ROOT" "$ROOT/ingest/.venv/bin/python" -u -m aiosmtpd -n -l 127.0.0.1:1025

# The containers reach the API through Docker's bridge, so with the fleet it
# listens on every interface; see docs/ops-03-stability-run.md.
api_host=127.0.0.1
if [ "$FLEET" = 1 ]; then api_host=0.0.0.0; fi
start api "$ROOT/ingest" env \
  PULSE_DATABASE_URL="$DB_URL" PULSE_API_KEYS="$API_KEY" PULSE_ALERT_INTERVAL_SECONDS=2 \
  PULSE_SMTP_HOST=127.0.0.1 PULSE_SMTP_PORT=1025 PULSE_SMTP_SECURITY=none \
  PULSE_ALERT_EMAIL_FROM=pulse@localhost PULSE_ALERT_EMAIL_TO=me@localhost \
  PULSE_SLACK_WEBHOOK_URL="${PULSE_SLACK_WEBHOOK_URL:-}" \
  "$ROOT/ingest/.venv/bin/uvicorn" pulse_ingest.app:app --host "$api_host" --port 8000 --no-access-log
wait_for http://127.0.0.1:8000/health "The API"
echo "API: http://localhost:8000 (alerts checked every 2s)"

cat > "$RUN_DIR/agent.yaml" <<EOF
endpoint: http://127.0.0.1:8000/metrics
api_key: $API_KEY
interval_seconds: 2
environment: demo
EOF
start agent "$ROOT" "$ROOT/agent/.venv/bin/pulse-agent" --config "$RUN_DIR/agent.yaml"
echo "Agent: this machine ($(hostname)), every 2s"

if [ "$FLEET" = 1 ]; then
  PULSE_API_KEY="$API_KEY" PULSE_FLEET_ENDPOINT=http://host.docker.internal:8000/metrics PULSE_FLEET_INTERVAL=5 \
    "$ROOT/scripts/test-fleet.sh" up "${PULSE_DEMO_HOSTS:-3}" | sed 's/^/Fleet: /'
fi

# Vite itself rather than through npx, whose child would outlive Ctrl-C.
start dashboard "$ROOT/dashboard" env VITE_API_BASE_URL=http://localhost:8000 \
  ./node_modules/.bin/vite --port 5173 --strictPort
wait_for http://127.0.0.1:5173/ "The dashboard"

cat <<EOF

  Dashboard   http://localhost:5173
  Logs        $RUN_DIR
  Faults      scripts/test-fleet.sh stop|pause|start fleet-2
  CPU load    for i in \$(seq \$(getconf _NPROCESSORS_ONLN)); do yes > /dev/null & done   (stop: pkill yes)

Alert transitions and every email Pulse sends appear below. Ctrl-C stops the demo.

EOF
tail -n +1 -F "$RUN_DIR/api.log" "$RUN_DIR/inbox.log" &
PIDS+=($!)
wait
