from __future__ import annotations

import logging
import time
from collections import deque
from typing import Protocol

from pulse_agent.metrics import Metric

logger = logging.getLogger(__name__)


class Sender(Protocol):
    def send(self, metrics: list[Metric]) -> bool:
        """Attempt to deliver a batch of metrics. Returns True on success."""


class MetricBuffer:
    """Batches collected metrics and retries failed sends with exponential
    backoff, so a temporarily unreachable ingestion API doesn't lose data
    or spam it with retries.

    Metrics are only dropped once the buffer exceeds its capacity, at which
    point the oldest are discarded to keep the agent's own memory bounded
    during a prolonged outage.
    """

    def __init__(
        self,
        max_batches: int = 100,
        metrics_per_batch_estimate: int = 50,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 60.0,
    ) -> None:
        self._pending: deque[Metric] = deque()
        self._max_metrics = max_batches * metrics_per_batch_estimate
        self._base_backoff = base_backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._consecutive_failures = 0
        self._next_attempt_time = 0.0

    def add(self, metrics: list[Metric]) -> None:
        self._pending.extend(metrics)
        overflow = len(self._pending) - self._max_metrics
        if overflow > 0:
            for _ in range(overflow):
                self._pending.popleft()
            logger.warning("Metric buffer overflowed, dropped %d oldest metric(s)", overflow)

    def flush(self, sender: Sender, now: float | None = None) -> bool:
        """Attempt to send everything currently buffered as one batch.

        Returns True if there was nothing to send or the send succeeded;
        False if a send was skipped (still in backoff) or failed.
        """
        now = time.time() if now is None else now
        if not self._pending:
            return True
        if now < self._next_attempt_time:
            return False

        batch = list(self._pending)
        try:
            success = sender.send(batch)
        except Exception:
            logger.exception("Sender raised while flushing metric buffer")
            success = False

        if success:
            self._pending.clear()
            self._consecutive_failures = 0
            self._next_attempt_time = 0.0
            return True

        self._consecutive_failures += 1
        delay = min(self._base_backoff * (2 ** (self._consecutive_failures - 1)), self._max_backoff)
        self._next_attempt_time = now + delay
        logger.warning(
            "Failed to send %d metric(s), retrying in %.1fs (failure #%d)",
            len(batch), delay, self._consecutive_failures,
        )
        return False

    def __len__(self) -> int:
        return len(self._pending)
