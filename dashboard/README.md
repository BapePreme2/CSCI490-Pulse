# Pulse Dashboard

Fleet and single-host dashboard. React + TypeScript, built with Vite,
routed with `react-router-dom`.

## Setup

```bash
cd dashboard
npm install
```

## Run

```bash
npm run dev       # http://localhost:5173
```

## Test

```bash
npm test
```

## Type-check + build

```bash
npm run build
```

## Structure

- `src/main.tsx` -- entry point; mounts `<App>` inside `BrowserRouter`.
- `src/App.tsx` -- routes: `/` (fleet overview) and `/hosts/:hostname` (a
  single host's page).
- `src/layout/AppShell.tsx` -- shared header + content area every route
  renders inside.
- `src/routes/FleetOverviewPage.tsx` -- placeholder; the real fleet list is
  built in Week 6 (FLEET-03).
- `src/routes/HostPage.tsx` -- the single-host overview: fetches
  `GET /hosts/{host}/metrics/latest` and renders it as tiles.
- `src/api/client.ts` -- the API client. Reads `VITE_API_BASE_URL` (default
  `http://localhost:8000`); throws `HostNotFoundError` on a 404 and
  `ApiError` for anything else non-2xx or unreachable.
- `src/metrics/select.ts` -- picks entries out of a flat metrics list:
  `findUntagged` (the one series with no tags, e.g. overall CPU) and
  `metricsNamed` (every series under a name, e.g. per-core CPU).
- `src/metrics/format.ts` -- display formatting (`%`, MB/GB, MB/s).
- `src/components/*Tile.tsx` -- one tile per metric domain (CPU, memory,
  disk, network), each reading straight from the flat metrics array via the
  helpers above and rendering its own empty state.
- `src/components/LineChart.tsx` -- a reusable time-series line chart (plain
  SVG, no charting library). Takes one or more `{label, points}` series and
  draws a colored line per series, with a legend once there's more than one.
  `HostPage` uses it for a "CPU usage (last hour)" chart, one line per core,
  via `GET /hosts/{host}/metrics/cpu.usage` and
  `src/metrics/series.ts#toChartSeries` (which turns each series' tags into
  its line's label, e.g. `{core: "0"}` -> `"core 0"`).

The dashboard calls the ingest API directly from the browser (a different
origin in dev), which needs the API's CORS support -- see
`ingest/README.md`'s CORS section. If tiles never load and the browser
console shows a CORS error, check `PULSE_CORS_ORIGINS` on the API side
matches the URL you're loading the dashboard from.

## Notes

- `npm run build` runs `tsc -b` first, which needs Node >=20 per
  `react-router-dom`'s `engines` field. It works fine on Node 18.19 in
  practice (tested here), but if a real incompatibility ever surfaces,
  upgrading Node is the fix, not downgrading the router.
- `npm audit` currently reports vulnerabilities in `vite`/`vitest`/`esbuild`
  (dev-server and test-runner only, not shipped in `dist/`) with fixes only
  available via a major-version bump. Deferred rather than forced into a
  scaffold task; revisit before this ever runs somewhere untrusted can reach
  the dev server.

## Task mapping (Week 5)

| Task | File(s) |
| --- | --- |
| DASH-01 frontend scaffold | this directory: `package.json`, `vite.config.ts`, `src/main.tsx`, `src/App.tsx`, `src/layout/AppShell.tsx`, `src/App.test.tsx` |
| DASH-04 single-host overview page | `src/routes/HostPage.tsx`, `src/api/client.ts`, `src/metrics/`, `src/components/*Tile.tsx` |
| DASH-05 reusable line chart component | `src/components/LineChart.tsx`, `src/metrics/series.ts`, wired into `src/routes/HostPage.tsx` |
