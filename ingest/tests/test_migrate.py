import uuid

import psycopg
import pytest
from psycopg import sql

from pulse_ingest.migrate import apply_migrations, database_url

pytestmark = pytest.mark.integration


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


def test_migrations_create_expected_tables(scratch_db_url):
    applied = apply_migrations(scratch_db_url)

    assert applied == ["001_initial_schema"]
    with psycopg.connect(scratch_db_url) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
    assert {"hosts", "metrics", "metric_values", "schema_migrations"} <= tables


def test_migrations_are_idempotent(scratch_db_url):
    apply_migrations(scratch_db_url)

    assert apply_migrations(scratch_db_url) == []


def _seed(conn):
    host_id = conn.execute(
        "INSERT INTO hosts (hostname) VALUES ('web-1') RETURNING id"
    ).fetchone()[0]
    metric_id = conn.execute(
        "INSERT INTO metrics (name, unit) VALUES ('cpu.usage', 'percent') RETURNING id"
    ).fetchone()[0]
    return host_id, metric_id


def test_duplicate_sample_is_rejected_so_retries_can_be_idempotent(scratch_db_url):
    apply_migrations(scratch_db_url)
    insert = (
        "INSERT INTO metric_values (host_id, metric_id, ts, value, tags) "
        "VALUES (%s, %s, to_timestamp(1789419042), 1.0, %s::jsonb) ON CONFLICT DO NOTHING"
    )

    with psycopg.connect(scratch_db_url) as conn:
        host_id, metric_id = _seed(conn)
        first = conn.execute(insert, (host_id, metric_id, '{"core": "0"}'))
        again = conn.execute(insert, (host_id, metric_id, '{"core": "0"}'))
        other_core = conn.execute(insert, (host_id, metric_id, '{"core": "1"}'))

        assert (first.rowcount, again.rowcount, other_core.rowcount) == (1, 0, 1)


def test_deleting_a_host_removes_its_samples(scratch_db_url):
    apply_migrations(scratch_db_url)

    with psycopg.connect(scratch_db_url) as conn:
        host_id, metric_id = _seed(conn)
        conn.execute(
            "INSERT INTO metric_values (host_id, metric_id, ts, value) "
            "VALUES (%s, %s, now(), 1.0)",
            (host_id, metric_id),
        )
        conn.execute("DELETE FROM hosts WHERE id = %s", (host_id,))

        assert conn.execute("SELECT count(*) FROM metric_values").fetchone()[0] == 0


def test_failed_migration_leaves_no_partial_state(scratch_db_url, tmp_path):
    (tmp_path / "001_ok.sql").write_text("CREATE TABLE a (id int);")
    (tmp_path / "002_bad.sql").write_text("CREATE TABLE b (id int); SELECT nope_not_a_function();")

    with pytest.raises(psycopg.Error):
        apply_migrations(scratch_db_url, tmp_path)

    with psycopg.connect(scratch_db_url) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
        versions = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    assert "a" in tables and "b" not in tables
    assert versions == {"001_ok"}
