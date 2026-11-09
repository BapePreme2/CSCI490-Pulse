import socket
from collections import namedtuple
from unittest.mock import MagicMock, patch

import psutil

from pulse_agent.collectors.ports import ListeningPortsCollector

Addr = namedtuple("Addr", "ip port")
Conn = namedtuple("Conn", "fd family type laddr raddr status pid")

MODULE = "pulse_agent.collectors.ports.psutil"


def tcp(ip, port, status=psutil.CONN_LISTEN, pid=None, raddr=()):
    return Conn(3, socket.AF_INET, socket.SOCK_STREAM, Addr(ip, port), raddr, status, pid)


def udp(ip, port, pid=None, raddr=()):
    return Conn(4, socket.AF_INET, socket.SOCK_DGRAM, Addr(ip, port), raddr, psutil.CONN_NONE, pid)


def collect(conns, names=None):
    names = names or {}

    def process(pid):
        proc = MagicMock()
        proc.name.return_value = names[pid]
        return proc

    with patch(f"{MODULE}.net_connections", return_value=conns), patch(f"{MODULE}.Process", side_effect=process):
        return ListeningPortsCollector().collect()


def ports(metrics):
    return [m.tags for m in metrics if m.name == "network.listening_port"]


def count(metrics):
    return next(m.value for m in metrics if m.name == "network.listening_ports")


def test_reports_listening_tcp_and_bound_udp_sockets_sorted_by_port():
    metrics = collect(
        [
            tcp("0.0.0.0", 5432, pid=10),
            tcp("127.0.0.1", 22),
            udp("0.0.0.0", 53),
        ],
        names={10: "postgres"},
    )

    assert ports(metrics) == [
        {"protocol": "tcp", "address": "127.0.0.1", "port": "22"},
        {"protocol": "udp", "address": "0.0.0.0", "port": "53"},
        {"protocol": "tcp", "address": "0.0.0.0", "port": "5432", "process": "postgres"},
    ]
    assert count(metrics) == 3
    assert all(m.value == 1.0 and m.unit == "bool" for m in metrics if m.name == "network.listening_port")


def test_ignores_established_tcp_and_connected_udp_sockets():
    metrics = collect(
        [
            tcp("10.0.0.5", 51000, status=psutil.CONN_ESTABLISHED, raddr=Addr("1.2.3.4", 443)),
            udp("10.0.0.5", 40000, raddr=Addr("8.8.8.8", 53)),
            tcp("0.0.0.0", 80),
        ]
    )

    assert [t["port"] for t in ports(metrics)] == ["80"]
    assert count(metrics) == 1


def test_still_sends_a_zero_count_when_nothing_is_listening():
    metrics = collect([tcp("10.0.0.5", 51000, status=psutil.CONN_ESTABLISHED, raddr=Addr("1.2.3.4", 443))])

    assert ports(metrics) == []
    assert count(metrics) == 0


def test_merges_duplicate_sockets_and_joins_their_process_names():
    # e.g. a pre-forking server with several workers on the same port
    metrics = collect(
        [tcp("0.0.0.0", 80, pid=2), tcp("0.0.0.0", 80, pid=1), tcp("0.0.0.0", 80, pid=3)],
        names={1: "nginx", 2: "nginx", 3: "certbot"},
    )

    assert ports(metrics) == [{"protocol": "tcp", "address": "0.0.0.0", "port": "80", "process": "certbot,nginx"}]
    assert count(metrics) == 1


def test_ipv4_and_ipv6_binds_of_one_port_are_separate_listeners():
    metrics = collect([tcp("0.0.0.0", 22), tcp("::", 22)])

    assert sorted(t["address"] for t in ports(metrics)) == ["0.0.0.0", "::"]


def test_omits_process_tag_when_the_process_cannot_be_inspected():
    def denied(pid):
        raise psutil.AccessDenied(pid)

    with patch(f"{MODULE}.net_connections", return_value=[tcp("0.0.0.0", 22, pid=1)]), patch(
        f"{MODULE}.Process", side_effect=denied
    ):
        metrics = ListeningPortsCollector().collect()

    assert ports(metrics) == [{"protocol": "tcp", "address": "0.0.0.0", "port": "22"}]


def test_port_samples_share_the_count_timestamp():
    with patch("pulse_agent.collectors.ports.time.time", return_value=1000.0):
        metrics = collect([tcp("0.0.0.0", 22), tcp("0.0.0.0", 80)])

    assert {m.timestamp for m in metrics} == {1000.0}


def test_falls_back_to_per_process_sockets_when_listing_all_is_denied():
    readable = MagicMock(pid=7)
    readable.net_connections.return_value = [tcp("127.0.0.1", 8000)]
    readable.name.return_value = "uvicorn"
    unreadable = MagicMock(pid=8)
    unreadable.net_connections.side_effect = psutil.AccessDenied(8)

    with patch(f"{MODULE}.net_connections", side_effect=psutil.AccessDenied()), patch(
        f"{MODULE}.process_iter", return_value=[readable, unreadable]
    ), patch(f"{MODULE}.Process", return_value=readable):
        metrics = ListeningPortsCollector().collect()

    assert ports(metrics) == [{"protocol": "tcp", "address": "127.0.0.1", "port": "8000", "process": "uvicorn"}]


def test_reports_nothing_when_no_sockets_can_be_read_at_all():
    unreadable = MagicMock(pid=8)
    unreadable.net_connections.side_effect = psutil.AccessDenied(8)

    with patch(f"{MODULE}.net_connections", side_effect=psutil.AccessDenied()), patch(
        f"{MODULE}.process_iter", return_value=[unreadable]
    ):
        assert ListeningPortsCollector().collect() == []
