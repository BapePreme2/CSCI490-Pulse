from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Metric:
    """A single measurement collected from the host."""

    name: str
    value: float
    unit: str
    tags: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "tags": self.tags,
            "timestamp": self.timestamp,
        }
