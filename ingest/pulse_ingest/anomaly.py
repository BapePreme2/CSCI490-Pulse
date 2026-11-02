"""Rolling baselines for anomaly detection (ANOM-01).

A series' baseline at a sample is the mean and standard deviation of that
same series' samples over the trailing `window_seconds` *before* it. The
sample itself is left out, so a spike can't pull its own baseline toward it
and hide how far it deviates. Until the window holds `min_samples` samples
there is no baseline: a standard deviation of three samples says nothing
about what's normal.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

from pulse_ingest.store import HistoryPoint

DEFAULT_WINDOW_SECONDS = 3600
MIN_WINDOW_SECONDS = 60
MAX_WINDOW_SECONDS = 24 * 3600

# Five minutes of samples at the agent's default 10s interval.
MIN_BASELINE_SAMPLES = 30


@dataclass(frozen=True)
class BaselinePoint:
    """One sample with its baseline. `mean` and `stddev` (sample, n - 1) are
    None while the window holds fewer than the minimum samples; `samples` is
    how many it held either way."""

    timestamp: float
    value: float
    samples: int
    mean: float | None = None
    stddev: float | None = None


class _Window:
    """Running mean and sum of squared deviations (Welford's method) over a
    window that samples enter and leave. Numerically far steadier than
    keeping sum(x) and sum(x^2), which cancel badly on large, steady values
    like memory in MB."""

    def __init__(self) -> None:
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0

    def add(self, x: float) -> None:
        self.count += 1
        delta = x - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (x - self.mean)

    def remove(self, x: float) -> None:
        self.count -= 1
        if self.count == 0:
            # Reset exactly, so rounding left by earlier samples can't
            # outlive a window that emptied (e.g. across a long gap).
            self.mean = 0.0
            self.m2 = 0.0
            return
        delta = x - self.mean
        self.mean -= delta / self.count
        self.m2 -= delta * (x - self.mean)

    def stddev(self) -> float:
        # m2 can drift a hair below zero through rounding when every sample
        # in the window is (nearly) equal.
        return math.sqrt(max(self.m2, 0.0) / (self.count - 1))


def rolling_baseline(
    points: Sequence[HistoryPoint],
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    min_samples: int = MIN_BASELINE_SAMPLES,
    start: float | None = None,
) -> list[BaselinePoint]:
    """The baseline at each of one series' samples, oldest first.

    `points` must be in timestamp order. The baseline at a sample taken at
    time t covers the samples in [t - window_seconds, t). Samples before
    `start` only feed later baselines and are left out of the result, so to
    have a baseline from `start` onward, pass samples from
    `start - window_seconds`.

    One pass with each sample entering and leaving the window once, so a
    week of 10s samples is no slower per sample than an hour of them.
    """
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive")
    if min_samples < 2:
        raise ValueError("min_samples must be at least 2 (a standard deviation needs two samples)")

    window = _Window()
    in_window: deque[HistoryPoint] = deque()
    result: list[BaselinePoint] = []
    for point in points:
        while in_window and in_window[0].timestamp < point.timestamp - window_seconds:
            window.remove(in_window.popleft().value)

        if start is None or point.timestamp >= start:
            if window.count >= min_samples:
                result.append(
                    BaselinePoint(point.timestamp, point.value, window.count, window.mean, window.stddev())
                )
            else:
                result.append(BaselinePoint(point.timestamp, point.value, window.count))

        window.add(point.value)
        in_window.append(point)
    return result
