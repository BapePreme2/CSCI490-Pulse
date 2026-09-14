from pulse_agent.buffer import MetricBuffer
from pulse_agent.metrics import Metric


class StubSender:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def send(self, metrics):
        self.calls.append(list(metrics))
        return self._results.pop(0)


def make_metric(name="cpu.usage"):
    return Metric(name=name, value=1.0, unit="percent")


def test_flush_with_empty_buffer_is_noop_success():
    buffer = MetricBuffer()
    sender = StubSender([])

    assert buffer.flush(sender) is True
    assert sender.calls == []


def test_flush_clears_buffer_on_success():
    buffer = MetricBuffer()
    buffer.add([make_metric()])
    sender = StubSender([True])

    assert buffer.flush(sender) is True
    assert len(buffer) == 0


def test_flush_keeps_metrics_and_backs_off_on_failure():
    buffer = MetricBuffer(base_backoff_seconds=1.0)
    buffer.add([make_metric()])
    sender = StubSender([False])

    assert buffer.flush(sender, now=0.0) is False
    assert len(buffer) == 1

    # Retrying immediately is suppressed by the backoff window.
    assert buffer.flush(sender, now=0.5) is False
    assert len(sender.calls) == 1

    # After the backoff window elapses, it retries and can succeed.
    sender._results = [True]
    assert buffer.flush(sender, now=2.0) is True
    assert len(buffer) == 0


def test_backoff_grows_exponentially_on_repeated_failure():
    buffer = MetricBuffer(base_backoff_seconds=1.0, max_backoff_seconds=100.0)
    buffer.add([make_metric()])
    sender = StubSender([False, False, False])

    buffer.flush(sender, now=0.0)
    assert buffer._next_attempt_time == 1.0

    buffer.flush(sender, now=1.0)
    assert buffer._next_attempt_time == 3.0

    buffer.flush(sender, now=3.0)
    assert buffer._next_attempt_time == 7.0


def test_sender_exception_is_treated_as_failure():
    class ExplodingSender:
        def send(self, metrics):
            raise RuntimeError("network down")

    buffer = MetricBuffer()
    buffer.add([make_metric()])

    assert buffer.flush(ExplodingSender(), now=0.0) is False
    assert len(buffer) == 1


def test_buffer_drops_oldest_when_over_capacity():
    buffer = MetricBuffer(max_batches=1, metrics_per_batch_estimate=50)
    for i in range(60):
        buffer.add([make_metric(name=f"m{i}")])

    assert len(buffer) == 50
    remaining_names = {m.name for m in buffer._pending}
    assert "m0" not in remaining_names
    assert "m59" in remaining_names
