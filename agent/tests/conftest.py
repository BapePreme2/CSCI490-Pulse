import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class StubServer:
    """A real local HTTP server standing in for the ingestion API.

    Each POST consumes the next status from `statuses` (default 202) and is
    recorded in `requests`.
    """

    def __init__(self):
        self.requests = []
        self.statuses = []
        self.delay = 0.0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"null")
                outer.requests.append(
                    {"path": self.path, "headers": dict(self.headers), "json": body}
                )
                if outer.delay:
                    time.sleep(outer.delay)
                status = outer.statuses.pop(0) if outer.statuses else 202
                payload = json.dumps({"stub_status": status}).encode()
                self.send_response(status)
                if 300 <= status < 400:
                    self.send_header("Location", outer.url)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}/metrics"
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )

    def start(self):
        self._thread.start()

    def stop(self):
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def stub_server():
    server = StubServer()
    server.start()
    yield server
    server.stop()
