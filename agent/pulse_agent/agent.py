from __future__ import annotations

import logging
import time

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

        host_tags = {"host": self.config.hostname, **self.config.tags}
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
        logger.info(
            "Pulse agent starting: host=%s endpoint=%s interval=%.1fs",
            self.config.hostname, self.config.endpoint, self.config.interval_seconds,
        )
        while True:
            self.run_once()
            time.sleep(self.config.interval_seconds)
