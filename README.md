# Pulse

A self-built cloud infrastructure monitoring platform for CSCI 490
(Capstone), Fall 2026 — a host agent, an ingestion pipeline, a
threshold-and-duration alerting engine, and a fleet dashboard.

See the semester plan (metrics catalog + week-by-week task breakdown) for
the full scope and schedule.

## Layout

- `agent/` — the host monitoring agent (Python). Collects CPU, load
  average, memory, disk, and network metrics and reports them to the
  ingestion API. See `agent/README.md`.
- `ingest/` — ingestion API + storage (planned, Week 4).
- `dashboard/` — fleet dashboard (planned, Week 5).

Each subdirectory is its own project with its own dependencies.
