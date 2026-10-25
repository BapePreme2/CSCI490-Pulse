from __future__ import annotations

import argparse
import logging
import signal
import sys

from pulse_agent.agent import LoggingSender, PulseAgent
from pulse_agent.config import ConfigError, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pulse-agent", description="Pulse host monitoring agent")
    parser.add_argument("--config", required=True, help="Path to the agent's YAML config file")
    parser.add_argument("--once", action="store_true", help="Collect and flush a single cycle, then exit")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log each batch instead of sending it to the ingestion API",
    )
    parser.add_argument("--log-level", default="INFO", help="Logging level (default: INFO)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1

    agent = PulseAgent(config, sender=LoggingSender() if args.dry_run else None)
    if args.once:
        agent.run_once()
    else:
        # SIGTERM is how `docker stop`, systemd, and `kill` ask a process to
        # exit, and Python only handles SIGINT itself. Worse, as PID 1 in a
        # container an unhandled SIGTERM is ignored outright, so `docker stop`
        # used to wait its full 10s and then kill the agent.
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: agent.stop())
        agent.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
