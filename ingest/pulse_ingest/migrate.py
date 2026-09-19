from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg

DEFAULT_DATABASE_URL = "postgresql://pulse:pulse@localhost:5432/pulse"
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

_CREATE_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def database_url() -> str:
    return os.environ.get("PULSE_DATABASE_URL", DEFAULT_DATABASE_URL)


def apply_migrations(url: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every not-yet-applied *.sql file in filename order.

    Each migration runs in its own transaction together with its bookkeeping
    row, so a failing migration leaves the database untouched. Returns the
    versions applied by this call.
    """
    applied_now: list[str] = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(_CREATE_TRACKING_TABLE)
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

        for path in sorted(migrations_dir.glob("*.sql")):
            version = path.stem
            if version in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            applied_now.append(version)
    return applied_now


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pulse-migrate", description="Apply Pulse DB migrations")
    parser.add_argument("--database-url", default=None, help="Defaults to $PULSE_DATABASE_URL")
    args = parser.parse_args(argv)

    applied = apply_migrations(args.database_url or database_url())
    if applied:
        print("Applied: " + ", ".join(applied))
    else:
        print("Database is up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
