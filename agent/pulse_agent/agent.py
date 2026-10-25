from __future__ import annotations

import logging
import threading

from pulse_agent.buffer import MetricBuffer, Sender
from pulse_agent.collectors.cpu import CPUCollector
from pulse_agent.collectors.disk import DiskIOCollector, DiskUsageCollector
from pulse_agent.collectors.load import LoadAverageCollector
from pulse_agent.collectors.memory import MemoryCollector
from pulse_agent.collectors.network import NetworkCollector
from pulse_agent.config import AgentConfig
from pulse_agent.metrics import Metric
from pulse_agent.sender import HttpSender

logger = logging.getLogger(__name__)


class LoggingSender:
    """Sender for --dry-run: logs each batch instead of transmitting it."""

    def send(self, metrics: list[Metric]) -> bool:
        logger.info("Would send %d metric(s)", len(metrics))
        for metric in metrics:
            logger.debug("  %s", metric.to_dict())
        return True


class PulseAgent:
    def __init__(self, config: AgentConfig, sender: Sender | None = None) -> None:
        self.config = config
        self.sender = sender or HttpSender(
            config.endpoint,
            config.api_key,
            config.hostname,
            timeout=config.request_timeout_seconds,
        )
        self.buffer = MetricBuffer(max_batches=config.buffer_max_batches)
        self._stop = threading.Event()
        self.collectors = [
            CPUCollector(),
            LoadAverageCollector(),
            MemoryCollector(),
            DiskUsageCollector(),
            DiskIOCollector(),
            NetworkCollector(),
        ]

    def collect_once(self) -> list[Metric]:
        metrics: list[Metric] = []
        for collector in self.collectors:
            metrics.extend(collector.collect())

        # host/environment are applied last so they can never be clobbered
        # by a crafted tags dict, even bypassing from_dict's own validation
        # of this (e.g. a config built directly rather than loaded from YAML).
        host_tags: dict[str, str] = dict(self.config.tags)
        if self.config.environment:
            host_tags["environment"] = self.config.environment
        host_tags["host"] = self.config.hostname

        for metric in metrics:
            for key, value in host_tags.items():
                metric.tags.setdefault(key, value)
        return metrics

    def run_once(self) -> None:
        metrics = self.collect_once()
        if metrics:
            self.buffer.add(metrics)
        self.buffer.flush(self.sender)

    def run_forever(self) -> None:
        """Collect and flush every interval_seconds until stop() is called."""
        logger.info(
            "Pulse agent starting: host=%s endpoint=%s interval=%.1fs",
            self.config.hostname, self.config.endpoint, self.config.interval_seconds,
        )
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self.config.interval_seconds)
        # Metrics are flushed every cycle, so anything still here is what the
        # API refused or couldn't be reached for; say so rather than drop it
        # silently.
        undelivered = f"; {len(self.buffer)} buffered metric(s) were not delivered" if len(self.buffer) else ""
        logger.info("Pulse agent stopping%s", undelivered)

    def stop(self) -> None:
        """Make run_forever() return, at once if it's between cycles. Safe to
        call from a signal handler."""
        self._stop.set()
