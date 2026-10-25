import json
import logging
import smtplib
import socket
import threading
import time
from dataclasses import replace

import pytest
from aiosmtpd.smtp import AuthResult

from pulse_ingest.app import _notifier
from pulse_ingest.notify import (
    NOTIFY_ENV_VARS,
    EmailChannel,
    EmailConfig,
    Notifier,
    SlackChannel,
    SlackConfig,
    build_email,
    build_slack_message,
)
from pulse_ingest.store import Alert, AlertChange

from .conftest import SmtpSink, start_smtp_server

# 2026-10-19 08:53:20 UTC
FIRED_AT = 1_792_400_000.0


def firing_alert(**overrides):
    a = Alert(
        id=12,
        rule_id=3,
        host_id=1,
        hostname="web-1",
        unit="percent",
        tags={"core": "0"},
        rule_name="High CPU",
        metric_name="cpu.usage",
        operator=">",
        threshold=80.0,
        duration_seconds=300,
        state="firing",
        pending_since=FIRED_AT - 300,
        last_value=95.5,
        last_evaluated_at=FIRED_AT,
        fired_at=FIRED_AT,
    )
    return replace(a, **overrides)


def fired(**overrides):
    return AlertChange("fired", None, firing_alert(**overrides))


def resolved(reason="recovered", after_seconds=425, **overrides):
    before = firing_alert(**overrides)
    after = replace(
        before, state="resolved", resolved_at=FIRED_AT + after_seconds, resolve_reason=reason, last_value=41.0
    )
    return AlertChange("resolved", before, after)


def config(**overrides):
    c = EmailConfig("127.0.0.1", 25, "none", "Pulse <alerts@example.com>", ("ops@example.com", "me@example.com"))
    return replace(c, **overrides)


# --- configuration ---

MINIMAL_ENV = {
    "PULSE_SMTP_HOST": "smtp.example.com",
    "PULSE_ALERT_EMAIL_FROM": "alerts@example.com",
    "PULSE_ALERT_EMAIL_TO": "ops@example.com",
}


@pytest.mark.parametrize("env", [{}, {"PULSE_SMTP_HOST": "  ", "PULSE_ALERT_EMAIL_TO": ""}])
def test_email_is_off_when_nothing_is_configured(env):
    assert EmailConfig.from_env(env) is None


def test_minimal_config_uses_starttls_on_587_without_login():
    env = {**MINIMAL_ENV, "PULSE_ALERT_EMAIL_TO": " ops@example.com, ,Me <me@example.com> "}

    c = EmailConfig.from_env(env)

    assert (c.host, c.port, c.security, c.username, c.password) == ("smtp.example.com", 587, "starttls", None, None)
    assert c.sender == "alerts@example.com"
    assert c.recipients == ("ops@example.com", "Me <me@example.com>")


@pytest.mark.parametrize(
    "security, port, expected",
    [("SSL", None, 465), ("none", None, 25), ("starttls", "2525", 2525)],
)
def test_port_defaults_to_the_security_modes_usual_port(security, port, expected):
    env = {**MINIMAL_ENV, "PULSE_SMTP_SECURITY": security, **({"PULSE_SMTP_PORT": port} if port else {})}

    assert EmailConfig.from_env(env).port == expected


def test_login_needs_both_username_and_password_and_keeps_the_password_as_typed():
    env = {**MINIMAL_ENV, "PULSE_SMTP_USERNAME": "alerts", "PULSE_SMTP_PASSWORD": " abcd efgh "}

    c = EmailConfig.from_env(env)

    assert (c.username, c.password) == ("alerts", " abcd efgh ")
    assert "abcd" not in repr(c)


def test_partial_config_names_what_is_missing():
    with pytest.raises(ValueError, match="also set PULSE_SMTP_HOST, PULSE_ALERT_EMAIL_FROM"):
        EmailConfig.from_env({"PULSE_ALERT_EMAIL_TO": "ops@example.com"})


