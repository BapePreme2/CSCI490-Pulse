#!/usr/bin/env bash
# Manage the local development Postgres for Pulse (dev-only credentials).
# Usage: scripts/dev-db.sh up|down|reset|status|psql
set -euo pipefail

NAME=pulse-postgres
VOLUME=pulse-pgdata
IMAGE=postgres:16

case "${1:-}" in
  up)
    if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
      docker start "$NAME" >/dev/null
    else
      docker run -d --name "$NAME" \
        -e POSTGRES_USER=pulse -e POSTGRES_PASSWORD=pulse -e POSTGRES_DB=pulse \
        -p 5432:5432 -v "$VOLUME":/var/lib/postgresql/data \
        --health-cmd "pg_isready -U pulse -d pulse" --health-interval 2s --health-retries 15 \
        "$IMAGE" >/dev/null
    fi
    until [ "$(docker inspect -f '{{.State.Health.Status}}' "$NAME")" = "healthy" ]; do sleep 1; done
    echo "Postgres ready: postgresql://pulse:pulse@localhost:5432/pulse"
    ;;
  down)   docker stop "$NAME" >/dev/null && echo "stopped" ;;
  reset)  docker rm -f "$NAME" >/dev/null 2>&1 || true; docker volume rm "$VOLUME" >/dev/null 2>&1 || true; echo "removed container and data" ;;
  status) docker ps -a --filter "name=$NAME" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' ;;
  psql)   docker exec -it "$NAME" psql -U pulse -d pulse ;;
  *) echo "usage: $0 up|down|reset|status|psql" >&2; exit 1 ;;
esac
