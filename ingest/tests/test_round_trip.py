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


def start_api(db_url, port, procs, tmp_path):
    env = {**os.environ, "PULSE_DATABASE_URL": db_url, "PULSE_API_KEYS": API_KEY}
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
        "tags:\n  environment: e2e\n"
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
