"""The two ways a notification leaves this system.

Isolated for the same reason the SERP provider is: this is the part that needs
a network, and everything that decides *what* to send stays testable without
one. Both transports return the same small result so the worker does not care
which it used.

The webhook target is the most dangerous URL in the whole platform — it is
typed by a user and fetched by a server that sits inside a private network.
Every one goes through the SSRF guard, which means a tenant cannot point a
notification at `169.254.169.254` and have this service read the instance
credentials out to them.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

import requests

from shared import netguard

log = logging.getLogger(__name__)

TIMEOUT = float(os.environ.get("NOTIFY_TIMEOUT", "15"))
USER_AGENT = "SeoAgent-Notifications/1.0"


@dataclass
class Result:
    ok: bool
    status: int | None = None
    error: str | None = None


def send_webhook(target: str, body: dict[str, Any], secret: str | None = None) -> Result:
    """POST the payload, signed if the channel has a secret."""
    from . import deliver

    try:
        netguard.check_url(target)
    except Exception as exc:
        # Not a transport failure and never retryable: the target is one this
        # server is not allowed to reach, and it will not become allowed.
        return Result(ok=False, status=None, error=f"blocked target: {exc}")

    # Serialised once. The signature covers these exact bytes, so a receiver
    # can reproduce it; signing a re-encoding of the dict cannot be verified.
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": USER_AGENT,
        "X-Seo-Event": str(body.get("event", "")),
    }
    if secret:
        headers["X-Seo-Signature"] = deliver.signature(secret, raw)

    try:
        response = requests.post(target, data=raw, headers=headers, timeout=TIMEOUT)
    except Exception as exc:
        return Result(ok=False, status=None, error=f"{type(exc).__name__}: {exc}")

    return Result(ok=response.status_code < 400, status=response.status_code,
                  error=None if response.status_code < 400 else f"HTTP {response.status_code}")


def send_email(target: str, subject: str, body: str) -> Result:
    """Hand the message to an SMTP server.

    No HTML part. A plain-text mail renders in every client, cannot carry a
    tracking pixel, and puts the link where the reader can see it — which is
    all a notification needs to do.
    """
    host = os.environ.get("SMTP_HOST")
    if not host:
        # Not a failure to retry: nothing is configured, and the same send
        # will fail identically in five minutes.
        return Result(ok=False, status=None, error="SMTP_HOST is not configured")

    message = EmailMessage()
    message["From"] = os.environ.get("SMTP_FROM", "seo-agent@localhost")
    message["To"] = target
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "25")), timeout=TIMEOUT) as smtp:
            if os.environ.get("SMTP_STARTTLS", "0") == "1":
                smtp.starttls()
            user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
            if user and password:
                smtp.login(user, password)
            smtp.send_message(message)
    except Exception as exc:
        return Result(ok=False, status=None, error=f"{type(exc).__name__}: {exc}")

    return Result(ok=True, status=None)


def send(channel: dict[str, Any], event_type: str, body: dict[str, Any]) -> Result:
    """Dispatch to whichever transport the channel is."""
    from . import deliver

    kind = channel.get("kind")
    if kind == "webhook":
        return send_webhook(channel["target"], body, channel.get("secret"))
    if kind == "email":
        return send_email(
            channel["target"], deliver.subject(event_type, body), deliver.text(event_type, body)
        )
    return Result(ok=False, status=None, error=f"unknown channel kind {kind!r}")
