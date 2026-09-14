from __future__ import annotations

import psutil

from pulse_agent.metrics import Metric


class CPUCollector:
    """Collects overall and per-core CPU utilization.

    psutil.cpu_percent() measures usage since its *previous* call, so the
    very first reading is meaningless. This collector eats that first call
    as a priming step and returns no metrics for it.
    """

    def __init__(self) -> None:
        self._primed = False

    def collect(self) -> list[Metric]:
        if not self._primed:
            psutil.cpu_percent(percpu=False)
            psutil.cpu_percent(percpu=True)
            self._primed = True
            return []

        metrics: list[Metric] = [
            Metric(name="cpu.usage", value=psutil.cpu_percent(percpu=False), unit="percent")
        ]
        for core_index, pct in enumerate(psutil.cpu_percent(percpu=True)):
            metrics.append(
                Metric(name="cpu.usage", value=pct, unit="percent", tags={"core": str(core_index)})
            )
        return metrics
