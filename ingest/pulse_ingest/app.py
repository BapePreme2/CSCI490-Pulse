from __future__ import annotations

import logging
from collections.abc import Iterable

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from pulse_ingest import __version__
from pulse_ingest.auth import extract_bearer_token, is_valid_key, load_api_keys
from pulse_ingest.schemas import MetricBatch

logger = logging.getLogger(__name__)

AGENT_PATHS = {"/metrics"}


def create_app(api_keys: Iterable[str] | None = None) -> FastAPI:
    keys = frozenset(api_keys) if api_keys is not None else load_api_keys()
    if not keys:
        logger.warning("No API keys configured (PULSE_API_KEYS); all agent requests will be rejected")

    app = FastAPI(title="Pulse Ingest", version=__version__)

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
        return {"accepted": len(batch.metrics)}

    return app


app = create_app()
