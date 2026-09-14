from __future__ import annotations

import psutil

from pulse_agent.metrics import Metric


class LoadAverageCollector:
    """Collects the 1/5/15 minute system load averages."""

    def collect(self) -> list[Metric]:
        one, five, fifteen = psutil.getloadavg()
        return [
            Metric(name="load.avg", value=one, unit="load", tags={"window": "1m"}),
            Metric(name="load.avg", value=five, unit="load", tags={"window": "5m"}),
            Metric(name="load.avg", value=fifteen, unit="load", tags={"window": "15m"}),
        ]
