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


def partitions(*parts):
    return [MagicMock(mountpoint=mount, device=device) for mount, device in parts]


def test_disk_usage_skips_bind_mounted_files_like_dockers_etc_hosts():
    real = partitions(("/", "/dev/sda1"), ("/etc/hosts", "/dev/sda1"), ("/etc/resolv.conf", "/dev/sda1"))
    usage = MagicMock(percent=10.0, free=1024**3)

    with patch("pulse_agent.collectors.disk.psutil.disk_partitions", return_value=real), patch(
        "pulse_agent.collectors.disk.psutil.disk_usage", return_value=usage
    ), patch("pulse_agent.collectors.disk.os.path.isfile", side_effect=lambda path: path.startswith("/etc/")):
        metrics = DiskUsageCollector().collect()

    assert {m.tags["mount"] for m in metrics} == {"/"}


def test_disk_usage_includes_a_containers_overlay_root():
    # In a container, all=False lists only Docker's bind-mounted files; the
    # root filesystem is an overlay, which only all=True lists.
    def disk_partitions(all=False):
        if all:
            return partitions(("/", "overlay"), ("/proc", "proc"), ("/etc/hostname", "/dev/sdd"))
        return partitions(("/etc/hostname", "/dev/sdd"))

    usage = MagicMock(percent=2.0, free=900 * 1024**3)
    with patch("pulse_agent.collectors.disk.psutil.disk_partitions", side_effect=disk_partitions), patch(
        "pulse_agent.collectors.disk.psutil.disk_usage", return_value=usage
    ), patch("pulse_agent.collectors.disk.os.path.isfile", side_effect=lambda path: path == "/etc/hostname"):
        metrics = DiskUsageCollector().collect()

    assert {(m.name, m.tags["mount"], m.tags["device"]) for m in metrics} == {
        ("disk.usage", "/", "overlay"),
        ("disk.free", "/", "overlay"),
    }


def test_disk_io_skips_loop_devices_and_ramdisks():
    collector = DiskIOCollector()
    idle = MagicMock(read_bytes=0, write_bytes=0, read_count=0, write_count=0)
    disks = {name: idle for name in ("sda", "nvme0n1", "loop0", "loop12", "ram3", "zram0")}

    with patch(
        "pulse_agent.collectors.disk.psutil.disk_io_counters", side_effect=[disks, disks]
    ), patch("pulse_agent.collectors.disk.time.time", side_effect=[0.0, 1.0]):
        collector.collect()
        metrics = collector.collect()

    assert {m.tags["device"] for m in metrics} == {"sda", "nvme0n1", "zram0"}
