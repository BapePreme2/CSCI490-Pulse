from pulse_ingest.store import WriteResult


class FakeStore:
    """In-memory MetricStore/QueryStore for API tests that don't need Postgres."""

    def __init__(self, error: Exception | None = None, hosts: dict | None = None):
        self.batches = []
        self.error = error
        # host -> {"latest": [LatestMetric, ...], "history": {name: [HistorySeries, ...]}}
        self._hosts = hosts or {}

    def write_batch(self, batch):
        if self.error:
            raise self.error
        self.batches.append(batch)
        return WriteResult(stored=len(batch.metrics))

    def host_exists(self, host):
        if self.error:
            raise self.error
        return host in self._hosts

    def get_latest(self, host):
        if self.error:
            raise self.error
        return self._hosts.get(host, {}).get("latest", [])

    def get_history(self, host, name, start, end):
        if self.error:
            raise self.error
        return self._hosts.get(host, {}).get("history", {}).get(name, [])
