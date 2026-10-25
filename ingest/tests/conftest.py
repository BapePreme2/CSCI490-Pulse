import uuid

import psycopg
import pytest
from psycopg import sql

from pulse_ingest.migrate import database_url
# The local SMTP and webhook stand-ins the smoke test uses; test_notify
# imports SmtpSink and start_smtp_server from here too.
from pulse_ingest.smoke import SmtpSink, WebhookSink, start_smtp_server


@pytest.fixture
def scratch_db_url():
    """A throwaway database so tests never touch the dev database."""
    admin_url = database_url()
    name = f"pulse_test_{uuid.uuid4().hex[:8]}"

    try:
        admin = psycopg.connect(admin_url, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable (run scripts/dev-db.sh up)")

    with admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            yield psycopg.conninfo.make_conninfo(admin_url, dbname=name)
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def smtp_sink():
    """A real SMTP server on localhost (no TLS, no auth); its port is `.port`."""
    sink = SmtpSink()
    controller = start_smtp_server(sink)
    sink.port = controller.port
    yield sink
    controller.stop()


@pytest.fixture
def webhook_sink():
    """A real HTTP server on localhost standing in for a Slack webhook (see
    pulse_ingest.smoke.WebhookSink)."""
    sink = WebhookSink()
    sink.start()
    yield sink
    sink.stop()