@pytest.mark.parametrize(
    "extra, message",
    [
        ({"PULSE_SMTP_SECURITY": "tls"}, "must be starttls, ssl, or none"),
        ({"PULSE_SMTP_PORT": "smtp"}, "must be a port number"),
        ({"PULSE_SMTP_PORT": "0"}, "must be a port number"),
        ({"PULSE_SMTP_PORT": "65536"}, "must be a port number"),
        ({"PULSE_SMTP_USERNAME": "alerts"}, "both PULSE_SMTP_USERNAME and PULSE_SMTP_PASSWORD"),
        ({"PULSE_SMTP_PASSWORD": "secret"}, "both PULSE_SMTP_USERNAME and PULSE_SMTP_PASSWORD"),
        ({"PULSE_ALERT_EMAIL_FROM": "alerts"}, "Not an email address: 'alerts'"),
        ({"PULSE_ALERT_EMAIL_TO": "ops@example.com, oncall"}, "Not an email address: 'oncall'"),
    ],
)
def test_bad_settings_are_rejected(extra, message):
    with pytest.raises(ValueError, match=message):
        EmailConfig.from_env({**MINIMAL_ENV, **extra})


def test_warns_when_a_password_would_go_unencrypted(caplog):
    env = {**MINIMAL_ENV, "PULSE_SMTP_SECURITY": "none", "PULSE_SMTP_USERNAME": "u", "PULSE_SMTP_PASSWORD": "p"}

    with caplog.at_level(logging.WARNING, logger="pulse_ingest.notify"):
        EmailConfig.from_env(env)

    assert "sends the SMTP password unencrypted" in caplog.text


# --- the messages ---


def body(message):
    return message.get_content()


def test_fired_email():
    m = build_email(fired(), config(), FIRED_AT)

    assert m["Subject"] == "[Pulse] FIRING: High CPU on web-1 (core=0)"
    assert (m["From"], m["To"]) == ("Pulse <alerts@example.com>", "ops@example.com, me@example.com")
    assert m["Date"] == "Mon, 19 Oct 2026 08:53:20 +0000"
    assert m["Message-ID"].endswith("@example.com>")
    assert body(m) == (
        "High CPU is FIRING on web-1.\n"
        "\n"
        "  Series:          cpu.usage{core=0}\n"
        "  Value:           95.5 percent\n"
        "  Condition:       cpu.usage > 80 for 5m\n"
        "  Breaching since: 2026-10-19 08:48:20 UTC\n"
        "  Fired at:        2026-10-19 08:53:20 UTC\n"
        "\n"
        "Alert #12, rule #3.\n"
    )


def test_recovered_email_says_it_recovered_and_how_long_it_fired():
    m = build_email(resolved("recovered", after_seconds=3725), config(), FIRED_AT + 3725)

    assert m["Subject"] == "[Pulse] RESOLVED: High CPU on web-1 (core=0)"
    text = body(m)
    assert text.startswith("High CPU on web-1 has RESOLVED: the value is back within the threshold.\n")
    assert "  Value:           41 percent\n" in text
    assert "  Resolved at:     2026-10-19 09:55:25 UTC\n" in text
    assert "  Firing for:      1h 2m 5s\n" in text


def test_not_evaluated_email_says_it_did_not_recover():
    m = build_email(resolved("not_evaluated"), config(), FIRED_AT + 425)

    assert m["Subject"] == "[Pulse] RESOLVED (no longer evaluated): High CPU on web-1 (core=0)"
    text = body(m)
    assert "NOT because the value recovered" in " ".join(text.split())
    assert "the host stopped reporting this metric" in " ".join(text.split())
    assert "  Last value seen: 41 percent\n" in text
    assert "  Firing for:      7m 5s\n" in text


def test_email_for_an_alert_whose_rule_was_deleted():
    m = build_email(resolved("not_evaluated", rule_id=None), config(), FIRED_AT + 425)

    assert body(m).endswith("Alert #12, rule since deleted.\n")


