from __future__ import annotations

import ipaddress
import logging
import socket
import time
from dataclasses import dataclass, field

import psutil

from pulse_agent.metrics import Metric

logger = logging.getLogger(__name__)

# Joined process names go into a tag, so keep them well under the API's
# 200-character tag limit even for a port shared by many workers.
_MAX_PROCESS_TAG_LEN = 64

# Services that are rarely meant to be open to a network, and are common
# targets when they are (SEC-02): remote access, file sharing, databases and
# caches, and unauthenticated admin APIs. Web ports (80/443) are left out on
# purpose: serving them to the network is usually the point.
DEFAULT_SENSITIVE_PORTS: tuple[int, ...] = (
    21,     # FTP
    22,     # SSH
    23,     # Telnet
    25,     # SMTP
    445,    # SMB
    1433,   # SQL Server
    2375,   # Docker API (unencrypted)
    3306,   # MySQL
    3389,   # RDP
    5432,   # PostgreSQL
    5900,   # VNC
    6379,   # Redis
    9200,   # Elasticsearch
    11211,  # Memcached
    27017,  # MongoDB
)


@dataclass
class _Listener:
    protocol: str
    address: str
    port: int
    processes: set[str] = field(default_factory=set)

    @property
    def network_reachable(self) -> bool:
        """Whether other machines could connect: bound to every interface
        (0.0.0.0 / ::) or to any specific non-loopback address. An address
        that can't be parsed counts as reachable, so it gets flagged rather
        than missed."""
        try:
            ip = ipaddress.ip_address(self.address.split("%", 1)[0])
        except ValueError:
            return True
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return not ip.is_loopback


class ListeningPortsCollector:
    """Reports every TCP/UDP socket the host is listening on (SEC-01) and
    flags the sensitive ones other machines can reach (SEC-02).

    Each listening socket becomes one `network.listening_port` sample
    (value 1) tagged with its protocol, bind address, port, and owning
    process when the agent is allowed to see it. A `network.listening_ports`
    count is also sent every cycle, even when it is 0, and shares its
    timestamp with that cycle's port samples, so a reader can tell which
    ports are still open: a port that closes just stops being reported.

    A port in `sensitive_ports` that is reachable from the network also
    gets an `exposed: "true"` tag, and `network.exposed_services` counts
    those every cycle (as distinct protocol/port pairs, so a service bound
    on both IPv4 and IPv6 counts once). A threshold rule on that count
    (`> 0`) alerts on exposure with no new alerting code.

    The metric pipeline only stores numbers, so a port list is sent as one
    series per port instead of a new payload type. That way ingest, storage,
    and alert rules need no changes.
    """

    def __init__(self, sensitive_ports: tuple[int, ...] | frozenset[int] = DEFAULT_SENSITIVE_PORTS) -> None:
        self._sensitive_ports = frozenset(sensitive_ports)
        self._warned_partial = False

    def collect(self) -> list[Metric]:
        listeners = self._listeners()
        if listeners is None:
            return []

        now = time.time()
        exposed = {(l.protocol, l.port) for l in listeners.values() if self._is_exposed(l)}
        metrics = [
            Metric(
                "network.listening_port",
                1.0,
                "bool",
                self._tags(listener),
                timestamp=now,
            )
            for listener in sorted(listeners.values(), key=lambda l: (l.port, l.protocol, l.address))
        ]
        metrics.append(Metric("network.listening_ports", float(len(listeners)), "count", timestamp=now))
        metrics.append(Metric("network.exposed_services", float(len(exposed)), "count", timestamp=now))
        return metrics

    def _is_exposed(self, listener: _Listener) -> bool:
        return listener.port in self._sensitive_ports and listener.network_reachable

    def _tags(self, listener: _Listener) -> dict[str, str]:
        tags = {
            "protocol": listener.protocol,
            "address": listener.address,
            "port": str(listener.port),
        }
        if listener.processes:
            tags["process"] = ",".join(sorted(listener.processes))[:_MAX_PROCESS_TAG_LEN]
        if self._is_exposed(listener):
            tags["exposed"] = "true"
        return tags

    def _listeners(self) -> dict[tuple[str, str, int], _Listener] | None:
        """Listening sockets keyed by (protocol, address, port), or None if
        none could be read at all."""
        try:
            # (connection, pid) pairs; a pid is None when the socket belongs
            # to a process the agent isn't allowed to inspect.
            pairs = [(c, c.pid) for c in psutil.net_connections(kind="inet")]
        except psutil.AccessDenied:
            # macOS (and some hardened Linux setups) only let root list every
            # socket. Fall back to the sockets of processes we can inspect,
            # which is at least the agent user's own.
            pairs = self._per_process_connections()
            if pairs is None:
                return None
            if not self._warned_partial:
                logger.warning(
                    "Not permitted to list all sockets; reporting listening ports "
                    "only for processes this agent can inspect (run as root for all)."
                )
                self._warned_partial = True

        listeners: dict[tuple[str, str, int], _Listener] = {}
        names: dict[int, str | None] = {}
        for conn, pid in pairs:
            protocol = self._listening_protocol(conn)
            if protocol is None:
                continue
            address, port = conn.laddr.ip, conn.laddr.port
            listener = listeners.setdefault(
                (protocol, address, port), _Listener(protocol, address, port)
            )
            if pid is not None:
                if pid not in names:
                    names[pid] = self._process_name(pid)
                if names[pid]:
                    listener.processes.add(names[pid])
        return listeners

    @staticmethod
    def _listening_protocol(conn) -> str | None:
        """"tcp"/"udp" if conn is a listening socket, else None. A TCP socket
        listens in the LISTEN state; a UDP socket has no state, so one that's
        bound to a local port and has no remote peer counts as listening."""
        if not conn.laddr:
            return None
        if conn.type == socket.SOCK_STREAM and conn.status == psutil.CONN_LISTEN:
            return "tcp"
        if conn.type == socket.SOCK_DGRAM and not conn.raddr:
            return "udp"
        return None

    @staticmethod
    def _process_name(pid: int) -> str | None:
        try:
            return psutil.Process(pid).name() or None
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return None

    @staticmethod
    def _per_process_connections() -> list[tuple[object, int]] | None:
        pairs: list[tuple[object, int]] = []
        any_readable = False
        for proc in psutil.process_iter():
            try:
                conns = proc.net_connections(kind="inet")
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            any_readable = True
            pairs.extend((c, proc.pid) for c in conns)
        return pairs if any_readable else None
