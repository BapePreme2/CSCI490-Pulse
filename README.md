# Pulse

A self-built cloud infrastructure monitoring platform for CSCI 490
(Capstone), Fall 2026 — a host agent, an ingestion pipeline, a
threshold-and-duration alerting engine, and a fleet dashboard.

See the semester plan (metrics catalog + week-by-week task breakdown) for
the full scope and schedule.

## Layout

- `agent/` — the host monitoring agent (Python). Collects CPU, load
  average, memory, disk, and network metrics and reports them to the
  ingestion API. See `agent/README.md` to develop it, or
  `agent/DEPLOY.md` to put it on a machine you want monitored.
- `ingest/` — ingestion API + Postgres storage layer. See `ingest/README.md`.
- `dashboard/` — fleet and single-host dashboard (React). See
  `dashboard/README.md`.
- `scripts/dev-db.sh` — starts/stops the local development Postgres (Docker).

Each subdirectory is its own project with its own dependencies.

## AI assistance

This project was developed with assistance from Claude Sonnet 5 (Anthropic),
which was used to plan the work, write code, tests, and documentation, and
debug. The author is responsible for the accuracy, quality, and originality
of the submitted work.