def test_untagged_zero_duration_alert_with_large_values():
    change = fired(tags={}, unit="bytes", metric_name="memory.used", threshold=8e9, duration_seconds=0, last_value=1.6e10)

    m = build_email(change, config(), FIRED_AT)

    assert m["Subject"] == "[Pulse] FIRING: High CPU on web-1"
    assert "  Series:          memory.used\n" in body(m)
    assert "  Value:           16000000000 bytes\n" in body(m)
    assert "  Condition:       memory.used > 8000000000\n" in body(m)


def test_line_breaks_in_names_and_tags_cannot_reach_the_headers():
    change = fired(rule_name="High\r\nBcc: victim@example.com", tags={"mount": "/\nx"})

    m = build_email(change, config(), FIRED_AT)

    assert m["Subject"] == "[Pulse] FIRING: High Bcc: victim@example.com on web-1 (mount=/ x)"
    assert m["Bcc"] is None


def test_long_text_is_wrapped_so_the_email_stays_plain_7bit():
    m = build_email(resolved("not_evaluated"), config(), FIRED_AT)

    assert m["Content-Transfer-Encoding"] == "7bit"
    assert max(len(line) for line in body(m).splitlines()) <= 72


# --- sending ---


def test_sends_to_a_real_smtp_server(smtp_sink):
    EmailChannel(config(port=smtp_sink.port)).send(fired(), FIRED_AT)

    (envelope,) = smtp_sink.envelopes
    assert envelope.mail_from == "alerts@example.com"
    assert envelope.rcpt_tos == ["ops@example.com", "me@example.com"]
    (received,) = smtp_sink.messages
    assert received["Subject"] == "[Pulse] FIRING: High CPU on web-1 (core=0)"
    assert "  Series:          cpu.usage{core=0}\r\n" in received.get_content()  # SMTP line endings


def test_non_ascii_names_survive_the_trip(smtp_sink):
    change = fired(rule_name="Température élevée 🔥", hostname="serveur-é")

    EmailChannel(config(port=smtp_sink.port)).send(change, FIRED_AT)

    (received,) = smtp_sink.messages
    assert received["Subject"] == "[Pulse] FIRING: Température élevée 🔥 on serveur-é (core=0)"
    assert received.get_content().startswith("Température élevée 🔥 is FIRING on serveur-é.")


@pytest.mark.filterwarnings("ignore:Requiring AUTH while not requiring TLS")
def test_logs_in_when_given_credentials():
    logins = []

    def authenticator(server, session, envelope, mechanism, auth_data):
        logins.append((auth_data.login, auth_data.password))
        # handled=False: the server sends the standard 535 on failure.
        return AuthResult(success=auth_data.password == b"right", handled=False)

    sink = SmtpSink()
    controller = start_smtp_server(sink, authenticator=authenticator, auth_require_tls=False, auth_required=True)
    try:
        channel = EmailChannel(config(port=controller.port, username="alerts", password="right"))
        channel.send(fired(), FIRED_AT)
        with pytest.raises(smtplib.SMTPAuthenticationError):
            EmailChannel(replace(channel.config, password="wrong")).send(fired(), FIRED_AT)
    finally:
        controller.stop()

    # smtplib tries the wrong password once per mechanism the server offers.
    assert logins[0] == (b"alerts", b"right")
    assert set(logins[1:]) == {(b"alerts", b"wrong")}
    assert len(sink.envelopes) == 1


def test_raises_when_the_server_is_unreachable():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    # Nothing listens there now, so the connection is refused; the Notifier
    # counts that as a failed attempt.
    with pytest.raises(OSError):
        EmailChannel(config(port=port)).send(fired(), FIRED_AT)


class FakeSMTP:
    """Records the SMTP conversation, for the TLS modes a test server can't
    easily offer."""

    def __init__(self, host, port, timeout=None, context=None):
        self.calls = [(type(self).__name__, host, port, timeout, context is not None)]
        sessions.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append("quit")

    def starttls(self, context=None):
        self.calls.append(("starttls", context is not None))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, message):
        self.calls.append(("send", message["Subject"]))


class FakeSMTP_SSL(FakeSMTP):
    pass


sessions = []


