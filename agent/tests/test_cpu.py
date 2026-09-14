from unittest.mock import patch

from pulse_agent.collectors.cpu import CPUCollector


def test_first_collect_primes_and_returns_nothing():
    with patch("pulse_agent.collectors.cpu.psutil.cpu_percent") as mock_cpu:
        metrics = CPUCollector().collect()

    assert metrics == []
    assert mock_cpu.call_count == 2


def test_second_collect_returns_overall_and_per_core():
    collector = CPUCollector()
    with patch("pulse_agent.collectors.cpu.psutil.cpu_percent"):
        collector.collect()  # priming call

    with patch(
        "pulse_agent.collectors.cpu.psutil.cpu_percent",
        side_effect=[42.0, [10.0, 20.0, 30.0, 40.0]],
    ):
        metrics = collector.collect()

    assert len(metrics) == 5
    assert metrics[0].name == "cpu.usage"
    assert metrics[0].value == 42.0
    assert metrics[0].tags == {}

    per_core_values = [10.0, 20.0, 30.0, 40.0]
    for index, metric in enumerate(metrics[1:]):
        assert metric.tags == {"core": str(index)}
        assert metric.value == per_core_values[index]
