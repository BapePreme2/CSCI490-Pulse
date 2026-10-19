import psycopg
import pytest

from pulse_ingest.migrate import apply_migrations

pytestmark = pytest.mark.integration


def test_migrations_create_expected_tables(scratch_db_url):
    applied = apply_migrations(scratch_db_url)

    assert applied == sorted(applied)
    assert "001_initial_schema" in applied
    with psycopg.connect(scratch_db_url) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
    assert {"hosts", "metrics", "metric_values", "alert_rules", "alerts", "schema_migrations"} <= tables


def test_migrations_create_the_hosts_last_seen_index(scratch_db_url):
    apply_migrations(scratch_db_url)

    with psycopg.connect(scratch_db_url) as conn:
        indexes = {
            r[0] for r in conn.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'hosts'")
        }
    assert "hosts_last_seen_at_idx" in indexes


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


def _insert_rule(conn, **overrides):
    row = {
        "name": "High CPU",
        "metric_name": "cpu.usage",
        "operator": ">",
        "threshold": 80.0,
        "duration_seconds": 300,
        "host_id": None,
        "tag_filter": "{}",
    }
    row.update(overrides)
    return conn.execute(
        "INSERT INTO alert_rules (name, metric_name, operator, threshold, duration_seconds, host_id, tag_filter) "
        "VALUES (%(name)s, %(metric_name)s, %(operator)s, %(threshold)s, %(duration_seconds)s, "
        "%(host_id)s, %(tag_filter)s::jsonb) RETURNING id",
        row,
    ).fetchone()[0]


def test_alert_rule_defaults(scratch_db_url):
    apply_migrations(scratch_db_url)

    with psycopg.connect(scratch_db_url) as conn:
        rule_id = _insert_rule(conn)
        enabled, tag_filter, host_id = conn.execute(
            "SELECT enabled, tag_filter, host_id FROM alert_rules WHERE id = %s", (rule_id,)
        ).fetchone()

    assert (enabled, tag_filter, host_id) == (True, {}, None)


@pytest.mark.parametrize(
    "override",
    [
        {"name": ""},
        {"metric_name": ""},
        {"operator": "=="},
        {"threshold": float("nan")},
        {"threshold": float("inf")},
        {"threshold": float("-inf")},
        {"duration_seconds": -1},
        {"duration_seconds": 86401},
        {"tag_filter": "[]"},
        {"tag_filter": '"core"'},
        {"host_id": 999999},
    ],
)
def test_alert_rule_constraints_reject_bad_rows(scratch_db_url, override):
    apply_migrations(scratch_db_url)

    with psycopg.connect(scratch_db_url) as conn:
        with pytest.raises(psycopg.errors.IntegrityError):
            _insert_rule(conn, **override)


def test_deleting_a_host_removes_only_its_own_alert_rules(scratch_db_url):
    apply_migrations(scratch_db_url)

    with psycopg.connect(scratch_db_url) as conn:
        host_id, _ = _seed(conn)
        _insert_rule(conn, name="web-1 only", host_id=host_id)
        _insert_rule(conn, name="fleet-wide")
        conn.execute("DELETE FROM hosts WHERE id = %s", (host_id,))

        names = [r[0] for r in conn.execute("SELECT name FROM alert_rules")]

    assert names == ["fleet-wide"]


def test_tag_filter_null_means_absent_is_expressible_in_sql(scratch_db_url):
    # The evaluator (ALERT-02) needs to match series against a rule's filter in
    # SQL: non-null values must be contained in the series tags, and null
    # values name tags the series must not have. Checked here so the schema
    # design is known to be evaluable before anything is built on it.
    apply_migrations(scratch_db_url)
    match = (
        "SELECT %(tags)s::jsonb @> jsonb_strip_nulls(%(f)s::jsonb) "
        "AND NOT (%(tags)s::jsonb ?| ARRAY(SELECT key FROM jsonb_each(%(f)s::jsonb) "
        "WHERE value = 'null'::jsonb))"
    )
    overall = '{"environment": "demo"}'
    core0 = '{"environment": "demo", "core": "0"}'

    with psycopg.connect(scratch_db_url) as conn:
        def matches(tags, f):
            return conn.execute(match, {"tags": tags, "f": f}).fetchone()[0]

        assert matches(overall, "{}") and matches(core0, "{}")
        assert matches(overall, '{"core": null}') and not matches(core0, '{"core": null}')
        assert matches(core0, '{"core": "0"}') and not matches(overall, '{"core": "0"}')
        assert not matches(core0, '{"core": "1"}')


