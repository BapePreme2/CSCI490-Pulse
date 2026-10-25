"""End-to-end smoke test (OPS-02): agent -> ingest API -> Postgres -> rule
evaluation -> alert -> email and Slack notifications.

Runs the real agent CLI and the real API as separate processes against a
throwaway database, and drives them only through the interfaces a deployment
uses: the agent's config file, the REST API (POST/PUT/DELETE /rules,
GET /hosts, GET /alerts), and real SMTP and webhook deliveries to local
stand-ins. Prints one line per check and exits 0 if all pass.

    scripts/smoke-test.sh            # from the repo root
    python -m pulse_ingest.smoke     # the same, from ingest/ with its venv active

Needs a running Postgres (scripts/dev-db.sh up) and the agent installed in
agent/.venv. The SMTP stand-in uses aiosmtpd, one of the dev dependencies.
"""

from __future__ import annotations

import argparse
import email
import email.policy
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from pulse_ingest.migrate import apply_migrations, database_url
from pulse_ingest.notify import NOTIFY_ENV_VARS, SLACK_COLORS

INGEST_DIR = Path(__file__).resolve().parents[1]
DEFAULT_AGENT = INGEST_DIR.parent / "agent" / ".venv" / "bin" / "pulse-agent"
DEFAULT_TIMEOUT_SECONDS = 30.0

HOST = "smoke-host"
RULE = {
    "name": "Smoke test",
    "metric_name": "memory.total",
    "operator": ">",
    "threshold": 0,  # always true, so the rule fires on the agent's real data
    "duration_seconds": 3,
    "host": HOST,
    "tag_filter": {},
}
# How the notifications name the series: the agent's environment tag rides
# along on every metric.
SERIES = f"{RULE['name']} on {HOST} (environment=smoke)"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SmtpSink:
    """Handler for a real local SMTP server (aiosmtpd) that keeps every
    message it accepts."""

    def __init__(self) -> None:
        self.envelopes: list = []

    async def handle_DATA(self, server, session, envelope):
        self.envelopes.append(envelope)
        return "250 Message accepted"

    @property
    def messages(self) -> list[email.message.EmailMessage]:
        return [email.message_from_bytes(e.content, policy=email.policy.default) for e in self.envelopes]


def start_smtp_server(sink: SmtpSink, **kwargs):
    """A real SMTP server on localhost for `sink`; its port is `.port`."""
    from aiosmtpd.controller import Controller  # a dev dependency, so imported only when needed

    controller = Controller(sink, hostname="127.0.0.1", port=free_port(), **kwargs)
    controller.start()
    return controller


