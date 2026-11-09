from __future__ import annotations

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


@dataclass
class _Listener:
    protocol: str
    address: str
    port: int
    processes: set[str] = field(default_factory=set)


class ListeningPortsCollector:
    """Reports every TCP/UDP socket the host is listening on (SEC-01).

    Each listening socket becomes one `network.listening_port` sample
    (value 1) tagged with its protocol, bind address, port, and owning
    process when the agent is allowed to see it. A `network.listening_ports`
    count is also sent every cycle, even when it is 0, and shares its
    timestamp with that cycle's port samples, so a reader can tell which
    ports are still open: a port that closes just stops being reported.

    The metric pipeline only stores numbers, so a port list is sent as one
    series per port instead of a new payload type. That way ingest, storage,
    and alert rules need no changes.
    """

    def __init__(self) -> None:
        self._warned_partial = False

    def collect(self) -> list[Metric]:
        listeners = self._listeners()
        if listeners is None:
            return []

        now = time.time()
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
        return metrics

    @staticmethod
    def _tags(listener: _Listener) -> dict[str, str]:
        tags = {
            "protocol": listener.protocol,
            "address": listener.address,
            "port": str(listener.port),
        }
        if listener.processes:
            tags["process"] = ",".join(sorted(listener.processes))[:_MAX_PROCESS_TAG_LEN]
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