@pytest.mark.parametrize(
    "security, opening, tls_upgrade",
    [
        ("starttls", ("FakeSMTP", "smtp.example.com", 587, 10.0, False), [("starttls", True)]),
        ("ssl", ("FakeSMTP_SSL", "smtp.example.com", 587, 10.0, True), []),
        ("none", ("FakeSMTP", "smtp.example.com", 587, 10.0, False), []),
    ],
)
def test_each_security_mode_encrypts_before_logging_in(monkeypatch, security, opening, tls_upgrade):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP_SSL)
    sessions.clear()
    c = config(host="smtp.example.com", port=587, security=security, username="u", password="p")

    EmailChannel(c).send(fired(), FIRED_AT)

    (session,) = sessions
    assert session.calls == [
        opening,
        *tls_upgrade,
        ("login", "u", "p"),
        ("send", "[Pulse] FIRING: High CPU on web-1 (core=0)"),
        "quit",
    ]


# --- delivery in the background ---


class FakeChannel:
    def __init__(self, name="fake", failures=0):
        self.name = name
        self.failures = failures
        self.attempts = 0
        self.sent = []

    def send(self, change, now):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise OSError(f"attempt {self.attempts} refused")
        self.sent.append((change.kind, change.after.id, now))


def run(notifier, *changes):
    notifier.start()
    for change in changes:
        notifier(change)
    notifier.stop()


def test_delivers_fired_and_resolved_to_every_channel_in_order():
    email, other = FakeChannel("email"), FakeChannel("other")
    pending = AlertChange("pending", None, replace(firing_alert(id=7), state="pending", fired_at=None))
    cancelled = AlertChange("cancelled", pending.after, None)
    updated = AlertChange("updated", fired().after, fired().after)

    run(Notifier([email, other], clock=lambda: 99.0), pending, fired(), updated, cancelled, resolved())

    assert email.sent == other.sent == [("fired", 12, 99.0), ("resolved", 12, 99.0)]


def test_a_failed_send_is_retried_then_given_up(caplog):
    broken = FakeChannel("broken", failures=99)

    with caplog.at_level(logging.INFO, logger="pulse_ingest.notify"):
        run(Notifier([broken], retry_delays=(0.01, 0.01)), fired())

    assert broken.attempts == 3
    messages = [r.getMessage() for r in caplog.records]
    assert messages == [
        "broken notification for alert 12 (fired) failed, retrying in 0.01s: attempt 1 refused",
        "broken notification for alert 12 (fired) failed, retrying in 0.01s: attempt 2 refused",
        "Giving up on broken notification for alert 12 (fired) after 3 attempts: attempt 3 refused",
    ]
    assert caplog.records[2].levelname == "ERROR"


