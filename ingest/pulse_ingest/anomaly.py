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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from pulse_ingest.schemas import DEFAULT_BASELINE_SECONDS, MAX_BASELINE_SECONDS, MIN_BASELINE_SECONDS
from pulse_ingest.store import HistoryPoint

# One set of limits for the baseline endpoint's window and anomaly rules'
# baseline_seconds.
DEFAULT_WINDOW_SECONDS = DEFAULT_BASELINE_SECONDS
MIN_WINDOW_SECONDS = MIN_BASELINE_SECONDS
MAX_WINDOW_SECONDS = MAX_BASELINE_SECONDS

# Five minutes of samples at the agent's default 10s interval.
MIN_BASELINE_SAMPLES = 30

# A series that barely moves (disk.usage, memory.total) has a standard
# deviation at or near zero, so any wobble at all would be "many sigma" away.
# Deviations are measured in at least this fraction of the baseline mean.
MIN_RELATIVE_SPREAD = 0.01

# The dashboard's anomaly markers (DASH-10) flag samples at least this many
# sigma from their baseline: the same default as a new anomaly rule.
DEFAULT_SIGMA = 3.0
MAX_SIGMA = 100.0

Direction = Literal["above", "below"]


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


@dataclass(frozen=True)
class Baseline:
    """The mean and sample stddev (n - 1) of a set of samples."""

    samples: int
    mean: float
    stddev: float


def baseline(values: Iterable[float], min_samples: int = MIN_BASELINE_SAMPLES) -> Baseline | None:
    """The baseline of these values, or None if there are fewer than
    `min_samples` of them."""
    if min_samples < 2:
        raise ValueError("min_samples must be at least 2 (a standard deviation needs two samples)")
    window = _Window()
    for value in values:
        window.add(value)
    if window.count < min_samples:
        return None
    return Baseline(window.count, window.mean, window.stddev())


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


def spread(mean: float, stddev: float) -> float:
    """The standard deviation a deviation is measured in: the baseline's own,
    but at least MIN_RELATIVE_SPREAD of its mean. 0 only for a baseline that
    was constantly 0."""
    return max(stddev, MIN_RELATIVE_SPREAD * abs(mean))


def deviation(value: float, mean: float, stddev: float) -> float:
    """How many standard deviations (see `spread`) `value` is from `mean`:
    positive above it, negative below.

    Against a baseline that was constantly 0 (network.errors on a healthy
    host), any other value is infinitely far off, so it's +/-inf rather than
    a division by zero. Not JSON-safe; never put it in a response as is.
    """
    diff = value - mean
    s = spread(mean, stddev)
    if s == 0:
        return 0.0 if diff == 0 else math.copysign(math.inf, diff)
    return diff / s


@dataclass(frozen=True)
class AnomalousPoint:
    """One sample at least N sigma from its baseline. `spread` is what the
    deviation was measured in (see `spread`); `deviation` is +/-inf against a
    baseline that was constantly 0."""

    timestamp: float
    value: float
    mean: float
    spread: float
    deviation: float


@dataclass(frozen=True)
class Anomaly:
    """A run of one series' consecutive samples, each at least N sigma from
    its own baseline, all in the same direction."""

    direction: Direction
    points: tuple[AnomalousPoint, ...]

    @property
    def start(self) -> float:
        return self.points[0].timestamp

    @property
    def end(self) -> float:
        return self.points[-1].timestamp


def find_anomalies(points: Iterable[BaselinePoint], sigma: float = DEFAULT_SIGMA) -> list[Anomaly]:
    """The runs of samples in one series' `rolling_baseline` that are at
    least `sigma` from their baseline, oldest first.

    Each sample is judged against its own rolling baseline, so the verdict on
    a sample depends only on the window before it, never on which range is
    on screen. A lasting change is flagged until it has pulled the baseline
    up behind it (roughly window / sigma^2: about 7 minutes at 3 sigma over an
    hour). An anomaly rule's open alert keeps the baseline it opened with
    instead, so it stays firing. A run ends at a sample that's within sigma,
    past it in the other direction, or has no baseline yet.
    """
    if not 0 < sigma <= MAX_SIGMA:
        raise ValueError(f"sigma must be more than 0 and at most {MAX_SIGMA:g}")

    runs: list[Anomaly] = []
    run: list[AnomalousPoint] = []
    run_direction: Direction = "above"
    for p in points:
        flagged: Direction | None = None
        if p.mean is not None and p.stddev is not None:
            d = deviation(p.value, p.mean, p.stddev)
            if d >= sigma:
                flagged = "above"
            elif d <= -sigma:
                flagged = "below"
        if run and flagged != run_direction:
            runs.append(Anomaly(run_direction, tuple(run)))
            run = []
        if flagged is not None:
            run_direction = flagged
            run.append(AnomalousPoint(p.timestamp, p.value, p.mean, spread(p.mean, p.stddev), d))
    if run:
        runs.append(Anomaly(run_direction, tuple(run)))
    return runs
