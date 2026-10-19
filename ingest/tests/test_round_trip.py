"""End-to-end: the real agent CLI -> the real API server -> a real Postgres.

Both programs run as separate processes. Needs the agent's virtualenv
(agent/.venv) and a running dev Postgres (scripts/dev-db.sh up).
"""

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import psycopg
import pytest

from pulse_ingest.migrate import apply_migrations
from pulse_ingest.notify import EMAIL_ENV_VARS

pytestmark = pytest.mark.integration

INGEST_DIR = Path(__file__).resolve().parents[1]
AGENT_BIN = INGEST_DIR.parent / "agent" / ".venv" / "bin" / "pulse-agent"
API_KEY = "round-trip-key"
HOST = "round-trip-host"


@pytest.fixture(autouse=True)
def _need_agent():
    if not AGENT_BIN.exists():
        pytest.skip("agent not installed (cd agent && python3 -m venv .venv && pip install -e .)")


@pytest.fixture
def db_url(scratch_db_url):
    apply_migrations(scratch_db_url)
    return scratch_db_url


@pytest.fixture
def procs():
    started = []
    yield started
    for proc in started:
        if proc.poll() is None:
            proc.terminate()
    for proc in started:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(condition, timeout=20.0, interval=0.25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(interval)
    return None


def spawn(args, log_path, procs, **kwargs):
    with open(log_path, "w") as log:
        proc = subprocess.Popen(args, stdout=log, stderr=log, **kwargs)
    procs.append(proc)
    return proc


def start_api(db_url, port, procs, tmp_path, **extra_env):
    # Never inherit real mail settings from the shell running the tests.
    inherited = {k: v for k, v in os.environ.items() if k not in EMAIL_ENV_VARS}
    env = {**inherited, "PULSE_DATABASE_URL": db_url, "PULSE_API_KEYS": API_KEY, **extra_env}
    spawn(
        [sys.executable, "-m", "uvicorn", "pulse_ingest.app:app",
         "--port", str(port), "--log-level", "warning"],
        tmp_path / f"api-{port}.log", procs, cwd=INGEST_DIR, env=env,
    )

    def healthy():
        try:
            return urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1).status == 200
        except OSError:
            return False

    assert wait_for(healthy, timeout=15), "API never became healthy\n" + logs(tmp_path)


def start_agent(port, api_key, procs, tmp_path):
    config = tmp_path / f"agent-{api_key}.yaml"
    config.write_text(
        f"endpoint: http://127.0.0.1:{port}/metrics\n"
        f"api_key: {api_key}\n"
        f"hostname: {HOST}\n"
        "interval_seconds: 1\n"
        "environment: e2e\n"
    )
    return spawn(
        [str(AGENT_BIN), "--config", str(config), "--log-level", "DEBUG"],
        tmp_path / f"agent-{api_key}.log", procs,
    )


def logs(tmp_path):
    return "\n".join(f"--- {p.name} ---\n{p.read_text()[-2000:]}" for p in sorted(tmp_path.glob("*.log")))


def query(db_url, sql, params=()):
    with psycopg.connect(db_url) as conn:
        return conn.execute(sql, params).fetchall()


def stored_metric_names(db_url):
    rows = query(
        db_url,
        "SELECT DISTINCT m.name FROM metric_values v "
        "JOIN hosts h ON h.id = v.host_id JOIN metrics m ON m.id = v.metric_id "
        "WHERE h.hostname = %s",
        (HOST,),
    )
    return {r[0] for r in rows}


CORE_METRICS = {
    "cpu.usage", "load.avg", "memory.total", "memory.used", "disk.usage", "network.bytes_sent",
}


def test_agent_metrics_arrive_in_postgres(db_url, procs, tmp_path):
    port = free_port()
    start_api(db_url, port, procs, tmp_path)
    start_agent(port, API_KEY, procs, tmp_path)

    arrived = wait_for(lambda: CORE_METRICS <= stored_metric_names(db_url), timeout=20)
    assert arrived, f"metrics never arrived: {stored_metric_names(db_url)}\n{logs(tmp_path)}"

    assert query(db_url, "SELECT hostname FROM hosts") == [(HOST,)]

    (age,) = query(db_url, "SELECT extract(epoch FROM now() - last_seen_at) FROM hosts")[0]
    assert float(age) < 30

    rows = query(
        db_url,
        "SELECT m.name, v.value, extract(epoch FROM v.ts), v.tags "
        "FROM metric_values v JOIN metrics m ON m.id = v.metric_id",
    )
    by_name = {}
    for name, value, epoch, tags in rows:
        by_name.setdefault(name, []).append(value)
        assert abs(float(epoch) - time.time()) < 60, "sample timestamp is not recent"
        assert tags.get("environment") == "e2e"
        assert "host" not in tags, "redundant host tag should not be stored"

    assert all(0.0 <= v <= 100.0 for v in by_name["cpu.usage"])
    assert all(v > 0 for v in by_name["memory.total"])


