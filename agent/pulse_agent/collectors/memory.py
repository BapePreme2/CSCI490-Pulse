from __future__ import annotations

import psutil

from pulse_agent.metrics import Metric

_BYTES_PER_MB = 1024 * 1024


class MemoryCollector:
    """Collects RAM and swap usage."""

    def collect(self) -> list[Metric]:
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return [
            Metric(name="memory.total", value=vm.total / _BYTES_PER_MB, unit="MB"),
            Metric(name="memory.used", value=vm.used / _BYTES_PER_MB, unit="MB"),
            Metric(name="memory.free", value=vm.free / _BYTES_PER_MB, unit="MB"),
            Metric(name="memory.available", value=vm.available / _BYTES_PER_MB, unit="MB"),
            Metric(name="memory.swap_used", value=swap.used / _BYTES_PER_MB, unit="MB"),
            Metric(name="memory.swap_free", value=swap.free / _BYTES_PER_MB, unit="MB"),
        ]
