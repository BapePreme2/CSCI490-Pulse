from unittest.mock import MagicMock, patch

from pulse_agent.collectors.disk import DiskIOCollector, DiskUsageCollector


def test_disk_usage_reports_percent_and_free_per_mount():
    part = MagicMock(mountpoint="/", device="/dev/sda1")
    usage = MagicMock(percent=55.5, free=10 * 1024**3)

    with patch("pulse_agent.collectors.disk.psutil.disk_partitions", return_value=[part]), patch(
        "pulse_agent.collectors.disk.psutil.disk_usage", return_value=usage
    ):
        metrics = DiskUsageCollector().collect()

    by_name = {m.name: m for m in metrics}
    assert by_name["disk.usage"].value == 55.5
    assert by_name["disk.free"].value == 10.0
    assert by_name["disk.usage"].tags == {"mount": "/", "device": "/dev/sda1"}


def test_disk_usage_skips_unreadable_mounts():
    part = MagicMock(mountpoint="/mnt/locked", device="/dev/sdb1")

    with patch("pulse_agent.collectors.disk.psutil.disk_partitions", return_value=[part]), patch(
        "pulse_agent.collectors.disk.psutil.disk_usage", side_effect=PermissionError
    ):
        metrics = DiskUsageCollector().collect()

    assert metrics == []


def test_disk_io_first_call_has_no_rate():
    counters = {"sda": MagicMock(read_bytes=0, write_bytes=0, read_count=0, write_count=0)}
    with patch("pulse_agent.collectors.disk.psutil.disk_io_counters", return_value=counters):
        metrics = DiskIOCollector().collect()

    assert metrics == []


def test_disk_io_second_call_computes_rate():
    collector = DiskIOCollector()
    first = {"sda": MagicMock(read_bytes=0, write_bytes=0, read_count=0, write_count=0)}
    second = {"sda": MagicMock(read_bytes=1024 * 1024, write_bytes=0, read_count=100, write_count=0)}

    with patch(
        "pulse_agent.collectors.disk.psutil.disk_io_counters", side_effect=[first, second]
    ), patch("pulse_agent.collectors.disk.time.time", side_effect=[0.0, 1.0]):
        collector.collect()
        metrics = collector.collect()

    by_name = {m.name: m.value for m in metrics}
    assert by_name["disk.read_throughput"] == 1.0
    assert by_name["disk.read_iops"] == 100.0
    assert by_name["disk.write_throughput"] == 0.0
    assert by_name["disk.write_iops"] == 0.0
