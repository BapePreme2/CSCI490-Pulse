"""Alert notifications: an email and/or a Slack message when an alert fires
and when it resolves."""

from __future__ import annotations

import json
import logging
import os
import queue
import smtplib
import ssl
import textwrap
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

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

SLACK_ENV_VAR = "PULSE_SLACK_WEBHOOK_URL"
SLACK_TIMEOUT_SECONDS = 10.0
# Slack rejects the whole message if a block's text is longer than these.
SLACK_SECTION_LIMIT = 3000
SLACK_FIELD_LIMIT = 2000

# Every variable a channel reads, so tests can keep a developer's real
# settings out of the API they start.
NOTIFY_ENV_VARS = (*EMAIL_ENV_VARS, SLACK_ENV_VAR)


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


@dataclass(frozen=True)
class SlackConfig:
    # The URL is the credential: anyone who has it can post to the channel.
    webhook_url: str = field(repr=False)

    @property
    def host(self) -> str:
        """The webhook's host, the only part of the URL that's safe to log."""
        return urlsplit(self.webhook_url).hostname or ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SlackConfig | None:
        """The Slack settings, or None if Slack is off (the variable isn't set)."""
        env = os.environ if env is None else env
        url = env.get(SLACK_ENV_VAR, "").strip()
        if not url:
            return None
        # The errors don't repeat the URL, since it's a secret.
        parts = urlsplit(url)
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise ValueError(f"{SLACK_ENV_VAR} must be a webhook URL starting with https://")
        if parts.scheme == "http":
            logger.warning("%s is http://, so the webhook URL (its secret) is sent unencrypted", SLACK_ENV_VAR)
        return cls(url)


AlertOutcome = Literal["firing", "recovered", "not_evaluated"]


@dataclass(frozen=True)
class Notice:
    """What a notification says about a `fired` or `resolved` change, for
    each channel to lay out its own way."""

    outcome: AlertOutcome
    subject: str
    summary: str
    details: list[tuple[str, str]]
    footer: str


def describe(change: AlertChange) -> Notice:
    """The notice for a `fired` or `resolved` change (after it's stored, so
    the alert has its id)."""
    a = change.after
    outcome: AlertOutcome
    if change.kind == "fired":
        outcome, status, summary = "firing", "FIRING", f"{a.rule_name} is FIRING on {a.hostname}."
    elif a.resolve_reason == "recovered":
        outcome, status = "recovered", "RESOLVED"
        summary = f"{a.rule_name} on {a.hostname} has RESOLVED: the value is back within the threshold."
    else:
        # Closing an alert because nothing evaluates it any more is not the
        # same as the problem going away, and the notice has to say so.
        outcome, status = "not_evaluated", "RESOLVED (no longer evaluated)"
        summary = (
            f"{a.rule_name} on {a.hostname} was closed because Pulse stopped evaluating it, "
            "NOT because the value recovered. Either the host stopped reporting this metric, "
            "or the rule was disabled, edited so it no longer matches, or deleted."
        )

    scope = f" ({', '.join(f'{k}={v}' for k, v in sorted(a.tags.items()))})" if a.tags else ""
    # One line: rule names, hostnames, and tags may contain line breaks,
    # which can't go in an email header.
    subject = " ".join(f"[Pulse] {status}: {a.rule_name} on {a.hostname}{scope}".split())

    value_label = "Value" if outcome != "not_evaluated" else "Last value seen"
    details = [
        ("Series", series_label(a)),
        (value_label, f"{_number(a.last_value)} {a.unit}"),
        ("Condition", _condition(a)),
        ("Breaching since", _time(a.pending_since)),
        ("Fired at", _time(a.fired_at)),
    ]
    if change.kind == "resolved":
        details += [("Resolved at", _time(a.resolved_at)), ("Firing for", _duration(a.resolved_at - a.fired_at))]
    rule = f"rule #{a.rule_id}" if a.rule_id is not None else "rule since deleted"
    return Notice(outcome, subject, summary, details, f"Alert #{a.id}, {rule}.")


def build_email(change: AlertChange, config: EmailConfig, now: float) -> EmailMessage:
    """The message for a `fired` or `resolved` change."""
    n = describe(change)
    width = max(len(label) for label, _ in n.details) + 1

    message = EmailMessage()
    message["Subject"] = n.subject
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message["Date"] = formatdate(now, usegmt=True)
    message["Message-ID"] = make_msgid("pulse-alert", domain=parseaddr(config.sender)[1].rpartition("@")[2])
    message.set_content(
        "\n".join(
            [
                textwrap.fill(n.summary, 72),
                "",
                *(f"  {label + ':':<{width}} {value}" for label, value in n.details),
                "",
                n.footer,
            ]
        )
        + "\n"
    )
    return message


