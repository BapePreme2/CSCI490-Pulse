import email
import email.policy
import json
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


class WebhookSink:
    """What a local stand-in for a Slack incoming webhook received: each
    POST's path, Content-Type, and JSON body."""

    def __init__(self):
        self.requests = []
        self.reply = (200, "ok")

    @property
    def payloads(self):
        return [body for _, _, body in self.requests]


@pytest.fixture
def webhook_sink():
    """A real HTTP server on localhost. `.url` has a made-up secret path like
    a real webhook's; set `.reply` to (status, body) to answer differently."""
    sink = WebhookSink()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            sink.requests.append((self.path, self.headers["Content-Type"], json.loads(body)))
            status, text = sink.reply
            self.send_response(status)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(text.encode())

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    sink.url = f"http://127.0.0.1:{server.server_address[1]}/services/T000/B000/secret-token"
    yield sink
    server.shutdown()
    server.server_close()
