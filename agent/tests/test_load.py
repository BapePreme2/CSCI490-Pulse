from unittest.mock import patch

from pulse_agent.collectors.load import LoadAverageCollector


def test_collect_returns_all_three_windows():
    with patch("pulse_agent.collectors.load.psutil.getloadavg", return_value=(1.0, 2.0, 3.0)):
        metrics = LoadAverageCollector().collect()

    assert [m.tags["window"] for m in metrics] == ["1m", "5m", "15m"]
    assert [m.value for m in metrics] == [1.0, 2.0, 3.0]
    assert all(m.name == "load.avg" for m in metrics)
