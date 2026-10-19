"""Alert notifications: an email when an alert fires and when it resolves."""

from __future__ import annotations

import logging
import os
import queue
import smtplib
import ssl
import textwrap
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from typing import Literal, Protocol

from pulse_ingest.alerts import series_label
from pulse_ingest.store import Alert, AlertChange

logger = logging.getLogger(__name__)

# Pending and cancelled alerts never fired, so nobody is told about them.
NOTIFY_KINDS = frozenset({"fired", "resolved"})

SmtpSecurity = Literal["starttls", "ssl", "none"]
DEFAULT_SMTP_PORTS: dict[str, int] = {"starttls": 587, "ssl": 465, "none": 25}
SMTP_TIMEOUT_SECONDS = 10.0

EMAIL_ENV_VARS = (
    "PULSE_SMTP_HOST",
    "PULSE_SMTP_PORT",
    "PULSE_SMTP_SECURITY",
    "PULSE_SMTP_USERNAME",
    "PULSE_SMTP_PASSWORD",
    "PULSE_ALERT_EMAIL_FROM",
    "PULSE_ALERT_EMAIL_TO",
)


@dataclass(frozen=True)
class EmailConfig:
    host: str
    port: int
    security: SmtpSecurity
    sender: str
    recipients: tuple[str, ...]
    username: str | None = None
    password: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> EmailConfig | None:
        """The email settings, or None if email is off (none of the variables
        are set). Setting some but not all of them raises ValueError, so a
        typo stops the API at startup instead of silently sending nothing."""
        env = os.environ if env is None else env

        def get(name: str) -> str | None:
            return env.get(name, "").strip() or None

        if not any(get(name) for name in EMAIL_ENV_VARS):
            return None

        required = {name: get(name) for name in ("PULSE_SMTP_HOST", "PULSE_ALERT_EMAIL_FROM", "PULSE_ALERT_EMAIL_TO")}
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError(
                f"Email notifications are only partly configured; also set {', '.join(missing)} "
                "(or unset every PULSE_SMTP_* and PULSE_ALERT_EMAIL_* variable to turn email off)"
            )

        security = (get("PULSE_SMTP_SECURITY") or "starttls").lower()
        if security not in DEFAULT_SMTP_PORTS:
            raise ValueError(f"PULSE_SMTP_SECURITY must be starttls, ssl, or none, not {security!r}")

        port_text = get("PULSE_SMTP_PORT")
        port = DEFAULT_SMTP_PORTS[security] if port_text is None else _port(port_text)

        # Passwords aren't stripped: spaces can be part of one.
        username, password = get("PULSE_SMTP_USERNAME"), env.get("PULSE_SMTP_PASSWORD") or None
        if (username is None) != (password is None):
            raise ValueError("Set both PULSE_SMTP_USERNAME and PULSE_SMTP_PASSWORD, or neither")
        if password is not None and security == "none":
            logger.warning("PULSE_SMTP_SECURITY=none sends the SMTP password unencrypted")

        sender = required["PULSE_ALERT_EMAIL_FROM"]
        recipients = tuple(address.strip() for address in required["PULSE_ALERT_EMAIL_TO"].split(",") if address.strip())
        for address in (sender, *recipients):
            if "@" not in parseaddr(address)[1]:
                raise ValueError(f"Not an email address: {address!r}")

        return cls(required["PULSE_SMTP_HOST"], port, security, sender, recipients, username, password)


def _port(text: str) -> int:
    try:
        port = int(text)
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise ValueError(f"PULSE_SMTP_PORT must be a port number, not {text!r}")
    return port


def build_email(change: AlertChange, config: EmailConfig, now: float) -> EmailMessage:
    """The message for a `fired` or `resolved` change (after it's stored, so
    the alert has its id)."""
    a = change.after
    if change.kind == "fired":
        status, summary = "FIRING", f"{a.rule_name} is FIRING on {a.hostname}."
    elif a.resolve_reason == "recovered":
        status, summary = "RESOLVED", f"{a.rule_name} on {a.hostname} has RESOLVED: the value is back within the threshold."
    else:
        # Closing an alert because nothing evaluates it any more is not the
        # same as the problem going away, and the email has to say so.
        status = "RESOLVED (no longer evaluated)"
        summary = (
            f"{a.rule_name} on {a.hostname} was closed because Pulse stopped evaluating it, "
            "NOT because the value recovered. Either the host stopped reporting this metric, "
            "or the rule was disabled, edited so it no longer matches, or deleted."
        )

    scope = f" ({', '.join(f'{k}={v}' for k, v in sorted(a.tags.items()))})" if a.tags else ""
    subject = f"[Pulse] {status}: {a.rule_name} on {a.hostname}{scope}"

    value_label = "Value" if change.kind == "fired" or a.resolve_reason == "recovered" else "Last value seen"
    details = [
        ("Series", series_label(a)),
        (value_label, f"{_number(a.last_value)} {a.unit}"),
        ("Condition", _condition(a)),
        ("Breaching since", _time(a.pending_since)),
        ("Fired at", _time(a.fired_at)),
    ]
    if change.kind == "resolved":
        details += [("Resolved at", _time(a.resolved_at)), ("Firing for", _duration(a.resolved_at - a.fired_at))]
    width = max(len(label) for label, _ in details) + 1
    rule = f"rule #{a.rule_id}" if a.rule_id is not None else "rule since deleted"

    message = EmailMessage()
    # Rule names, hostnames, and tags may contain line breaks, which can't
    # go in a header.
    message["Subject"] = " ".join(subject.split())
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message["Date"] = formatdate(now, usegmt=True)
    message["Message-ID"] = make_msgid("pulse-alert", domain=parseaddr(config.sender)[1].rpartition("@")[2])
    message.set_content(
        "\n".join(
            [
                textwrap.fill(summary, 72),
                "",
                *(f"  {label + ':':<{width}} {value}" for label, value in details),
                "",
                f"Alert #{a.id}, {rule}.",
            ]
        )
        + "\n"
    )
    return message