class WebhookSink:
    """A real local HTTP server standing in for a Slack incoming webhook. It
    keeps each POST's path, Content-Type, and JSON body. `.url` has a
    made-up secret path like a real webhook's; set `.reply` to (status, body)
    to answer differently."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, Any]] = []
        self.reply = (200, "ok")
        sink = self

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

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/services/T000/B000/secret-token"

    @property
    def payloads(self) -> list[Any]:
        return [body for _, _, body in self.requests]

    def start(self) -> None:
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class SmokeFailure(Exception):
    """A check failed; the message says what was expected and what happened."""


class Smoke:
    """One smoke-test run. `run()` performs the checks in order and stops at
    the first failure; `cleanup()` stops everything it started and drops the
    scratch database, whether or not the checks passed."""

    def __init__(self, workdir: Path, agent: Path, timeout: float, out: Callable[[str], None] = print) -> None:
        self.workdir = workdir
        self.agent = agent
        self.timeout = timeout
        self.out = out
        self.admin_url = database_url()
        self.db_name = f"pulse_smoke_{secrets.token_hex(4)}"
        self.db_created = False
        self.api_key = secrets.token_urlsafe(16)
        self.api_url = ""
        self.processes: list[subprocess.Popen] = []
        self.smtp = SmtpSink()
        self.smtp_controller = None
        self.webhook = WebhookSink()
        self.webhook_started = False
        self.checks = 0

    # --- the checks ---

    def run(self) -> None:
        self.check(self.create_database)
        self.check(self.start_sinks)
        self.check(self.start_api)
        self.check(self.start_agent)
        rule_id = self.check(self.create_rule)
        alert_id = self.check(lambda: self.wait_until_fired(rule_id))
        self.check(lambda: self.expect_notifications(1, "FIRING", "firing"))
        self.check(lambda: self.resolve_by_raising_threshold(rule_id, alert_id))
        self.check(lambda: self.expect_notifications(2, "RESOLVED", "recovered"))
        self.check(self.expect_nothing_else_sent)
        self.check(lambda: self.delete_rule_keeps_history(rule_id, alert_id))

    def check(self, step: Callable[[], Any]) -> Any:
        """Run one check, printing "ok" and its description, or "FAIL"."""
        started = time.monotonic()
        try:
            description, result = step()
        except SmokeFailure as exc:
            self.out(f"  FAIL  {time.monotonic() - started:5.1f}s  {exc}")
            raise
        self.checks += 1
        self.out(f"  ok    {time.monotonic() - started:5.1f}s  {description}")
        return result

    def create_database(self):
        try:
            admin = psycopg.connect(self.admin_url, autocommit=True, connect_timeout=3)
        except psycopg.OperationalError:
            raise SmokeFailure(f"Postgres not reachable at {_describe_db(self.admin_url)} (start it: scripts/dev-db.sh up)")
        with admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.db_name)))
        self.db_created = True
        applied = apply_migrations(self.db_url)
        return f"scratch database {self.db_name} created, {len(applied)} migrations applied", None

    def start_sinks(self):
        self.smtp_controller = start_smtp_server(self.smtp)
        self.webhook.start()
        self.webhook_started = True
        return f"local SMTP server on :{self.smtp_controller.port}, Slack webhook stand-in on :{self.webhook.port}", None

    def start_api(self):
        port = free_port()
        self.api_url = f"http://127.0.0.1:{port}"
        # Nothing from the caller's shell that could send real notifications
        # or point at a real database.
        inherited = {k: v for k, v in os.environ.items() if k not in NOTIFY_ENV_VARS and not k.startswith("PULSE_")}
        env = {
            **inherited,
            "PULSE_DATABASE_URL": self.db_url,
            "PULSE_API_KEYS": self.api_key,
            "PULSE_ALERT_INTERVAL_SECONDS": "0.5",
            "PULSE_SMTP_HOST": "127.0.0.1",
            "PULSE_SMTP_PORT": str(self.smtp_controller.port),
            "PULSE_SMTP_SECURITY": "none",
            "PULSE_ALERT_EMAIL_FROM": "pulse@smoke.test",
            "PULSE_ALERT_EMAIL_TO": "oncall@smoke.test",
            "PULSE_SLACK_WEBHOOK_URL": self.webhook.url,
        }
        api = self.spawn(
            "api",
            [sys.executable, "-m", "uvicorn", "pulse_ingest.app:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=INGEST_DIR,
            env=env,
        )

        def healthy():
            if api.poll() is not None:
                raise SmokeFailure(f"API exited with code {api.returncode} during startup")
            try:
                return self.request("GET", "/health")["status"] == "ok"
            except SmokeFailure:
                return False

        self.wait(healthy, "the API to answer GET /health")
        return f"API healthy on :{port} (email and Slack notifications on)", None

    def start_agent(self):
        config = self.workdir / "agent.yaml"
        config.write_text(
            f"endpoint: {self.api_url}/metrics\n"
            f"api_key: {self.api_key}\n"
            f"hostname: {HOST}\n"
            "interval_seconds: 1\n"
            "environment: smoke\n"
        )
        agent = self.spawn("agent", [str(self.agent), "--config", str(config)])

        def reporting():
            if agent.poll() is not None:
                raise SmokeFailure(f"agent exited with code {agent.returncode}")
            hosts = {h["hostname"]: h for h in self.request("GET", "/hosts")["hosts"]}
            if hosts.get(HOST, {}).get("status") != "online":
                return None
            latest = self.request("GET", f"/hosts/{HOST}/metrics/latest")["metrics"]
            return latest if any(m["name"] == RULE["metric_name"] for m in latest) else None

        latest = self.wait(reporting, f"{HOST} to show as online with {RULE['metric_name']} stored")
        return f"agent reporting as {HOST}: online, {len(latest)} series stored", None

    def create_rule(self):
        rule = self.request("POST", "/rules", RULE)
        condition = f"{RULE['metric_name']} {RULE['operator']} {RULE['threshold']} for {RULE['duration_seconds']}s"
        return f"rule #{rule['id']} created through POST /rules: {condition} on {HOST}", rule["id"]

    def wait_until_fired(self, rule_id: int):
        created = time.monotonic()

        def fired():
            history = self.request("GET", f"/alerts?host={HOST}&state=firing")
            alert = next((a for a in history["alerts"] if a["rule_id"] == rule_id), None)
            return (alert, history["firing_count"]) if alert else None

        alert, firing_count = self.wait(fired, f"rule #{rule_id} to fire (GET /alerts?state=firing)")
        if firing_count != 1:
            raise SmokeFailure(f"expected 1 alert firing on {HOST}, GET /alerts says {firing_count}")
        return f"alert #{alert['id']} firing {time.monotonic() - created:.1f}s later, listed by GET /alerts", alert["id"]

    def expect_notifications(self, count: int, status: str, outcome: str):
        subject = f"[Pulse] {status}: {SERIES}"
        self.wait(lambda: len(self.smtp.envelopes) >= count, f"email #{count} ({status})")
        message = self.smtp.messages[count - 1]
        if message["Subject"] != subject:
            raise SmokeFailure(f"email #{count} subject was {message['Subject']!r}, expected {subject!r}")
        if self.smtp.envelopes[count - 1].rcpt_tos != ["oncall@smoke.test"]:
            raise SmokeFailure(f"email #{count} went to {self.smtp.envelopes[count - 1].rcpt_tos}")

        self.wait(lambda: len(self.webhook.payloads) >= count, f"Slack message #{count} ({status})")
        payload = self.webhook.payloads[count - 1]
        color = payload["attachments"][0]["color"]
        if (payload["text"], color) != (subject, SLACK_COLORS[outcome]):
            raise SmokeFailure(f"Slack message #{count} was {payload['text']!r} in {color}, expected {subject!r}")
        return f"{status.lower()} email and Slack message delivered: {subject!r}", None

    def resolve_by_raising_threshold(self, rule_id: int, alert_id: int):
        # Out of reach, so the next evaluation finds the value back within
        # the threshold: a genuine recovery, not a rule that stopped matching.
        self.request("PUT", f"/rules/{rule_id}", {**RULE, "threshold": 1e15, "enabled": True})

        def resolved():
            history = self.request("GET", f"/alerts?host={HOST}&state=resolved")
            return next((a for a in history["alerts"] if a["id"] == alert_id), None)

        alert = self.wait(resolved, f"alert #{alert_id} to resolve after PUT /rules/{rule_id}")
        if alert["resolve_reason"] != "recovered":
            raise SmokeFailure(f"alert #{alert_id} resolved as {alert['resolve_reason']!r}, expected 'recovered'")
        fired_for = alert["resolved_at"] - alert["fired_at"]
        return f"threshold raised through PUT /rules; alert #{alert_id} resolved (recovered) after {fired_for:.1f}s", None

    def expect_nothing_else_sent(self):
        # A few more evaluation cycles: a duplicate, or a notification for the
        # pending step, would show up by then.
        time.sleep(2)
        sent = (len(self.smtp.envelopes), len(self.webhook.payloads))
        if sent != (2, 2):
            raise SmokeFailure(f"expected exactly 2 emails and 2 Slack messages, got {sent[0]} and {sent[1]}")
        return "exactly one email and one Slack message per transition", None

    def delete_rule_keeps_history(self, rule_id: int, alert_id: int):
        self.request("DELETE", f"/rules/{rule_id}")
        alert = next((a for a in self.request("GET", "/alerts")["alerts"] if a["id"] == alert_id), None)
        if alert is None or alert["rule_id"] is not None:
            raise SmokeFailure(f"after DELETE /rules/{rule_id}, alert #{alert_id} should stay listed with rule_id null")
        return f"rule deleted; alert #{alert_id} stays in the history", None

    # --- plumbing ---

    @property
    def db_url(self) -> str:
        return psycopg.conninfo.make_conninfo(self.admin_url, dbname=self.db_name)

    def spawn(self, name: str, args: list[str], **kwargs) -> subprocess.Popen:
        with open(self.workdir / f"{name}.log", "w") as log:
            proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, **kwargs)
        self.processes.append(proc)
        return proc

    def request(self, method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            self.api_url + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                text = response.read()
        except urllib.error.HTTPError as exc:
            raise SmokeFailure(f"{method} {path} answered {exc.code}: {exc.read(300).decode('utf-8', 'replace')}")
        except OSError as exc:
            raise SmokeFailure(f"{method} {path} failed: {getattr(exc, 'reason', exc)}")
        return json.loads(text) if text else None

    def wait(self, condition: Callable[[], Any], what: str) -> Any:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            result = condition()
            if result:
                return result
            time.sleep(0.25)
        raise SmokeFailure(f"timed out after {self.timeout:g}s waiting for {what}")

    def logs(self, lines: int = 30) -> str:
        parts = []
        for path in sorted(self.workdir.glob("*.log")):
            tail = path.read_text(errors="replace").splitlines()[-lines:]
            parts.append(f"--- {path.name} (last {len(tail)} lines) ---\n" + "\n".join(tail))
        return "\n".join(parts)

    def cleanup(self) -> None:
        for proc in reversed(self.processes):  # the agent before the API
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if self.webhook_started:
            self.webhook.stop()
        if self.smtp_controller is not None:
            self.smtp_controller.stop()
        if self.db_created:
            try:
                with psycopg.connect(self.admin_url, autocommit=True, connect_timeout=3) as admin:
                    admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(self.db_name)))
            except psycopg.OperationalError as exc:
                self.out(f"  warning: could not drop the scratch database {self.db_name}: {exc}")


def _describe_db(url: str) -> str:
    """host:port/dbname, without the password."""
    info = psycopg.conninfo.conninfo_to_dict(url)
    return f"{info.get('host', 'localhost')}:{info.get('port', 5432)}/{info.get('dbname', '')}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pulse-smoke", description="End-to-end smoke test: agent -> API -> alert -> notifications"
    )
    parser.add_argument("--agent", type=Path, default=DEFAULT_AGENT, help="the pulse-agent executable to run")
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="seconds to wait at each step (default 30)"
    )
    args = parser.parse_args(argv)

    if not args.agent.exists():
        print(
            f"Agent not found at {args.agent}. Install it (cd agent && python3 -m venv .venv && "
            "pip install -e .) or pass --agent.",
            file=sys.stderr,
        )
        return 2

    print("Pulse smoke test: agent -> API -> Postgres -> alert -> email + Slack")
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="pulse-smoke-") as workdir:
        smoke = Smoke(Path(workdir), args.agent, args.timeout)
        try:
            smoke.run()
        except SmokeFailure:
            print(smoke.logs())
            print(f"FAIL after {smoke.checks} passing checks ({time.monotonic() - started:.1f}s)")
            return 1
        finally:
            smoke.cleanup()
    print(f"PASS: {smoke.checks} checks in {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
