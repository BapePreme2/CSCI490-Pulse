from __future__ import annotations

from fastapi import FastAPI, status

from pulse_ingest import __version__
from pulse_ingest.schemas import MetricBatch


def create_app() -> FastAPI:
    app = FastAPI(title="Pulse Ingest", version=__version__)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/metrics", status_code=status.HTTP_202_ACCEPTED)
    def ingest_metrics(batch: MetricBatch) -> dict[str, int]:
        return {"accepted": len(batch.metrics)}

    return app


app = create_app()
