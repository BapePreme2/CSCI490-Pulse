from __future__ import annotations

import time

import psutil

from pulse_agent.metrics import Metric

_BYTES_PER_GB = 1024**3
_BYTES_PER_MB = 1024**2


class DiskUsageCollector:
    """Collects per-mount disk usage percentage and free space."""

    def collect(self) -> list[Metric]:
        metrics: list[Metric] = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue
            tags = {"mount": part.mountpoint, "device": part.device}
            metrics.append(Metric(name="disk.usage", value=usage.percent, unit="percent", tags=tags))
            metrics.append(Metric(name="disk.free", value=usage.free / _BYTES_PER_GB, unit="GB", tags=tags))
        return metrics


class DiskIOCollector:
    """Collects read/write throughput and IOPS per disk, as rates derived
    from psutil's cumulative-since-boot counters.

    A rate needs two samples, so the first collect() call only records a
    baseline and returns no metrics.
    """

    def __init__(self) -> None:
        self._last_counters: dict | None = None
        self._last_time: float | None = None

    def collect(self) -> list[Metric]:
        now = time.time()
        counters = psutil.disk_io_counters(perdisk=True)

        metrics: list[Metric] = []
        if self._last_counters is not None and self._last_time is not None:
            elapsed = now - self._last_time
            if elapsed > 0:
                for disk, current in counters.items():
                    previous = self._last_counters.get(disk)
                    if previous is None:
                        continue
                    tags = {"device": disk}
                    read_rate = (current.read_bytes - previous.read_bytes) / elapsed
                    write_rate = (current.write_bytes - previous.write_bytes) / elapsed
                    read_iops = (current.read_count - previous.read_count) / elapsed
                    write_iops = (current.write_count - previous.write_count) / elapsed
                    metrics.append(Metric("disk.read_throughput", read_rate / _BYTES_PER_MB, "MB/s", tags))
                    metrics.append(Metric("disk.write_throughput", write_rate / _BYTES_PER_MB, "MB/s", tags))
                    metrics.append(Metric("disk.read_iops", read_iops, "ops/s", tags))
                    metrics.append(Metric("disk.write_iops", write_iops, "ops/s", tags))

        self._last_counters = counters
        self._last_time = now
        return metrics
