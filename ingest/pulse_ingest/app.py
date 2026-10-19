from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterable
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from pulse_ingest import __version__
from pulse_ingest.alerts import DEFAULT_INTERVAL_SECONDS, AlertTracker, RuleEvaluator
from pulse_ingest.auth import extract_bearer_token, is_valid_key, load_api_keys
from pulse_ingest.migrate import database_url
from pulse_ingest.notify import EmailChannel, EmailConfig, Notifier
from pulse_ingest.schemas import AlertRuleIn, MetricBatch
from pulse_ingest.store import AlertRule, HostSummary, PostgresMetricStore, Store, UnknownHostError

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


def _alert_interval() -> float:
    return float(os.environ.get("PULSE_ALERT_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS))


def _notifier() -> Notifier | None:
    email = EmailConfig.from_env()
    if email is None:
        logger.info("Email notifications off (PULSE_SMTP_HOST not set)")
        return None
    logger.info(
        "Email notifications on: alerts go to %s via %s:%d (%s)",
        ", ".join(email.recipients), email.host, email.port, email.security,
    )
    return Notifier([EmailChannel(email)])


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

    # Rule evaluation runs only against the app's own Postgres store, i.e. in
    # a real deployment; API tests that inject a fake store don't start it.
    # Run a single API process: each one would evaluate every rule.
    notifier = _notifier() if owned_store else None
    evaluator = (
        RuleEvaluator(owned_store, AlertTracker(owned_store, notifier), interval=_alert_interval())
        if owned_store
        else None
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if owned_store:
            owned_store.open()
        if notifier:
            notifier.start()
        if evaluator:
            evaluator.start()
        try:
            yield
        finally:
            # Evaluator first, so its last notifications are queued before
            # the notifier drains and stops.
            if evaluator:
                evaluator.stop()
            if notifier:
                notifier.stop()
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
        allow_methods=["GET", "POST", "PUT", "DELETE"],
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

    # --- alert rules (ALERT-05). Like the read endpoints, these need no API
    # key: the dashboard is the client, and it has no credentials of its own.

    @app.get("/rules")
    def list_rules() -> dict:
        try:
            rules = store.list_rules()
        except (psycopg.OperationalError, PoolTimeout):
            raise _database_unavailable()
        return {"rules": [_rule_dict(r) for r in rules]}

    @app.get("/rules/{rule_id}")
    def get_rule(rule_id: int) -> dict:
        try:
            rule = store.get_rule(rule_id)
        except (psycopg.OperationalError, PoolTimeout):
            raise _database_unavailable()
        if rule is None:
            raise _rule_not_found(rule_id)
        return _rule_dict(rule)

    @app.post("/rules", status_code=status.HTTP_201_CREATED)
    def create_rule(rule: AlertRuleIn) -> dict:
        try:
            created = store.create_rule(rule)
        except UnknownHostError as exc:
            raise _unknown_rule_host(exc)
        except (psycopg.OperationalError, PoolTimeout):
            raise _database_unavailable()
        logger.info("Alert rule %d created: %s", created.id, created.name)
        return _rule_dict(created)

    @app.put("/rules/{rule_id}")
    def update_rule(rule_id: int, rule: AlertRuleIn) -> dict:
        try:
            updated = store.update_rule(rule_id, rule)
        except UnknownHostError as exc:
            raise _unknown_rule_host(exc)
        except (psycopg.OperationalError, PoolTimeout):
            raise _database_unavailable()
        if updated is None:
            raise _rule_not_found(rule_id)
        logger.info("Alert rule %d updated: %s", updated.id, updated.name)
        return _rule_dict(updated)

    @app.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_rule(rule_id: int) -> Response:
        try:
            deleted = store.delete_rule(rule_id)
        except (psycopg.OperationalError, PoolTimeout):
            raise _database_unavailable()
        if not deleted:
            raise _rule_not_found(rule_id)
        logger.info("Alert rule %d deleted", rule_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app


def _rule_not_found(rule_id: int) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown rule: {rule_id}")


def _unknown_rule_host(exc: UnknownHostError) -> HTTPException:
    # 422 like any other invalid field, located at ("body", "host") so a
    # form can show it next to the host picker.
    # Plain 422: Starlette renamed this constant, and the new name doesn't
    # exist in every version pyproject.toml allows.
    return HTTPException(
        422,
        detail=[{"loc": ["body", "host"], "msg": f"{exc} (it has never reported)", "type": "unknown_host"}],
    )


def _rule_dict(r: AlertRule) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "metric_name": r.metric_name,
        "operator": r.operator,
        "threshold": r.threshold,
        "duration_seconds": r.duration_seconds,
        "host": r.host,
        "tag_filter": r.tag_filter,
        "enabled": r.enabled,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


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
