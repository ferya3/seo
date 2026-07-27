"""What gets sent, and when a failure is worth another attempt.

Pure functions, so every decision a notification service makes before it
touches the network is pinned here. The two that matter most: a payload never
carries the report, and a signature covers the exact bytes that go on the wire.

    pytest services/notifications
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from services.notifications import deliver  # noqa: E402

RENDERED = {
    "report_id": "aaaa",
    "workflow_id": "wwww",
    "title": "گزارش سئو — https://site.test/",
    "status": "completed",
    "summary_source": "rules",
    "formats": ["html", "md"],
    "result_url": "/v1/reports/aaaa",
    "document_url": "/v1/reports/aaaa/document",
}

COMPLETED = {
    "workflow_id": "wwww",
    "goal": "site_audit",
    "status": "completed",
    "headline": {"overall_score": 87, "total_issues": 18},
    "result_url": "/v1/workflows/wwww",
}


# ---------------------------------------------------------------- who wants it


def test_an_empty_subscription_means_every_event():
    """What someone setting up their first webhook actually wants."""
    channel = {"events": [], "active": True}
    assert deliver.wants(channel, "report.rendered") is True
    assert deliver.wants(channel, "workflow.completed") is True


def test_a_named_subscription_takes_only_that_event():
    channel = {"events": ["report.rendered"], "active": True}
    assert deliver.wants(channel, "report.rendered") is True
    assert deliver.wants(channel, "workflow.completed") is False


def test_an_inactive_channel_wants_nothing():
    assert deliver.wants({"events": [], "active": False}, "report.rendered") is False


# ------------------------------------------------------------------- send once


def test_the_key_is_the_channel_and_the_event_not_the_moment():
    """The event id survives a redelivery and a relay restart, which is
    exactly when the same email gets sent twice."""
    assert deliver.dedupe_key("c-1", "e-1") == deliver.dedupe_key("c-1", "e-1")
    assert deliver.dedupe_key("c-1", "e-1") != deliver.dedupe_key("c-2", "e-1")
    assert deliver.dedupe_key("c-1", "e-1") != deliver.dedupe_key("c-1", "e-2")


# --------------------------------------------------------------- what is sent


def test_the_payload_carries_a_link_never_the_report():
    body = deliver.payload("report.rendered", RENDERED)

    assert body["document_url"] == "/v1/reports/aaaa/document"
    assert "<!doctype html>" not in json.dumps(body)
    assert "documents" not in body


def test_links_are_absolute_when_a_public_address_is_known():
    """A webhook receiver has no idea what the gateway's address is, and a
    relative url in an email is a link that goes nowhere."""
    body = deliver.payload("report.rendered", RENDERED, "https://seo.example.com/api")
    assert body["document_url"] == "https://seo.example.com/api/v1/reports/aaaa/document"


def test_an_already_absolute_link_is_left_alone():
    event = {**RENDERED, "document_url": "https://elsewhere.test/doc"}
    body = deliver.payload("report.rendered", event, "https://seo.example.com")
    assert body["document_url"] == "https://elsewhere.test/doc"


def test_a_workflow_event_carries_the_headline_numbers():
    body = deliver.payload("workflow.completed", COMPLETED)
    assert body["overall_score"] == 87
    assert body["total_issues"] == 18
    assert body["result_url"] == "/v1/workflows/wwww"


def test_a_missing_link_is_null_not_a_broken_url():
    body = deliver.payload("report.rendered", {**RENDERED, "document_url": None},
                           "https://seo.example.com")
    assert body["document_url"] is None


# ------------------------------------------------------------------ signing


def test_the_signature_is_reproducible_by_the_receiver():
    raw = json.dumps({"event": "report.rendered"}, separators=(",", ":")).encode()
    expected = hmac.new(b"topsecret", raw, hashlib.sha256).hexdigest()

    assert deliver.signature("topsecret", raw) == f"sha256={expected}"


def test_a_different_body_gives_a_different_signature():
    assert deliver.signature("s", b"a") != deliver.signature("s", b"b")


def test_a_different_secret_gives_a_different_signature():
    assert deliver.signature("s1", b"a") != deliver.signature("s2", b"a")


# ------------------------------------------------------------------ retrying


@pytest.mark.parametrize("status", [500, 502, 503, 504, 429, 408])
def test_a_bad_minute_is_worth_another_attempt(status):
    assert deliver.retryable(status) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 422])
def test_a_made_up_mind_is_not(status):
    """Retrying a permanent failure forever is how a queue fills with work
    that can never succeed."""
    assert deliver.retryable(status) is False


def test_no_response_at_all_is_worth_another_attempt():
    # Connection refused, DNS, timeout: the far side may be restarting.
    assert deliver.retryable(None, "ConnectionError") is True


# ------------------------------------------------------------------- the mail


def test_the_subject_is_the_report_title_when_there_is_one():
    body = deliver.payload("report.rendered", RENDERED)
    assert deliver.subject("report.rendered", body) == RENDERED["title"]


def test_the_subject_falls_back_to_the_score():
    body = deliver.payload("workflow.completed", COMPLETED)
    assert "87" in deliver.subject("workflow.completed", body)


def test_the_mail_body_is_plain_text_with_the_link():
    """It renders in every client, it cannot carry a tracking pixel, and the
    report is one click away."""
    body = deliver.payload("report.rendered", RENDERED, "https://seo.example.com")
    text = deliver.text("report.rendered", body)

    assert "<" not in text
    assert "https://seo.example.com/v1/reports/aaaa/document" in text


def test_the_mail_says_which_summary_the_report_holds():
    body = deliver.payload("report.rendered", RENDERED)
    assert "قانون‌محور" in deliver.text("report.rendered", body)
