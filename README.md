# Pulse

A self-built cloud infrastructure monitoring platform for CSCI 490
(Capstone), Fall 2026 — a host agent, an ingestion pipeline, a
threshold-and-duration alerting engine, and a fleet dashboard.

See the semester plan (metrics catalog + week-by-week task breakdown) for
the full scope and schedule.

## Try it

```bash
scripts/demo.sh        # everything, on a fresh demo database; then open http://localhost:5173
```

It starts Postgres, the ingest API, a local inbox that prints every alert
email, this machine's agent, three container hosts, and the dashboard, and
Ctrl-C stops them. It needs Docker, `ingest/.venv` (with the dev extras),
`agent/.venv`, and `dashboard/node_modules`; each project's README has the
setup. `--no-fleet` skips the containers.

## Layout

- `agent/` — the host monitoring agent (Python). Collects CPU, load
  average, memory, disk, and network metrics and reports them to the
  ingestion API. See `agent/README.md` to develop it, or
  `agent/DEPLOY.md` to put it on a machine you want monitored.
- `ingest/` — ingestion API + Postgres storage layer. See `ingest/README.md`.
- `dashboard/` — fleet and single-host dashboard (React). See
  `dashboard/README.md`.
- `scripts/dev-db.sh` — starts/stops the local development Postgres (Docker).
- `scripts/demo.sh` — the whole MVP in one terminal (above).
- `docs/` — the OPS-03 stability run (`ops-03-stability-run.md`).
- `scripts/test-fleet.sh` — starts a fleet of agents in Docker containers,
  each a separate host, against a local API (`up 3`, `down`, `stop fleet-2`,
  ...). What running the MVP against it found and fixed is in
  `docs/ops-03-stability-run.md`.
- `scripts/smoke-test.sh` — end-to-end check of the whole pipeline (agent →
  API → alert → email + Slack) against a throwaway database; prints one line
  per check. See `ingest/README.md`.

Each subdirectory is its own project with its own dependencies.

## AI assistance

This project was developed with assistance from Claude Sonnet 5 and Claude
Opus 5.5 (Anthropic), which were used to plan the work, write code, tests,
and documentation, and debug. The author is responsible for the accuracy,
quality, and originality of the submitted work.

## Task mapping (Week 8, cross-cutting)

| Task | File(s) |
| --- | --- |
| OPS-04 MVP walkthrough (one-command demo) | `scripts/demo.sh`, `README.md` ("Try it") |

The other Week 8 tasks are mapped in `ingest/README.md`, `agent/README.md`,
and `dashboard/README.md`.
