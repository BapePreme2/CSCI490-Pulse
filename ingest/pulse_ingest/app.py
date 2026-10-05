from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterable
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from pulse_ingest import __version__
from pulse_ingest.auth import extract_bearer_token, is_valid_key, load_api_keys
from pulse_ingest.migrate import database_url
from pulse_ingest.schemas import MetricBatch
from pulse_ingest.store import HostSummary, PostgresMetricStore, Store

# uvicorn configures only its own loggers (uvicorn/uvicorn.access/uvicorn.error);
# the root logger otherwise has no handler and a default level of WARNING, so
# this app's own INFO-level logs (e.g. new-host registration) would be silently
# dropped under a plain `uvicorn pulse_ingest.app:app` without this. A no-op if
# something else already configured the root logger's handlers.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

logger = logging.getLogger(__name__)

AGENT_PATHS = {"/metrics"}

DEFAULT_HISTORY_RANGE_SECONDS = 3600
MAX_HISTORY_RANGE_SECONDS = 30 * 24 * 3600


def _default_cors_origins() -> list[str]:
    raw = os.environ.get("PULSE_CORS_ORIGINS", "http://localhost:5173")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def create_app(
    api_keys: Iterable[str] | None = None,
    store: Store | None = None,
    cors_origins: Iterable[str] | None = None,
) -> FastAPI:
    keys = frozenset(api_keys) if api_keys is not None else load_api_keys()
    if not keys:
        logger.warning("No API keys configured (PULSE_API_KEYS); all agent requests will be rejected")

    origins = list(cors_origins) if cors_origins is not None else _default_cors_origins()

    owned_store = PostgresMetricStore(database_url()) if store is None else None
    store = store or owned_store

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if owned_store:
            owned_store.open()
        try:
            yield
        finally:
            if owned_store:
                owned_store.close()

    app = FastAPI(title="Pulse Ingest", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def require_api_key(request: Request, call_next):
        if request.url.path.rstrip("/") in AGENT_PATHS:
            token = extract_bearer_token(request.headers.get("authorization"))
            if token is None or not is_valid_key(token, keys):
                return JSONResponse(
                    {"detail": "Invalid or missing API key"},
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    headers={"WWW-Authenticate": "Bearer"},
                )
        return await call_next(request)

    # Added after the auth middleware above so it wraps outermost: Starlette's
    # add_middleware() inserts at the front of the middleware list, so the
    # *last* middleware added ends up outermost. CORS must be outermost so it
    # can directly answer a browser's preflight OPTIONS request before that
    # request ever reaches require_api_key, and so it wraps every response,
    # including 401/404/503 ones, so the browser's JS is allowed to read
    # error bodies too.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/metrics", status_code=status.HTTP_202_ACCEPTED)
    def ingest_metrics(batch: MetricBatch) -> dict[str, int | bool]:
        try:
            result = store.write_batch(batch)
        except (psycopg.OperationalError, PoolTimeout):
            logger.exception("Database unavailable while storing batch from %s", batch.host)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable"
            )
        if result.new_host:
            logger.info("New host registered: %s", batch.host)
        return {"accepted": len(batch.metrics), "stored": result.stored, "new_host": result.new_host}

    @app.get("/hosts")
    def hosts() -> dict:
        try:
            summaries = store.list_hosts()
        except (psycopg.OperationalError, PoolTimeout):
            raise _database_unavailable()
        return {"hosts": [_host_summary_dict(h) for h in summaries]}

    @app.get("/hosts/{host}")
    def host_summary(host: str) -> dict:
        try:
            summary = store.get_host_summary(host)
        except (psycopg.OperationalError, PoolTimeout):
            raise _database_unavailable()
        if summary is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown host: {host}")
        return _host_summary_dict(summary)

    @app.get("/hosts/{host}/metrics/latest")
    def latest_metrics(host: str) -> dict:
        try:
            known = store.host_exists(host)
        except (psycopg.OperationalError, PoolTimeout):
            raise _database_unavailable()
        if not known:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown host: {host}")

        try:
            metrics = store.get_latest(host)
        except (psycopg.OperationalError, PoolTimeout):
            raise _database_unavailable()
        return {
            "host": host,
            "metrics": [
                {"name": m.name, "unit": m.unit, "tags": m.tags, "value": m.value, "timestamp": m.timestamp}
                for m in metrics
            ],
        }

    @app.get("/hosts/{host}/metrics/{name}")
    def metric_history(
        host: str,
        name: str,
        start: float | None = Query(None, description="Unix epoch seconds; defaults to end - 1h"),
        end: float | None = Query(None, description="Unix epoch seconds; defaults to now"),
    ) -> dict:
        try:
            known = store.host_exists(host)
        except (psycopg.OperationalError, PoolTimeout):
            raise _database_unavailable()
        if not known:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown host: {host}")

        end = end if end is not None else time.time()
        start = start if start is not None else end - DEFAULT_HISTORY_RANGE_SECONDS
        if start >= end:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="start must be before end")
        if end - start > MAX_HISTORY_RANGE_SECONDS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"range cannot exceed {MAX_HISTORY_RANGE_SECONDS} seconds",
            )

        try:
            series = store.get_history(host, name, start, end)
        except (psycopg.OperationalError, PoolTimeout):
            raise _database_unavailable()
        return {
            "host": host,
            "name": name,
            "start": start,
            "end": end,
            "series": [
                {
                    "unit": s.unit,
                    "tags": s.tags,
                    "points": [{"timestamp": p.timestamp, "value": p.value} for p in s.points],
                }
                for s in series
            ],
        }

    return app


def _database_unavailable() -> HTTPException:
    logger.exception("Database unavailable")
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable")


def _host_summary_dict(h: HostSummary) -> dict:
    return {
        "hostname": h.hostname,
        "first_seen_at": h.first_seen_at,
        "last_seen_at": h.last_seen_at,
        "status": h.status,
        "cpu_usage": h.cpu_usage,
        "memory_percent": h.memory_percent,
    }


app = create_app()