def eventually(condition, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def test_a_stuck_channel_does_not_hold_up_the_others():
    gate = threading.Event()

    class StuckChannel(FakeChannel):
        def send(self, change, now):
            gate.wait(5)
            super().send(change, now)

    stuck, working = StuckChannel("stuck"), FakeChannel("working")
    notifier = Notifier([stuck, working])
    notifier.start()
    notifier(fired())
    notifier(resolved())

    delivered = eventually(lambda: len(working.sent) == 2)
    gate.set()
    notifier.stop()

    assert delivered, "the working channel waited for the stuck one"
    assert [kind for kind, _, _ in stuck.sent] == ["fired", "resolved"]


def test_a_send_that_succeeds_on_retry_is_delivered_once():
    flaky = FakeChannel(failures=1)

    run(Notifier([flaky], retry_delays=(0.01,)), fired())

    assert (flaky.attempts, len(flaky.sent)) == (2, 1)


def test_sending_happens_off_the_callers_thread():
    gate = threading.Event()

    class SlowChannel(FakeChannel):
        def send(self, change, now):
            gate.wait(5)
            super().send(change, now)

    slow = SlowChannel()
    notifier = Notifier([slow])
    notifier.start()
    started = time.monotonic()
    notifier(fired())
    queued_in = time.monotonic() - started
    gate.set()
    notifier.stop()

    assert queued_in < 0.5
    assert len(slow.sent) == 1


def test_stop_delivers_whats_queued_without_waiting_out_retry_delays():
    flaky = FakeChannel(failures=1)
    notifier = Notifier([flaky], retry_delays=(60.0,))
    notifier.start()
    notifier(fired())
    notifier(resolved())

    started = time.monotonic()
    notifier.stop(timeout=5)

    assert time.monotonic() - started < 2
    assert [kind for kind, _, _ in flaky.sent] == ["fired", "resolved"]


def test_drops_notifications_beyond_the_queue_limit(caplog):
    notifier = Notifier([FakeChannel()], max_queued=1)

    notifier(fired())
    notifier(resolved())

    assert "fake notification queue full; dropped resolved notification for alert 12" in caplog.text


# --- Slack: configuration ---

WEBHOOK = "https://hooks.slack.com/services/T000/B000/secret-token"


@pytest.mark.parametrize("env", [{}, {"PULSE_SLACK_WEBHOOK_URL": "  "}])
def test_slack_is_off_when_not_configured(env):
    assert SlackConfig.from_env(env) is None


def test_slack_config_keeps_the_webhook_secret_out_of_its_repr():
    c = SlackConfig.from_env({"PULSE_SLACK_WEBHOOK_URL": f" {WEBHOOK} "})

    assert (c.webhook_url, c.host) == (WEBHOOK, "hooks.slack.com")
    assert "secret-token" not in repr(c)


@pytest.mark.parametrize(
    "url",
    [
        "hooks.slack.com/services/T000/B000/secret-token",
        "ftp://hooks.slack.com/services/T000/B000/secret-token",
        "https:///services/T000/B000/secret-token",
        "secret-token",
    ],
)
def test_slack_webhook_must_be_a_url_and_the_error_does_not_repeat_it(url):
    with pytest.raises(ValueError, match="must be a webhook URL starting with https://") as error:
        SlackConfig.from_env({"PULSE_SLACK_WEBHOOK_URL": url})

    assert "secret-token" not in str(error.value)


def test_warns_when_the_webhook_url_would_go_unencrypted(caplog):
    with caplog.at_level(logging.WARNING, logger="pulse_ingest.notify"):
        SlackConfig.from_env({"PULSE_SLACK_WEBHOOK_URL": "http://chat.internal/hooks/secret-token"})

    assert "is http://, so the webhook URL (its secret) is sent unencrypted" in caplog.text
    assert "secret-token" not in caplog.text


def test_the_api_notifies_through_whichever_channels_are_configured(monkeypatch, caplog):
    for name in NOTIFY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    assert _notifier() is None

    monkeypatch.setenv("PULSE_SLACK_WEBHOOK_URL", WEBHOOK)
    with caplog.at_level(logging.INFO, logger="pulse_ingest.app"):
        assert isinstance(_notifier(), Notifier)

    assert "Email notifications off" in caplog.text
    assert "Slack notifications on: alerts post to the webhook at hooks.slack.com" in caplog.text
    assert "secret-token" not in caplog.text


# --- Slack: the messages ---


def fields(message):
    (attachment,) = message["attachments"]
    return [f["text"] for f in attachment["blocks"][1]["fields"]]


def test_fired_slack_message():
    assert build_slack_message(fired()) == {
        "text": "[Pulse] FIRING: High CPU on web-1 (core=0)",
        "attachments": [
            {
                "color": "#dc2626",
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn", "text": "High CPU is FIRING on web-1."}},
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": "*Series*\ncpu.usage{core=0}"},
                            {"type": "mrkdwn", "text": "*Value*\n95.5 percent"},
                            {"type": "mrkdwn", "text": "*Condition*\ncpu.usage &gt; 80 for 5m"},
                            {"type": "mrkdwn", "text": "*Breaching since*\n2026-10-19 08:48:20 UTC"},
                            {"type": "mrkdwn", "text": "*Fired at*\n2026-10-19 08:53:20 UTC"},
                        ],
                    },
                    {"type": "context", "elements": [{"type": "mrkdwn", "text": "Alert #12, rule #3."}]},
                ],
            }
        ],
    }