# The dashboard's danger, online, and warning colors.
SLACK_COLORS: dict[AlertOutcome, str] = {"firing": "#dc2626", "recovered": "#16a34a", "not_evaluated": "#b45309"}


def build_slack_message(change: AlertChange) -> dict[str, Any]:
    """The incoming-webhook payload for a `fired` or `resolved` change: the
    subject as the message text, then the details in an attachment whose
    color bar says firing (red), recovered (green), or no longer evaluated
    (amber)."""
    n = describe(change)
    return {
        "text": _slack_escape(n.subject),
        "attachments": [
            {
                "color": SLACK_COLORS[n.outcome],
                "blocks": [
                    {"type": "section", "text": _mrkdwn(_slack_escape(n.summary), SLACK_SECTION_LIMIT)},
                    {
                        "type": "section",
                        "fields": [
                            _mrkdwn(f"*{label}*\n{_slack_escape(value)}", SLACK_FIELD_LIMIT) for label, value in n.details
                        ],
                    },
                    {"type": "context", "elements": [_mrkdwn(n.footer, SLACK_SECTION_LIMIT)]},
                ],
            }
        ],
    }


def _slack_escape(text: str) -> str:
    """Escape Slack's control characters, so a rule named "<!channel>" can't
    notify the whole channel and "<https://...|text>" can't become a link."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _mrkdwn(text: str, limit: int) -> dict[str, str]:
    if len(text) > limit:
        text = text[: limit - 1]
        # Don't leave half an escape like "&am" at the end.
        amp = text.rfind("&")
        if amp != -1 and ";" not in text[amp:]:
            text = text[:amp]
        text += "…"
    return {"type": "mrkdwn", "text": text}


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


class SlackChannel:
    name = "slack"

    def __init__(self, config: SlackConfig) -> None:
        self.config = config

    def send(self, change: AlertChange, now: float) -> None:
        request = urllib.request.Request(
            self.config.webhook_url,
            data=json.dumps(build_slack_message(change)).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # None of the errors raised here include the URL, so the Notifier
        # can log them without leaking it.
        try:
            with urllib.request.urlopen(request, timeout=SLACK_TIMEOUT_SECONDS):
                pass
        except urllib.error.HTTPError as exc:
            # Slack says why in the body, e.g. "invalid_payload", or
            # "no_service" once the webhook has been removed.
            reason = exc.read(200).decode("utf-8", "replace").strip() or exc.reason
            raise RuntimeError(f"Slack answered {exc.code}: {reason}") from None


_STOP = object()


class Notifier:
    """Sends notifications for fired and resolved alerts, each channel from
    its own background thread, so a slow or unreachable mail server or
    webhook never holds up rule evaluation or the other channels.

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
        self._lanes = [(channel, queue.Queue()) for channel in channels]
        self._retry_delays = tuple(retry_delays)
        self._max_queued = max_queued
        self._clock = clock
        self._stopping = threading.Event()
        self._threads: list[threading.Thread] = []

    def __call__(self, change: AlertChange) -> None:
        """Queue a stored alert change; anything but fired/resolved is ignored."""
        if change.kind not in NOTIFY_KINDS:
            return
        for channel, pending in self._lanes:
            if pending.qsize() >= self._max_queued:
                logger.error(
                    "%s notification queue full; dropped %s notification for alert %d",
                    channel.name, change.kind, change.after.id,
                )
            else:
                pending.put(change)

    def start(self) -> None:
        self._stopping.clear()
        self._threads = [
            threading.Thread(target=self._run, args=lane, name=f"pulse-notify-{lane[0].name}", daemon=True)
            for lane in self._lanes
        ]
        for thread in self._threads:
            thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Finish what's queued (retrying without waiting), for up to `timeout`."""
        self._stopping.set()
        for _, pending in self._lanes:
            pending.put(_STOP)
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in self._threads):
            logger.warning("Notifier didn't finish within %ss; queued notifications may be lost", timeout)
        self._threads = []

    def _run(self, channel: Channel, pending: queue.Queue) -> None:
        while (change := pending.get()) is not _STOP:
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
