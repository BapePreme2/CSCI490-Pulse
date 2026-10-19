import email
import email.policy
import socket
import uuid

import psycopg
import pytest
from aiosmtpd.controller import Controller
from psycopg import sql

from pulse_ingest.migrate import database_url


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


class SmtpSink:
    """Handler for a real local SMTP server (aiosmtpd) that keeps every
    message it accepts."""

    def __init__(self):
        self.envelopes = []

    async def handle_DATA(self, server, session, envelope):
        self.envelopes.append(envelope)
        return "250 Message accepted"

    @property
    def messages(self):
        return [email.message_from_bytes(e.content, policy=email.policy.default) for e in self.envelopes]


def start_smtp_server(sink, **kwargs):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    controller = Controller(sink, hostname="127.0.0.1", port=port, **kwargs)
    controller.start()
    return controller


@pytest.fixture
def smtp_sink():
    """A real SMTP server on localhost (no TLS, no auth); its port is `.port`."""
    sink = SmtpSink()
    controller = start_smtp_server(sink)
    sink.port = controller.port
    yield sink
    controller.stop()
