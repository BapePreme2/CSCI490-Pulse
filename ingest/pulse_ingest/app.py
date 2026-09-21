from __future__ import annotations

import logging
from collections.abc import Iterable
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from pulse_ingest import __version__
from pulse_ingest.auth import extract_bearer_token, is_valid_key, load_api_keys
from pulse_ingest.migrate import database_url
from pulse_ingest.schemas import MetricBatch
from pulse_ingest.store import MetricStore, PostgresMetricStore

logger = logging.getLogger(__name__)

AGENT_PATHS = {"/metrics"}


def create_app(
    api_keys: Iterable[str] | None = None, store: MetricStore | None = None
) -> FastAPI:
    keys = frozenset(api_keys) if api_keys is not None else load_api_keys()
    if not keys:
        logger.warning("No API keys configured (PULSE_API_KEYS); all agent requests will be rejected")

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

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/metrics", status_code=status.HTTP_202_ACCEPTED)
    def ingest_metrics(batch: MetricBatch) -> dict[str, int]:
        try:
            result = store.write_batch(batch)
        except (psycopg.OperationalError, PoolTimeout):
            logger.exception("Database unavailable while storing batch from %s", batch.host)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable"
            )
        return {"accepted": len(batch.metrics), "stored": result.stored}

    return app


app = create_app()
