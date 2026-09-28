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
- `src/routes/` -- one file per route. Both are placeholders for now; the
  real content is built in later Week 5/6 tasks (see below).

No API client exists yet -- that lands with DASH-04, which is the first
page that actually reads from the ingest API's `/hosts/{host}/metrics/...`
endpoints.

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