def _condition(a: Alert) -> str:
    condition = f"{a.metric_name} {a.operator} {_number(a.threshold)}"
    return f"{condition} for {_duration(a.duration_seconds)}" if a.duration_seconds > 0 else condition


def _number(value: float) -> str:
    """95.0 -> "95", 16000000000.0 -> "16000000000", 93.25 -> "93.25"."""
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _time(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _duration(seconds: float) -> str:
    """Like the dashboard's formatDuration: 300 -> "5m", 5400 -> "1h 30m"."""
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds}s"
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return " ".join(f"{n}{unit}" for n, unit in ((hours, "h"), (minutes, "m"), (secs, "s")) if n)


class Channel(Protocol):
    name: str

    def send(self, change: AlertChange, now: float) -> None:
        """Deliver one notification; raise if it didn't go out."""


class EmailChannel:
    name = "email"

    def __init__(self, config: EmailConfig) -> None:
        self.config = config

    def send(self, change: AlertChange, now: float) -> None:
        c = self.config
        message = build_email(change, c, now)
        context = ssl.create_default_context()
        if c.security == "ssl":
            smtp = smtplib.SMTP_SSL(c.host, c.port, timeout=SMTP_TIMEOUT_SECONDS, context=context)
        else:
            smtp = smtplib.SMTP(c.host, c.port, timeout=SMTP_TIMEOUT_SECONDS)
        with smtp:
            if c.security == "starttls":
                smtp.starttls(context=context)
            if c.username is not None:
                smtp.login(c.username, c.password)
            smtp.send_message(message)


_STOP = object()


class Notifier:
    """Sends notifications for fired and resolved alerts on a background
    thread, so a slow or unreachable mail server never holds up rule
    evaluation.

    Delivery is best effort: a failed send is retried after each of
    `retry_delays`, then logged as lost. Notifications are only held in
    memory, so any still queued when the process dies are lost too.
    """

    def __init__(
        self,
        channels: Sequence[Channel],
        retry_delays: Sequence[float] = (5.0, 30.0),
        max_queued: int = 1000,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._channels = list(channels)
        self._retry_delays = tuple(retry_delays)
        self._max_queued = max_queued
        self._clock = clock
        self._queue: queue.Queue = queue.Queue()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def __call__(self, change: AlertChange) -> None:
        """Queue a stored alert change; anything but fired/resolved is ignored."""
        if change.kind not in NOTIFY_KINDS:
            return
        if self._queue.qsize() >= self._max_queued:
            logger.error("Notification queue full; dropped %s notification for alert %d", change.kind, change.after.id)
            return
        self._queue.put(change)

    def start(self) -> None:
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, name="pulse-notifier", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Finish what's queued (retrying without waiting), for up to `timeout`."""
        self._stopping.set()
        self._queue.put(_STOP)
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                logger.warning("Notifier didn't finish within %ss; queued notifications may be lost", timeout)
            self._thread = None

    def _run(self) -> None:
        while (change := self._queue.get()) is not _STOP:
            for channel in self._channels:
                self._deliver(channel, change)

    def _deliver(self, channel: Channel, change: AlertChange) -> None:
        a = change.after
        for attempt, delay in enumerate((*self._retry_delays, None), start=1):
            try:
                channel.send(change, self._clock())
            except Exception as exc:
                if delay is None:
                    logger.error(
                        "Giving up on %s notification for alert %d (%s) after %d attempts: %s",
                        channel.name, a.id, change.kind, attempt, exc,
                    )
                    return
                logger.warning(
                    "%s notification for alert %d (%s) failed, retrying in %ss: %s",
                    channel.name, a.id, change.kind, f"{delay:g}", exc,
                )
                self._stopping.wait(delay)
            else:
                logger.info("Sent %s notification for alert %d (%s)", channel.name, a.id, change.kind)
                return