def test_recovered_slack_message_is_green_and_says_how_long_it_fired():
    m = build_slack_message(resolved("recovered", after_seconds=3725))

    assert m["text"] == "[Pulse] RESOLVED: High CPU on web-1 (core=0)"
    assert m["attachments"][0]["color"] == "#16a34a"
    assert fields(m)[1] == "*Value*\n41 percent"
    assert fields(m)[-2:] == ["*Resolved at*\n2026-10-19 09:55:25 UTC", "*Firing for*\n1h 2m 5s"]


def test_not_evaluated_slack_message_is_amber_and_says_it_did_not_recover():
    m = build_slack_message(resolved("not_evaluated", rule_id=None))

    assert m["text"] == "[Pulse] RESOLVED (no longer evaluated): High CPU on web-1 (core=0)"
    (attachment,) = m["attachments"]
    assert attachment["color"] == "#b45309"
    assert "NOT because the value recovered" in attachment["blocks"][0]["text"]["text"]
    assert fields(m)[1] == "*Last value seen*\n41 percent"
    assert attachment["blocks"][2]["elements"][0]["text"] == "Alert #12, rule since deleted."


def test_names_cannot_mention_the_channel_or_add_links():
    change = fired(rule_name="<!channel> & <https://evil.example|Click>", hostname="<@U123>")

    m = build_slack_message(change)

    assert m["text"] == (
        "[Pulse] FIRING: &lt;!channel&gt; &amp; &lt;https://evil.example|Click&gt; on &lt;@U123&gt; (core=0)"
    )
    assert "<" not in json.dumps(m)


def test_long_values_are_cut_to_fit_slacks_limits():
    # 20 tags of the longest allowed size, full of a character Slack needs escaped.
    tags = {f"{i:02d}" + "k" * 198: "&" * 200 for i in range(20)}

    m = build_slack_message(fired(tags=tags, rule_name="&" * 200, hostname="h" * 255))

    summary = m["attachments"][0]["blocks"][0]["text"]["text"]
    assert len(summary) <= 3000
    assert all(len(text) <= 2000 for text in fields(m))
    assert fields(m)[0].endswith("&amp;…")  # cut between escapes, never inside one


# --- Slack: sending ---


def test_posts_the_message_as_json_to_the_webhook(webhook_sink):
    SlackChannel(SlackConfig(webhook_sink.url)).send(fired(), FIRED_AT)

    ((path, content_type, payload),) = webhook_sink.requests
    assert path == "/services/T000/B000/secret-token"
    assert content_type == "application/json"
    assert payload == build_slack_message(fired())


def test_non_ascii_names_survive_the_post(webhook_sink):
    change = fired(rule_name="Température élevée 🔥", hostname="serveur-é")

    SlackChannel(SlackConfig(webhook_sink.url)).send(change, FIRED_AT)

    assert webhook_sink.payloads[0]["text"] == "[Pulse] FIRING: Température élevée 🔥 on serveur-é (core=0)"


def test_a_rejected_post_raises_with_slacks_reason_but_not_the_url(webhook_sink):
    webhook_sink.reply = (404, "no_service")

    with pytest.raises(RuntimeError, match="^Slack answered 404: no_service$") as error:
        SlackChannel(SlackConfig(webhook_sink.url)).send(fired(), FIRED_AT)

    assert "secret-token" not in str(error.value)


def test_an_unreachable_webhook_raises_without_revealing_the_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    with pytest.raises(OSError) as error:
        SlackChannel(SlackConfig(f"http://127.0.0.1:{port}/services/T000/B000/secret-token")).send(fired(), FIRED_AT)

    assert "secret-token" not in str(error.value)


def test_failed_slack_posts_are_retried_and_logged_without_the_url(webhook_sink, caplog):
    webhook_sink.reply = (500, "server_error")

    run(Notifier([SlackChannel(SlackConfig(webhook_sink.url))], retry_delays=(0.01,)), fired())

    assert len(webhook_sink.requests) == 2
    assert (
        "Giving up on slack notification for alert 12 (fired) after 2 attempts: Slack answered 500: server_error"
        in caplog.text
    )
    assert "secret-token" not in caplog.text