def test_metrics_collected_during_an_api_outage_arrive_after_recovery(db_url, procs, tmp_path):
    port = free_port()
    agent = start_agent(port, API_KEY, procs, tmp_path)

    time.sleep(2.5)
    assert query(db_url, "SELECT count(*) FROM metric_values") == [(0,)]
    assert agent.poll() is None, "agent must survive an unreachable API\n" + logs(tmp_path)

    api_started_at = time.time()
    start_api(db_url, port, procs, tmp_path)

    arrived = wait_for(lambda: CORE_METRICS - {"cpu.usage", "network.bytes_sent"} <= stored_metric_names(db_url), timeout=25)
    assert arrived, f"buffered metrics never arrived\n{logs(tmp_path)}"

    (earliest,) = query(db_url, "SELECT min(extract(epoch FROM ts)) FROM metric_values")[0]
    assert float(earliest) < api_started_at, "expected samples collected before the API existed"


def test_wrong_api_key_stores_nothing_and_agent_keeps_retrying(db_url, procs, tmp_path):
    port = free_port()
    start_api(db_url, port, procs, tmp_path)
    agent = start_agent(port, "not-the-key", procs, tmp_path)

    time.sleep(3.5)

    assert agent.poll() is None, "agent should keep retrying, not exit\n" + logs(tmp_path)
    assert query(db_url, "SELECT count(*) FROM metric_values") == [(0,)]
    assert query(db_url, "SELECT count(*) FROM hosts") == [(0,)]
    assert "check api_key" in (tmp_path / "agent-not-the-key.log").read_text()


def test_running_api_raises_an_alert_from_live_agent_data(db_url, procs, tmp_path, smtp_sink):
    # memory.total > 0 is always true, so with the agent reporting every
    # second the rule should open a pending alert on the first sample, then
    # fire it once 3 seconds of breaching samples have landed.
    query(
        db_url,
        "INSERT INTO alert_rules (name, metric_name, operator, threshold, duration_seconds) "
        "VALUES ('always', 'memory.total', '>', 0, 3) RETURNING id",
    )
    port = free_port()
    start_api(
        db_url, port, procs, tmp_path,
        PULSE_ALERT_INTERVAL_SECONDS="0.5",
        PULSE_SMTP_HOST="127.0.0.1",
        PULSE_SMTP_PORT=str(smtp_sink.port),
        PULSE_SMTP_SECURITY="none",
        PULSE_ALERT_EMAIL_FROM="pulse@e2e.test",
        PULSE_ALERT_EMAIL_TO="oncall@e2e.test",
    )
    start_agent(port, API_KEY, procs, tmp_path)
    api_log = tmp_path / f"api-{port}.log"

    fired = wait_for(lambda: "FIRING" in api_log.read_text(), timeout=20)

    assert fired, "alert never fired\n" + logs(tmp_path)
    lines = [l for l in api_log.read_text().splitlines() if "Alert " in l]
    series = f"'always' on {HOST}: memory.total{{environment=e2e}}"
    assert "Alert 1 pending, fires if sustained for 3s" in lines[0] and series in lines[0], lines
    assert "Alert 1 FIRING" in lines[1] and series in lines[1], lines

    ((state, held, tags),) = query(
        db_url, "SELECT state, extract(epoch FROM fired_at - pending_since), tags FROM alerts"
    )
    assert (state, tags) == ("firing", {"environment": "e2e"})
    assert float(held) >= 3

    # The pending step sends nothing; firing sends one email.
    assert wait_for(lambda: smtp_sink.envelopes, timeout=10), "no email\n" + logs(tmp_path)
    (fired_email,) = smtp_sink.messages
    assert fired_email["Subject"] == f"[Pulse] FIRING: always on {HOST} (environment=e2e)"
    assert smtp_sink.envelopes[0].rcpt_tos == ["oncall@e2e.test"]

    # Disabling the rule stops its evaluation, which resolves the alert.
    query(db_url, "UPDATE alert_rules SET enabled = false RETURNING id")
    assert wait_for(lambda: len(smtp_sink.envelopes) == 2, timeout=10), "no resolve email\n" + logs(tmp_path)
    resolved_email = smtp_sink.messages[1]
    assert resolved_email["Subject"] == f"[Pulse] RESOLVED (no longer evaluated): always on {HOST} (environment=e2e)"
    assert "NOT because the value recovered" in " ".join(resolved_email.get_content().split())
