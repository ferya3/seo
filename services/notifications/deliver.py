"""What to send, to whom, and whether a failure is worth retrying.

Everything here is a pure function. The transports live in channels.py, which
is the part that needs a network; this is the part that decides — and in a
notification service the decisions are where the damage happens:

  * A notification sent twice is worse than one sent late. Delivery is
    at-least-once, so `dedupe_key` is what makes "the same event, the same
    channel" a thing that can only happen once, enforced by a unique index
    rather than by remembering.
  * A payload with the report inside it is a report leaked into a webhook log.
    Links only, and the link needs a token to be useful.
  * A 500 deserves a retry; a 404 does not. Retrying a permanent failure
    forever is how a queue fills with work that can never succeed.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

# Which events a channel may ask for. Kept as a list rather than "anything the
# bus carries", because a subscription to an event nobody publishes is a
# silence nobody can explain.
SUBSCRIBABLE = ("report.rendered", "workflow.completed")

# Retried: the far side is having a bad minute. Not retried: it has made up
# its mind. 408 and 429 are explicit "come back later" answers.
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


def wants(channel: dict[str, Any], event_type: str) -> bool:
    """Whether this channel asked for this event.

    An empty `events` means every subscribable event, which is what someone
    setting up a webhook for the first time actually wants.
    """
    if not channel.get("active", True):
        return False
    events = channel.get("events") or []
    return not events or event_type in events


def dedupe_key(channel_id: str, event_id: str) -> str:
    """One delivery per channel per event, forever.

    The event id is the one the producer staged in the outbox, so it survives
    a redelivery and a relay restart — which is exactly when a notification
    service sends the same email twice.
    """
    return f"{channel_id}:{event_id}"


def payload(event_type: str, event: dict[str, Any], base_url: str = "") -> dict[str, Any]:
    """What goes on the wire.

    A summary and links, never the document. A webhook body ends up in
    somebody else's log, and a report is not something to leave there.
    """
    body: dict[str, Any] = {
        "event": event_type,
        "workflow_id": event.get("workflow_id"),
    }

    if event_type == "report.rendered":
        body.update({
            "report_id": event.get("report_id"),
            "title": event.get("title"),
            "status": event.get("status"),
            "summary_source": event.get("summary_source"),
            "formats": event.get("formats") or [],
            "document_url": _absolute(base_url, event.get("document_url")),
        })
    else:
        headline = event.get("headline") or {}
        body.update({
            "status": event.get("status"),
            "goal": event.get("goal"),
            "overall_score": headline.get("overall_score"),
            "total_issues": headline.get("total_issues"),
            "result_url": _absolute(base_url, event.get("result_url")),
        })
    return body


def _absolute(base_url: str, path: str | None) -> str | None:
    if not path:
        return None
    if not base_url or path.startswith(("http://", "https://")):
        return path
    return f"{base_url.rstrip('/')}{path}"


def signature(secret: str, body: bytes) -> str:
    """HMAC-SHA256 of the exact bytes sent.

    Signed over the serialised body rather than a re-serialisation of the
    dict: two JSON encoders disagree about spacing, and a signature the
    receiver cannot reproduce is worse than no signature at all.
    """
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def retryable(status: int | None, error: str | None = None) -> bool:
    """Whether the same delivery is worth attempting again."""
    if status is None:
        # No response at all: connection refused, DNS, timeout. The far side
        # may simply be restarting.
        return True
    return status in RETRYABLE_STATUS


def subject(event_type: str, body: dict[str, Any]) -> str:
    """The one line an email is judged by, in the reader's inbox."""
    if event_type == "report.rendered":
        return body.get("title") or "گزارش سئوی شما آماده است"
    score = body.get("overall_score")
    if score is not None:
        return f"تحلیل سئو تمام شد — امتیاز {score}"
    return "تحلیل سئو تمام شد"


def text(event_type: str, body: dict[str, Any]) -> str:
    """The email body. Plain text on purpose: it renders everywhere, it cannot
    carry a tracking pixel, and the report is one click away."""
    lines = [subject(event_type, body), ""]

    if body.get("overall_score") is not None:
        lines.append(f"امتیاز کلی: {body['overall_score']} از ۱۰۰")
    if body.get("total_issues") is not None:
        lines.append(f"تعداد ایرادها: {body['total_issues']}")
    if body.get("summary_source"):
        lines.append(
            "خلاصه: " + ("نوشته‌ی مدل" if body["summary_source"] == "ai" else "قانون‌محور")
        )

    link = body.get("document_url") or body.get("result_url")
    if link:
        lines += ["", f"گزارش کامل: {link}"]
    lines += ["", "— ایجنت سئو"]
    return "\n".join(lines)
