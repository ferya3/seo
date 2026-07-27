"""Subscriptions, send-once, and the worker — against a real Postgres.

Send-once is enforced by a unique index, and a unique index is not something a
fake can demonstrate. This is the same discipline as `shared/tests`: the value
of the thing under test *is* the database behaviour.

    TEST_DATABASE_URL=postgresql://seo@127.0.0.1/seo pytest services/notifications
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

psycopg = pytest.importorskip("psycopg")

DSN = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not set")

from fastapi.testclient import TestClient  # noqa: E402

from services.notifications import api, channels  # noqa: E402
from services.notifications.store import NotificationStore  # noqa: E402
from shared.events import Envelope  # noqa: E402

RENDERED = {
    "report_id": "aaaaaaaa-0000-0000-0000-000000000000",
    "workflow_id": "wwwwwwww-0000-0000-0000-000000000000",
    "title": "گزارش سئو",
    "status": "completed",
    "formats": ["html", "md"],
    "document_url": "/v1/reports/aaaa/document",
}


@pytest.fixture(autouse=True)
def strict_guard(monkeypatch):
    """A notification target is judged by the production rules, whatever the
    rest of the suite has set."""
    monkeypatch.delenv("SEO_AGENT_ALLOW_PRIVATE", raising=False)
    from shared import netguard
    netguard.reset_cache()
    yield
    netguard.reset_cache()


@pytest.fixture
def store():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("TRUNCATE notification_deliveries, notification_channels CASCADE")
    s = NotificationStore(DSN)
    api.reset_store(s)
    yield s
    api.reset_store(None)
    s.close()


@pytest.fixture
def client(store):
    return TestClient(api.app)


@pytest.fixture
def sent(monkeypatch):
    """Every send captured, none of them leaving the process."""
    calls: list[tuple] = []
    monkeypatch.setattr(
        channels, "send",
        lambda channel, event_type, body: (
            calls.append((channel["id"], event_type, body)), channels.Result(ok=True, status=200)
        )[1],
    )
    return calls


# ------------------------------------------------------------------ channels


def test_a_webhook_channel_is_created_with_a_secret_shown_once(client):
    # A resolvable public host, because the guard refuses what it cannot
    # resolve — it has no way to prove an unknown name is not internal.
    created = client.post("/v1/channels", json={
        "kind": "webhook", "target": "https://example.com/hooks/seo",
    })
    assert created.status_code == 201
    body = created.json()
    assert body["secret"]

    # Never again: a listing that hands out signing secrets leaks them into
    # logs and screenshots.
    listed = client.get("/v1/channels").json()
    assert listed[0]["secret"] is None
    assert listed[0]["has_secret"] is True


def test_a_webhook_pointing_inside_the_network_is_refused_at_creation(client):
    """Caught when it is typed, not when the first report goes missing."""
    refused = client.post("/v1/channels", json={
        "kind": "webhook", "target": "http://169.254.169.254/",
    })
    assert refused.status_code == 422
    assert "not allowed" in refused.json()["detail"]


def test_a_target_that_does_not_resolve_is_refused(client):
    """The guard cannot prove an unknown name is not internal, so it says no.
    Better a rejected form than a webhook that quietly reaches a LAN."""
    assert client.post("/v1/channels", json={
        "kind": "webhook", "target": "https://nowhere.invalid/x",
    }).status_code == 422


def test_an_email_channel_needs_an_address(client):
    assert client.post(
        "/v1/channels", json={"kind": "email", "target": "not-an-address"}
    ).status_code == 422


def test_an_email_channel_gets_no_secret(client):
    body = client.post(
        "/v1/channels", json={"kind": "email", "target": "someone@example.com"}
    ).json()
    assert body["has_secret"] is False


def test_subscribing_to_an_event_nobody_publishes_is_refused(client):
    """A subscription to an event that never fires is a silence nobody can
    explain later."""
    assert client.post("/v1/channels", json={
        "kind": "email", "target": "a@example.com", "events": ["crawl.exploded"],
    }).status_code == 422


def test_channels_are_listed_per_tenant(client, store):
    store.add_channel("email", "a@example.com", tenant_id=None)

    assert len(client.get("/v1/channels").json()) == 1
    assert client.get("/v1/channels", params={"tenant_id": str(uuid.uuid4())}).json() == []


def test_deleting_someone_elses_channel_is_a_404(client, store):
    channel = store.add_channel("email", "a@example.com", tenant_id=None)
    assert client.delete(
        f"/v1/channels/{channel.id}", params={"tenant_id": str(uuid.uuid4())}
    ).status_code == 404
    assert client.delete(f"/v1/channels/{channel.id}").status_code == 204


def test_a_channel_can_be_tested_before_a_real_report_needs_it(client, store, sent):
    channel = store.add_channel("webhook", "https://hooks.example.com/x")
    result = client.post(f"/v1/channels/{channel.id}/test").json()

    assert result["ok"] is True
    assert sent[0][2]["title"] == "پیام آزمایشی از ایجنت سئو"


# ----------------------------------------------------------------- send once


def test_the_same_event_delivered_twice_sends_once(store, sent):
    """Delivery is at-least-once. Without the unique index this is two emails
    about one report."""
    from services.notifications import worker

    store.add_channel("email", "someone@example.com")
    envelope = Envelope(type="report.rendered", payload=RENDERED, producer="reporting-service")

    worker.handle(envelope)
    worker.handle(envelope)

    assert len(sent) == 1


def test_two_workers_racing_the_same_event_send_once(store, sent):
    """The claim is an INSERT with a unique constraint, so the loser of the
    race finds out from the database rather than from a lock it forgot."""
    channel = store.add_channel("email", "someone@example.com")
    event_id = str(uuid.uuid4())

    assert store.claim(channel.id, event_id, "report.rendered") is True
    assert store.claim(channel.id, event_id, "report.rendered") is False


def test_a_different_event_to_the_same_channel_still_sends(store, sent):
    from services.notifications import worker

    store.add_channel("email", "someone@example.com")
    worker.handle(Envelope(type="report.rendered", payload=RENDERED, producer="reporting"))
    worker.handle(Envelope(type="report.rendered", payload=RENDERED, producer="reporting"))

    assert len(sent) == 2       # two envelopes, two event ids


def test_a_retryable_failure_gives_the_claim_back(store, monkeypatch):
    """Otherwise the first transient failure means nobody is ever told."""
    from services.notifications import worker

    channel = store.add_channel("webhook", "https://hooks.example.com/x")
    monkeypatch.setattr(channels, "send",
                        lambda *a: channels.Result(ok=False, status=503, error="HTTP 503"))

    envelope = Envelope(type="report.rendered", payload=RENDERED, producer="reporting")
    worker.handle(envelope)

    # The pair is free again, so the redelivery can take it.
    assert store.claim(channel.id, envelope.id, "report.rendered") is True


def test_a_permanent_failure_keeps_its_record(store, monkeypatch):
    """The row is the answer to "why did nobody get told"."""
    from services.notifications import worker

    channel = store.add_channel("webhook", "https://hooks.example.com/x")
    monkeypatch.setattr(channels, "send",
                        lambda *a: channels.Result(ok=False, status=404, error="HTTP 404"))

    envelope = Envelope(type="report.rendered", payload=RENDERED, producer="reporting")
    worker.handle(envelope)

    assert store.claim(channel.id, envelope.id, "report.rendered") is False
    delivery = store.deliveries(None)[0]
    assert delivery["status"] == "failed"
    assert delivery["http_status"] == 404


# ------------------------------------------------------------------ the worker


def test_only_the_channels_that_asked_are_told(store, sent):
    from services.notifications import worker

    wanted = store.add_channel("email", "a@example.com", events=["report.rendered"])
    store.add_channel("email", "b@example.com", events=["workflow.completed"])

    worker.handle(Envelope(type="report.rendered", payload=RENDERED, producer="reporting"))

    assert [call[0] for call in sent] == [wanted.id]


def test_an_event_for_a_tenant_with_no_channels_sends_nothing(store, sent):
    from services.notifications import worker

    store.add_channel("email", "a@example.com", tenant_id=None)
    worker.handle(Envelope(
        type="report.rendered", payload=RENDERED, producer="reporting",
        tenant_id=str(uuid.uuid4()),
    ))
    assert sent == []


def test_a_delivery_listing_does_not_print_the_whole_address(store, sent):
    """A listing is the sort of thing that ends up in a screenshot, and an
    email address is personal data."""
    from services.notifications import worker

    store.add_channel("email", "someone@example.com")
    worker.handle(Envelope(type="report.rendered", payload=RENDERED, producer="reporting"))

    assert store.deliveries(None)[0]["target"] == "so…@example.com"


def test_the_worker_listens_for_everything_a_channel_can_subscribe_to():
    from services.notifications import deliver, worker

    assert set(worker.ROUTING_KEYS) == set(deliver.SUBSCRIBABLE)
