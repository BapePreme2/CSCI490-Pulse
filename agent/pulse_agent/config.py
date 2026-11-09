from __future__ import annotations

import socket
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from pulse_agent.collectors.ports import DEFAULT_SENSITIVE_PORTS


class ConfigError(Exception):
    """Raised when the agent's config file is missing or invalid."""


# "host" is always derived from `hostname`; "environment" has its own
# top-level config field. Allowing either inside `tags` too would be
# confusing (which one wins?), so it's rejected at load time instead.
_RESERVED_TAG_KEYS = {"host", "environment"}


@dataclass
class AgentConfig:
    endpoint: str
    api_key: str
    interval_seconds: float = 10.0
    hostname: str = field(default_factory=socket.gethostname)
    environment: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    buffer_max_batches: int = 100
    request_timeout_seconds: float = 10.0
    sensitive_ports: tuple[int, ...] = DEFAULT_SENSITIVE_PORTS

    @classmethod
    def from_dict(cls, data: dict) -> "AgentConfig":
        missing = [key for key in ("endpoint", "api_key") if not data.get(key)]
        if missing:
            raise ConfigError(f"Missing required config field(s): {', '.join(missing)}")

        endpoint = str(data["endpoint"])
        if not endpoint.startswith(("http://", "https://")):
            raise ConfigError(f"endpoint must start with http:// or https://, got: {endpoint}")

        timeout = float(data.get("request_timeout_seconds", 10.0))
        if timeout <= 0:
            raise ConfigError("request_timeout_seconds must be greater than 0")

        environment = data.get("environment")
        if environment is not None:
            environment = str(environment)

        return cls(
            endpoint=endpoint,
            api_key=data["api_key"],
            interval_seconds=float(data.get("interval_seconds", 10.0)),
            hostname=data.get("hostname") or socket.gethostname(),
            environment=environment,
            tags=_validate_tags(data.get("tags") or {}),
            buffer_max_batches=int(data.get("buffer_max_batches", 100)),
            request_timeout_seconds=timeout,
            sensitive_ports=_validate_ports(data.get("sensitive_ports", DEFAULT_SENSITIVE_PORTS)),
        )


def _validate_ports(raw: object) -> tuple[int, ...]:
    # null means "use the defaults"; an empty list turns exposure flagging off.
    if raw is None:
        return DEFAULT_SENSITIVE_PORTS
    if not isinstance(raw, (list, tuple)):
        raise ConfigError(f"sensitive_ports must be a list of port numbers, got: {raw!r}")
    ports: list[int] = []
    for port in raw:
        # bool is an int subclass; `true` in YAML is almost certainly a typo.
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ConfigError(f"sensitive_ports entries must be port numbers 1-65535, got: {port!r}")
        ports.append(port)
    return tuple(sorted(set(ports)))


def _validate_tags(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ConfigError(f"tags must be a mapping of key: value, got: {raw!r}")

    tags: dict[str, str] = {}
    for key, value in raw.items():
        key = str(key)
        if not key:
            raise ConfigError("tags keys must not be empty")
        if key in _RESERVED_TAG_KEYS:
            where = "the hostname" if key == "host" else "the top-level environment field"
            raise ConfigError(f"tags cannot include '{key}'; it is set automatically from {where}")
        tags[key] = str(value)
    return tags


def load_config(path: str | Path) -> AgentConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    with path.open("r") as f:
        raw = yaml.safe_load(f) or {}

    if not isinstance(raw, dict):
        raise ConfigError(f"Config file must contain a YAML mapping: {path}")

    return AgentConfig.from_dict(raw)
