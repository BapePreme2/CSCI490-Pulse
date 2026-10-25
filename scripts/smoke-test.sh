#!/usr/bin/env bash
# End-to-end smoke test (OPS-02): agent -> ingest API -> alert -> email + Slack,
# against a throwaway database. Needs the dev Postgres (scripts/dev-db.sh up)
# and both virtualenvs (ingest/.venv with dev deps, agent/.venv).
# Usage: scripts/smoke-test.sh [--timeout SECONDS] [--agent PATH]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/ingest/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "ingest/.venv not found; set it up first (see ingest/README.md)" >&2
  exit 2
fi
exec "$PYTHON" -m pulse_ingest.smoke "$@"
