"""OPS-02: the end-to-end smoke test itself, run against the real stack."""

import subprocess
import sys

import psycopg
import pytest

from pulse_ingest import smoke
from pulse_ingest.migrate import database_url

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _needs_agent_and_postgres():
    if not smoke.DEFAULT_AGENT.exists():
        pytest.skip("agent not installed (cd agent && python3 -m venv .venv && pip install -e .)")
    try:
        psycopg.connect(database_url(), connect_timeout=3).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable (run scripts/dev-db.sh up)")


def smoke_databases():
    with psycopg.connect(database_url()) as conn:
        return {r[0] for r in conn.execute("SELECT datname FROM pg_database WHERE datname LIKE 'pulse_smoke_%'")}


def test_the_smoke_test_passes_end_to_end():
    before = smoke_databases()

    result = subprocess.run(
        [sys.executable, "-m", "pulse_ingest.smoke"], capture_output=True, text=True, timeout=180
    )

    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert lines[-1].startswith("PASS: 11 checks in ")
    assert sum(line.startswith("  ok ") for line in lines) == 11
    assert any("resolved (recovered)" in line for line in lines)
    assert smoke_databases() == before, "the scratch database should be dropped"


def test_a_failed_check_says_which_shows_the_logs_and_still_cleans_up(monkeypatch, capsys):
    runs = []

    def failing_create_rule(self):
        runs.append(self)
        raise smoke.SmokeFailure("rule rejected (simulated)")

    monkeypatch.setattr(smoke.Smoke, "create_rule", failing_create_rule)

    assert smoke.main([]) == 1

    out = capsys.readouterr().out
    assert "  FAIL " in out and "rule rejected (simulated)" in out
    assert "--- api.log" in out and "--- agent.log" in out
    assert out.rstrip().splitlines()[-1].startswith("FAIL after 4 passing checks")
    (run,) = runs
    assert all(proc.poll() is not None for proc in run.processes), "API and agent should be stopped"
    assert run.db_name not in smoke_databases()


def test_unreachable_postgres_fails_the_first_check_with_a_hint(monkeypatch, capsys):
    monkeypatch.setenv("PULSE_DATABASE_URL", f"postgresql://pulse:hunter2@127.0.0.1:{smoke.free_port()}/pulse")

    assert smoke.main([]) == 1

    out = capsys.readouterr().out
    assert "Postgres not reachable at 127.0.0.1:" in out and "scripts/dev-db.sh up" in out
    assert "hunter2" not in out


def test_a_missing_agent_is_a_usage_error(tmp_path, capsys):
    assert smoke.main(["--agent", str(tmp_path / "nope")]) == 2

    assert "Agent not found" in capsys.readouterr().err
