from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from pulse_ingest.schemas import MetricBatch

_UPSERT_HOST = """
INSERT INTO hosts (hostname) VALUES (%s)
ON CONFLICT (hostname) DO UPDATE SET last_seen_at = now()
RETURNING id
"""

# The no-op DO UPDATE makes RETURNING yield rows that already existed too.
_UPSERT_METRICS = """
INSERT INTO metrics (name, unit)
SELECT * FROM unnest(%s::text[], %s::text[])
ON CONFLICT (name, unit) DO UPDATE SET name = EXCLUDED.name
RETURNING id, name, unit
"""

_INSERT_VALUE = """
INSERT INTO metric_values (host_id, metric_id, ts, value, tags)
VALUES (%s, %s, to_timestamp(%s), %s, %s)
ON CONFLICT DO NOTHING
"""


@dataclass(frozen=True)
class WriteResult:
    stored: int


class MetricStore(Protocol):
    def write_batch(self, batch: MetricBatch) -> WriteResult:
        """Persist a batch atomically; already-stored samples are skipped."""


class PostgresMetricStore:
    def __init__(
        self,
        url: str,
        min_size: int = 1,
        max_size: int = 10,
        connect_timeout: float = 5.0,
    ) -> None:
        self._pool = ConnectionPool(
            url, min_size=min_size, max_size=max_size, timeout=connect_timeout, open=False
        )

    def open(self) -> None:
        self._pool.open()

    def close(self) -> None:
        self._pool.close()

    def write_batch(self, batch: MetricBatch) -> WriteResult:
        # Sorted so concurrent batches lock shared metric rows in the same
        # order and cannot deadlock each other.
        pairs = sorted({(m.name, m.unit) for m in batch.metrics})

        with self._pool.connection() as conn, conn.cursor() as cur:
            host_id = cur.execute(_UPSERT_HOST, (batch.host,)).fetchone()[0]

            cur.execute(_UPSERT_METRICS, ([p[0] for p in pairs], [p[1] for p in pairs]))
            metric_ids = {(name, unit): metric_id for metric_id, name, unit in cur.fetchall()}

            # host_id already identifies the host, so the agent's redundant
            # "host" tag is not stored on every row.
            rows = [
                (
                    host_id,
                    metric_ids[(m.name, m.unit)],
                    m.timestamp,
                    m.value,
                    Jsonb({k: v for k, v in m.tags.items() if k != "host"}),
                )
                for m in batch.metrics
            ]
            cur.executemany(_INSERT_VALUE, rows)
            return WriteResult(stored=cur.rowcount)
