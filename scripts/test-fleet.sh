#!/usr/bin/env bash
# A local test fleet (OPS-03): Pulse agents in Docker containers, each one a
# separate host to the ingestion API running on this machine.
#
# Usage: scripts/test-fleet.sh <command>
#   up [N]                start fleet-1..fleet-N (default 3), building the image first
#   down                  stop and remove every fleet container
#   status                list the fleet containers, with restart counts
#   stop|start|pause|unpause|restart NAME
#                         fault injection on one host, e.g. `stop fleet-2`
#   logs NAME             follow one host's agent log
#
# Environment (for `up`):
#   PULSE_API_KEY         required: one of the API's PULSE_API_KEYS
#   PULSE_FLEET_ENDPOINT  default http://host.docker.internal:8000/metrics
#   PULSE_FLEET_INTERVAL  seconds between reports, default 10
#
# The containers reach the API through Docker's bridge, so start the API
# listening there too: uvicorn pulse_ingest.app:app --host 0.0.0.0
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE=pulse-agent:dev
LABEL=pulse.test-fleet
# Holds each host's config, API key included: private to this user.
CONFIG_DIR="${XDG_RUNTIME_DIR:-/tmp}/pulse-test-fleet-$(id -u)"

container() { echo "pulse-$1"; }

up() {
  local count="${1:-3}"
  : "${PULSE_API_KEY:?set PULSE_API_KEY to one of the keys in the API PULSE_API_KEYS}"
  local endpoint="${PULSE_FLEET_ENDPOINT:-http://host.docker.internal:8000/metrics}"
  local interval="${PULSE_FLEET_INTERVAL:-10}"

  # Quiet unless it fails: credential helpers for unrelated registries can
  # print warnings on every build.
  local out
  if ! out="$(docker build -q -t "$IMAGE" "$ROOT/agent" 2>&1)"; then
    echo "$out" >&2
    exit 1
  fi
  mkdir -p "$CONFIG_DIR" && chmod 700 "$CONFIG_DIR"
  for i in $(seq 1 "$count"); do
    local host="fleet-$i" config="$CONFIG_DIR/fleet-$i.yaml"
    printf 'endpoint: %s\napi_key: %s\nhostname: %s\ninterval_seconds: %s\nenvironment: test-fleet\n' \
      "$endpoint" "$PULSE_API_KEY" "$host" "$interval" > "$config"
    # Readable inside the container (the agent runs as its own user there);
    # the directory above keeps it private on this machine.
    chmod 644 "$config"
    docker rm -f "$(container "$host")" >/dev/null 2>&1 || true
    docker run -d --name "$(container "$host")" --hostname "$host" --label "$LABEL=1" \
      --add-host host.docker.internal:host-gateway --restart on-failure \
      -v "$config:/etc/pulse-agent/config.yaml:ro" "$IMAGE" >/dev/null
    echo "started $host -> $endpoint"
  done
}

case "${1:-}" in
  up) up "${2:-3}" ;;
  down)
    ids="$(docker ps -aq --filter "label=$LABEL")"
    if [ -n "$ids" ]; then docker rm -f $ids >/dev/null; fi
    rm -rf "$CONFIG_DIR"
    echo "fleet removed"
    ;;
  status)
    docker ps -a --filter "label=$LABEL" --format '{{.Names}}' | sort | while read -r name; do
      docker inspect -f '{{.Name}}  {{.State.Status}}  restarts={{.RestartCount}}  started={{.State.StartedAt}}' "$name" | sed 's|^/||'
    done
    ;;
  stop|start|pause|unpause|restart)
    docker "$1" "$(container "${2:?which host? e.g. fleet-2}")" >/dev/null && echo "$1 ${2}"
    ;;
  logs) docker logs -f "$(container "${2:?which host? e.g. fleet-2}")" ;;
  *) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
