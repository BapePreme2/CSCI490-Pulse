from unittest.mock import MagicMock, patch

from pulse_agent.collectors.memory import MemoryCollector


def test_collect_converts_bytes_to_mb():
    vm = MagicMock(
        total=2 * 1024 * 1024,
        used=1 * 1024 * 1024,
        free=1 * 1024 * 1024,
        available=1 * 1024 * 1024,
    )
    swap = MagicMock(used=512 * 1024, free=256 * 1024)

    with patch("pulse_agent.collectors.memory.psutil.virtual_memory", return_value=vm), patch(
        "pulse_agent.collectors.memory.psutil.swap_memory", return_value=swap
    ):
        metrics = MemoryCollector().collect()

    by_name = {m.name: m.value for m in metrics}
    assert by_name["memory.total"] == 2.0
    assert by_name["memory.used"] == 1.0
    assert by_name["memory.free"] == 1.0
    assert by_name["memory.available"] == 1.0
    assert by_name["memory.swap_used"] == 0.5
    assert by_name["memory.swap_free"] == 0.25
