from unittest.mock import MagicMock, patch

from pulse_agent.collectors.network import NetworkCollector


def test_first_call_has_no_rate():
    counters = {
        "eth0": MagicMock(
            bytes_sent=0, bytes_recv=0, packets_sent=0, packets_recv=0,
            errin=0, errout=0, dropin=0, dropout=0,
        )
    }
    with patch("pulse_agent.collectors.network.psutil.net_io_counters", return_value=counters):
        metrics = NetworkCollector().collect()

    assert metrics == []


def test_second_call_computes_rates_per_interface():
    collector = NetworkCollector()
    first = {
        "eth0": MagicMock(
            bytes_sent=0, bytes_recv=0, packets_sent=0, packets_recv=0,
            errin=0, errout=0, dropin=0, dropout=0,
        )
    }
    second = {
        "eth0": MagicMock(
            bytes_sent=1024 * 1024, bytes_recv=2 * 1024 * 1024,
            packets_sent=10, packets_recv=20,
            errin=1, errout=0, dropin=0, dropout=2,
        )
    }

    with patch(
        "pulse_agent.collectors.network.psutil.net_io_counters", side_effect=[first, second]
    ), patch("pulse_agent.collectors.network.time.time", side_effect=[0.0, 1.0]):
        collector.collect()
        metrics = collector.collect()

    by_name = {m.name: m.value for m in metrics}
    assert by_name["network.bytes_sent"] == 1.0
    assert by_name["network.bytes_recv"] == 2.0
    assert by_name["network.packets_sent"] == 10.0
    assert by_name["network.packets_recv"] == 20.0
    assert by_name["network.errors"] == 1.0
    assert by_name["network.drops"] == 2.0
    assert all(m.tags == {"interface": "eth0"} for m in metrics)
