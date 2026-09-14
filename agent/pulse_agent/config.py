from __future__ import annotations

import socket
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(Exception):
    """Raised when the agent's config file is missing or invalid."""


@dataclass
class AgentConfig:
    endpoint: str
    api_key: str
    interval_seconds: float = 10.0
    hostname: str = field(default_factory=socket.gethostname)
    tags: dict[str, str] = field(default_factory=dict)
    buffer_max_batches: int = 100

    @classmethod
    def from_dict(cls, data: dict) -> "AgentConfig":
        missing = [key for key in ("endpoint", "api_key") if not data.get(key)]
        if missing:
            raise ConfigError(f"Missing required config field(s): {', '.join(missing)}")

        return cls(
            endpoint=data["endpoint"],
            api_key=data["api_key"],
            interval_seconds=float(data.get("interval_seconds", 10.0)),
            hostname=data.get("hostname") or socket.gethostname(),
            tags=data.get("tags") or {},
            buffer_max_batches=int(data.get("buffer_max_batches", 100)),
        )


def load_config(path: str | Path) -> AgentConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    with path.open("r") as f:
        raw = yaml.safe_load(f) or {}

    if not isinstance(raw, dict):
        raise ConfigError(f"Config file must contain a YAML mapping: {path}")

    return AgentConfig.from_dict(raw)
