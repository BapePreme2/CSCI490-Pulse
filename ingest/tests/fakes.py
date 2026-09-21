from pulse_ingest.store import WriteResult


class FakeStore:
    """In-memory MetricStore for API tests that don't need Postgres."""

    def __init__(self, error: Exception | None = None):
        self.batches = []
        self.error = error

    def write_batch(self, batch):
        if self.error:
            raise self.error
        self.batches.append(batch)
        return WriteResult(stored=len(batch.metrics))
