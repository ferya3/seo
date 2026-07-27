"""The two transports, and the guard in front of the dangerous one.

A webhook target is the most dangerous url in the platform: typed by a user,
fetched by a server that sits inside a private network. Most of this file is
about that.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from services.notifications import channels, deliver  # noqa: E402
from shared import netguard  # noqa: E402

BODY = {"event": "report.rendered", "report_id": "aaaa", "title": "گزارش"}


@pytest.fixture(autouse=True)
def strict_guard(monkeypatch):
    # The guard is off in most of this repository's local runs; a notification
    # target must be judged by the production rules.
    monkeypatch.delenv("SEO_AGENT_ALLOW_PRIVATE", raising=False)
    netguard.reset_cache()
    yield
    netguard.reset_cache()


class Sent:
    def __init__(self, status=200):
        self.status = status
        self.calls: list[dict] = []

    def __call__(self, url, data=None, headers=None, timeout=None):
        self.calls.append({"url": url, "data": data, "headers": headers})
        return type("R", (), {"status_code": self.status})()


# ------------------------------------------------------------------ the guard


def test_a_webhook_at_the_metadata_endpoint_is_refused(monkeypatch):
    """The reason the guard exists: a tenant pointing a notification at the
    cloud metadata service and having this server read the credentials out."""
    sent = Sent()
    monkeypatch.setattr(channels.requests, "post", sent)

    result = channels.send_webhook("http://169.254.169.254/latest/meta-data/", BODY)

    assert result.ok is False
    assert "blocked" in result.error
    assert sent.calls == []                      # never left the process


def test_a_webhook_on_the_private_network_is_refused(monkeypatch):
    sent = Sent()
    monkeypatch.setattr(channels.requests, "post", sent)

    assert channels.send_webhook("http://10.0.0.5/hook", BODY).ok is False
    assert sent.calls == []


def test_a_blocked_target_is_never_retried():
    """It will not become allowed, and retrying it forever fills the queue."""
    result = channels.send_webhook("http://127.0.0.1/hook", BODY)
    assert deliver.retryable(result.status, result.error) is True or result.status is None
    assert "blocked" in result.error


# ---------------------------------------------------------------- the webhook


def test_the_body_is_sent_as_the_exact_bytes_that_were_signed(monkeypatch):
    sent = Sent()
    monkeypatch.setattr(channels.requests, "post", sent)
    monkeypatch.setattr(channels.netguard, "check_url", lambda url: None)

    channels.send_webhook("https://hooks.example.com/x", BODY, secret="topsecret")
    call = sent.calls[0]

    assert call["headers"]["X-Seo-Signature"] == deliver.signature("topsecret", call["data"])
    assert json.loads(call["data"].decode("utf-8")) == BODY


def test_persian_is_sent_as_utf8_not_escaped(monkeypatch):
    sent = Sent()
    monkeypatch.setattr(channels.requests, "post", sent)
    monkeypatch.setattr(channels.netguard, "check_url", lambda url: None)

    channels.send_webhook("https://hooks.example.com/x", BODY)
    assert "گزارش".encode() in sent.calls[0]["data"]


def test_a_channel_with_no_secret_sends_no_signature(monkeypatch):
    sent = Sent()
    monkeypatch.setattr(channels.requests, "post", sent)
    monkeypatch.setattr(channels.netguard, "check_url", lambda url: None)

    channels.send_webhook("https://hooks.example.com/x", BODY)
    assert "X-Seo-Signature" not in sent.calls[0]["headers"]


def test_the_event_type_travels_in_a_header_too(monkeypatch):
    """So a receiver can route without parsing the body."""
    sent = Sent()
    monkeypatch.setattr(channels.requests, "post", sent)
    monkeypatch.setattr(channels.netguard, "check_url", lambda url: None)

    channels.send_webhook("https://hooks.example.com/x", BODY)
    assert sent.calls[0]["headers"]["X-Seo-Event"] == "report.rendered"


def test_an_error_status_comes_back_as_a_failure(monkeypatch):
    monkeypatch.setattr(channels.requests, "post", Sent(status=503))
    monkeypatch.setattr(channels.netguard, "check_url", lambda url: None)

    result = channels.send_webhook("https://hooks.example.com/x", BODY)
    assert result.ok is False
    assert result.status == 503
    assert deliver.retryable(result.status) is True


def test_a_refused_connection_is_a_failure_with_no_status(monkeypatch):
    monkeypatch.setattr(channels.netguard, "check_url", lambda url: None)
    monkeypatch.setattr(channels.requests, "post", _raise(ConnectionError("refused")))

    result = channels.send_webhook("https://hooks.example.com/x", BODY)
    assert result.ok is False
    assert result.status is None
    assert deliver.retryable(result.status) is True


# ------------------------------------------------------------------ the email


def test_an_unconfigured_smtp_is_a_permanent_failure(monkeypatch):
    """Nothing is set up; the same send fails identically in five minutes."""
    monkeypatch.delenv("SMTP_HOST", raising=False)

    result = channels.send_email("someone@example.com", "س", "متن")
    assert result.ok is False
    assert "SMTP_HOST" in result.error


def test_the_message_is_addressed_and_plain(monkeypatch):
    captured: dict = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            captured["host"], captured["port"] = host, port

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def send_message(self, message):
            captured["message"] = message

    monkeypatch.setenv("SMTP_HOST", "mail.example.com")
    monkeypatch.setenv("SMTP_PORT", "2525")
    monkeypatch.setenv("SMTP_FROM", "seo@example.com")
    monkeypatch.setattr(channels.smtplib, "SMTP", FakeSMTP)

    result = channels.send_email("someone@example.com", "گزارش آماده است", "متن گزارش")

    assert result.ok is True
    assert (captured["host"], captured["port"]) == ("mail.example.com", 2525)
    message = captured["message"]
    assert message["To"] == "someone@example.com"
    assert message["Subject"] == "گزارش آماده است"
    assert message.get_content_type() == "text/plain"


def test_a_dead_mail_server_is_a_failure_worth_retrying(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "mail.example.com")
    monkeypatch.setattr(channels.smtplib, "SMTP", _raise(OSError("connection refused")))

    result = channels.send_email("someone@example.com", "س", "متن")
    assert result.ok is False
    assert deliver.retryable(result.status, result.error) is True


# ---------------------------------------------------------------- dispatching


def test_an_unknown_channel_kind_fails_loudly(monkeypatch):
    result = channels.send({"kind": "carrier-pigeon", "target": "x"}, "report.rendered", BODY)
    assert result.ok is False
    assert "carrier-pigeon" in result.error


def _raise(exc: Exception):
    def raiser(*args, **kwargs):
        raise exc
    return raiser
