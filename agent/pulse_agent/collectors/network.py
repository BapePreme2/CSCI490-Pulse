from __future__ import annotations

import time

import psutil

from pulse_agent.metrics import Metric

_BYTES_PER_MB = 1024**2


class NetworkCollector:
    """Collects per-interface network throughput, packet rate, errors, and
    drops, as rates derived from psutil's cumulative-since-boot counters.

    A rate needs two samples, so the first collect() call only records a
    baseline and returns no metrics.
    """

    def __init__(self) -> None:
        self._last_counters: dict | None = None
        self._last_time: float | None = None

    def collect(self) -> list[Metric]:
        now = time.time()
        counters = psutil.net_io_counters(pernic=True)

        metrics: list[Metric] = []
        if self._last_counters is not None and self._last_time is not None:
            elapsed = now - self._last_time
            if elapsed > 0:
                for iface, current in counters.items():
                    previous = self._last_counters.get(iface)
                    if previous is None:
                        continue
                    tags = {"interface": iface}
                    sent_rate = (current.bytes_sent - previous.bytes_sent) / elapsed
                    recv_rate = (current.bytes_recv - previous.bytes_recv) / elapsed
                    pkt_sent_rate = (current.packets_sent - previous.packets_sent) / elapsed
                    pkt_recv_rate = (current.packets_recv - previous.packets_recv) / elapsed
                    error_rate = (
                        (current.errin - previous.errin) + (current.errout - previous.errout)
                    ) / elapsed
                    drop_rate = (
                        (current.dropin - previous.dropin) + (current.dropout - previous.dropout)
                    ) / elapsed
                    metrics.append(Metric("network.bytes_sent", sent_rate / _BYTES_PER_MB, "MB/s", tags))
                    metrics.append(Metric("network.bytes_recv", recv_rate / _BYTES_PER_MB, "MB/s", tags))
                    metrics.append(Metric("network.packets_sent", pkt_sent_rate, "pkts/s", tags))
                    metrics.append(Metric("network.packets_recv", pkt_recv_rate, "pkts/s", tags))
                    metrics.append(Metric("network.errors", error_rate, "errors/s", tags))
                    metrics.append(Metric("network.drops", drop_rate, "drops/s", tags))

        self._last_counters = counters
        self._last_time = now
        return metrics