def _insert_alert(conn, rule_id, host_id, state="firing", tags="{}", **overrides):
    row = {
        "rule_id": rule_id,
        "host_id": host_id,
        "tags": tags,
        "state": state,
        "fired": state != "pending",
        "resolved": state == "resolved",
        "reason": "recovered" if state == "resolved" else None,
    }
    row.update(overrides)
    return conn.execute(
        "INSERT INTO alerts (rule_id, host_id, unit, tags, rule_name, metric_name, operator, threshold, "
        "duration_seconds, state, pending_since, fired_at, resolved_at, resolve_reason, last_value, "
        "last_evaluated_at) VALUES (%(rule_id)s, %(host_id)s, 'percent', %(tags)s::jsonb, 'High CPU', "
        "'cpu.usage', '>', 80, 300, %(state)s, now(), CASE WHEN %(fired)s THEN now() END, "
        "CASE WHEN %(resolved)s THEN now() END, %(reason)s, 95, now()) RETURNING id",
        row,
    ).fetchone()[0]


@pytest.mark.parametrize(
    "state, overrides",
    [
        ("pending", {"fired": True}),
        ("pending", {"reason": "recovered"}),
        ("firing", {"fired": False}),
        ("firing", {"resolved": True}),
        ("resolved", {"resolved": False}),
        ("resolved", {"reason": None}),
        ("resolved", {"fired": False}),
        ("resolved", {"reason": "fixed"}),
        ("cancelled", {}),
    ],
)
def test_alert_state_and_timestamps_must_agree(scratch_db_url, state, overrides):
    apply_migrations(scratch_db_url)

    with psycopg.connect(scratch_db_url) as conn:
        host_id, _ = _seed(conn)
        rule_id = _insert_rule(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_alert(conn, rule_id, host_id, state, **overrides)


def test_only_one_open_alert_per_rule_and_series(scratch_db_url):
    apply_migrations(scratch_db_url)

    with psycopg.connect(scratch_db_url) as conn:
        host_id, _ = _seed(conn)
        rule_id = _insert_rule(conn)
        _insert_alert(conn, rule_id, host_id, "resolved")
        _insert_alert(conn, rule_id, host_id, "resolved")
        _insert_alert(conn, rule_id, host_id, "firing")
        _insert_alert(conn, rule_id, host_id, "pending", tags='{"core": "0"}')

        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert_alert(conn, rule_id, host_id, "pending")


def test_deleting_a_rule_keeps_its_alerts(scratch_db_url):
    apply_migrations(scratch_db_url)

    with psycopg.connect(scratch_db_url) as conn:
        host_id, _ = _seed(conn)
        rule_id = _insert_rule(conn)
        alert_id = _insert_alert(conn, rule_id, host_id, "resolved")
        conn.execute("DELETE FROM alert_rules WHERE id = %s", (rule_id,))

        assert conn.execute("SELECT rule_id, rule_name FROM alerts WHERE id = %s", (alert_id,)).fetchone() == (
            None, "High CPU"
        )


def test_deleting_a_host_removes_its_alerts(scratch_db_url):
    apply_migrations(scratch_db_url)

    with psycopg.connect(scratch_db_url) as conn:
        host_id, _ = _seed(conn)
        _insert_alert(conn, _insert_rule(conn), host_id, "firing")
        conn.execute("DELETE FROM hosts WHERE id = %s", (host_id,))

        assert conn.execute("SELECT count(*) FROM alerts").fetchone()[0] == 0
