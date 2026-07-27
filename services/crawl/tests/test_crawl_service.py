"""Crawl service: HTTP API, store durability, event emission, worker idempotency.

The bus is stubbed — these cover our contract compliance and wiring, not
RabbitMQ's behaviour.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "engine"))

from shared.contracts import (  # noqa: E402
    ContractError,
    known_event_types,
    validate_event,
)
from shared.events import Envelope  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point the store at a temp dir and silence the bus before importing."""
    monkeypatch.setenv("CRAWL_DATA_DIR", str(tmp_path / "crawls"))
    monkeypatch.setenv("EVENTS_ENABLED", "0")
    monkeypatch.setenv("SEO_AGENT_ALLOW_PRIVATE", "1")

    from services.crawl import api
    from services.crawl.store import CrawlStore

    api.store = CrawlStore(tmp_path / "crawls")
    api._publisher = None
    yield api


@pytest.fixture
def client(isolated):
    return TestClient(isolated.app)


@pytest.fixture
def captured(isolated, monkeypatch):
    """Collect what would have gone on the bus."""
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        isolated, "_emit", lambda t, p, c, f: (validate_event(t, p), events.append((t, p)))[1]
    )
    return events


# ------------------------------------------------------------------ contracts


def test_every_schema_parses_and_is_named_correctly():
    from shared.contracts import event_schema

    types = known_event_types()
    assert "crawl.completed" in types
    for event_type in types:
        assert "." in event_type, event_type
        schema = event_schema(event_type)
        assert schema["title"] == event_type
        assert schema["type"] == "object"


def test_contract_rejects_a_bad_payload():
    with pytest.raises(ContractError):
        validate_event("crawl.completed", {"crawl_id": "x"})


def test_caused_propagates_the_trace():
    root = Envelope(type="crawl.requested", payload={}, producer="gateway", tenant_id="t1")
    child = root.caused("crawl.completed", {}, "crawl-service")
    assert child.correlation_id == root.id
    assert child.causation_id == root.id
    assert child.tenant_id == "t1"
    assert child.id != root.id


def test_envelope_round_trips_through_bytes():
    import json

    original = Envelope(type="page.updated", payload={"url": "https://e.com"}, producer="crawl-service")
    restored = Envelope.from_bytes(json.dumps(original.to_dict()).encode())
    assert restored.id == original.id
    assert restored.payload == original.payload


def test_envelope_rejects_a_truncated_message():
    with pytest.raises(ValueError):
        Envelope.from_bytes(b'{"id": "x", "type": "a.b"}')


# ------------------------------------------------------------------- the api


def test_healthz(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["service"] == "crawl-service"


def test_crawl_runs_end_to_end_and_is_readable(client, good_site):
    response = client.post("/v1/crawls", json={"start_url": good_site, "max_pages": 4})
    assert response.status_code == 202
    crawl_id = response.json()["crawl_id"]

    # TestClient runs background tasks before returning, so it is already done.
    result = client.get(f"/v1/crawls/{crawl_id}").json()
    assert result["status"] == "completed"
    assert 0 <= result["overall_score"] <= 100
    assert result["report"]["issues"] is not None
    assert result["report"]["pages"]

    listed = client.get("/v1/crawls").json()
    assert any(item["crawl_id"] == crawl_id for item in listed)


def test_unknown_crawl_is_404(client):
    assert client.get(f"/v1/crawls/{uuid.uuid4()}").status_code == 404


def test_internal_targets_are_refused_by_the_service(client, monkeypatch):
    monkeypatch.delenv("SEO_AGENT_ALLOW_PRIVATE", raising=False)
    from seoagent import netguard

    netguard.reset_cache()
    response = client.post("/v1/crawls", json={"start_url": "http://169.254.169.254/"})
    assert response.status_code == 400
    assert "مجاز نیست" in response.json()["detail"]


def test_out_of_range_input_is_rejected_by_the_schema(client):
    assert client.post("/v1/crawls", json={"start_url": "https://e.com", "max_pages": 0}).status_code == 422
    assert client.post("/v1/crawls", json={}).status_code == 422


# ------------------------------------------------------------------- events


def test_completion_emits_a_contract_valid_event(isolated, captured, good_site):
    crawl_id = str(uuid.uuid4())
    isolated.store.create(crawl_id, good_site)
    isolated.run_crawl(crawl_id, {"start_url": good_site, "max_pages": 3})

    types = [t for t, _ in captured]
    assert "crawl.completed" in types

    _, payload = next((t, p) for t, p in captured if t == "crawl.completed")
    assert payload["crawl_id"] == crawl_id
    assert payload["status"] == "completed"
    assert payload["result_url"] == f"/v1/crawls/{crawl_id}"
    validate_event("crawl.completed", payload)  # raises if the shape drifted


def test_each_indexable_page_emits_page_updated(isolated, captured, good_site):
    crawl_id = str(uuid.uuid4())
    isolated.store.create(crawl_id, good_site)
    record = isolated.run_crawl(crawl_id, {"start_url": good_site, "max_pages": 4})

    page_events = [p for t, p in captured if t == "page.updated"]
    indexable = [p for p in record.report["pages"] if p["indexable"]]
    assert len(page_events) == len(indexable)
    for payload in page_events:
        assert payload["content_hash"], "content_hash is what lets consumers skip unchanged pages"
        validate_event("page.updated", payload)


def test_a_failed_crawl_still_reports_completion(isolated, captured, monkeypatch):
    def explode(self):
        raise RuntimeError("network gone")

    monkeypatch.setattr("services.crawl.api.Crawler.crawl", explode)

    crawl_id = str(uuid.uuid4())
    isolated.store.create(crawl_id, "https://example.com")
    record = isolated.run_crawl(crawl_id, {"start_url": "https://example.com"})

    assert record.status == "failed"
    assert "network gone" in record.error
    payload = next(p for t, p in captured if t == "crawl.completed")
    assert payload["status"] == "failed"
    validate_event("crawl.completed", payload)


def test_a_broken_broker_does_not_fail_a_good_crawl(isolated, monkeypatch, good_site):
    """The result is already durable; a publish failure must not rewrite history."""

    class DeadPublisher:
        def emit(self, *args, **kwargs):
            raise ConnectionError("broker down")

        def publish(self, *args, **kwargs):
            raise ConnectionError("broker down")

    monkeypatch.setattr(isolated, "publisher", lambda: DeadPublisher())

    crawl_id = str(uuid.uuid4())
    isolated.store.create(crawl_id, good_site)
    record = isolated.run_crawl(crawl_id, {"start_url": good_site, "max_pages": 3})
    assert record.status == "completed"


def test_an_invalid_payload_is_never_published(isolated, monkeypatch):
    published: list[str] = []

    class Recorder:
        def emit(self, event_type, payload, **kwargs):
            published.append(event_type)

        def publish(self, envelope):
            published.append(envelope.type)

    monkeypatch.setattr(isolated, "publisher", lambda: Recorder())
    isolated._emit("crawl.completed", {"crawl_id": "only-this"}, None, {})
    assert published == []


# ------------------------------------------------------------------- store


def test_results_survive_a_restart(isolated, tmp_path, good_site):
    from services.crawl.store import CrawlStore

    crawl_id = str(uuid.uuid4())
    isolated.store.create(crawl_id, good_site)
    isolated.store.complete(crawl_id, {"overall_score": 91, "stats": {"total_issues": 2}})

    fresh = CrawlStore(tmp_path / "crawls")
    restored = fresh.get(crawl_id)
    assert restored is not None
    assert restored.status == "completed"
    assert restored.report["overall_score"] == 91


def test_a_readonly_volume_degrades_to_memory(tmp_path):
    from services.crawl.store import CrawlStore

    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x", encoding="utf-8")

    store = CrawlStore(blocker)
    record = store.create("abc", "https://e.com")
    assert store.get("abc") is record


# ------------------------------------------------------------------- worker


def test_worker_runs_a_requested_crawl(isolated, monkeypatch, good_site):
    from services.crawl import worker

    calls: list[str] = []
    monkeypatch.setattr(worker.api, "run_crawl", lambda cid, payload, **kw: calls.append(cid))
    worker._seen.clear()

    crawl_id = str(uuid.uuid4())
    worker.handle(
        Envelope(
            type="crawl.requested",
            payload={"crawl_id": crawl_id, "start_url": good_site, "max_pages": 2},
            producer="gateway",
        )
    )
    assert calls == [crawl_id]


def test_worker_ignores_a_redelivered_event(isolated, monkeypatch, good_site):
    from services.crawl import worker

    calls: list[str] = []
    monkeypatch.setattr(worker.api, "run_crawl", lambda cid, payload, **kw: calls.append(cid))
    worker._seen.clear()

    envelope = Envelope(
        type="crawl.requested",
        payload={"crawl_id": str(uuid.uuid4()), "start_url": good_site},
        producer="gateway",
    )
    worker.handle(envelope)
    worker.handle(envelope)  # at-least-once delivery redelivers
    assert len(calls) == 1


def test_worker_skips_a_crawl_that_already_ran(isolated, monkeypatch, good_site):
    from services.crawl import worker

    calls: list[str] = []
    monkeypatch.setattr(worker.api, "run_crawl", lambda cid, payload, **kw: calls.append(cid))
    worker._seen.clear()

    crawl_id = str(uuid.uuid4())
    isolated.store.create(crawl_id, good_site)
    isolated.store.complete(crawl_id, {"overall_score": 50})

    worker.handle(
        Envelope(
            type="crawl.requested",
            payload={"crawl_id": crawl_id, "start_url": good_site},
            producer="gateway",
        )
    )
    assert calls == []


def test_worker_dead_letters_a_malformed_request(isolated):
    from services.crawl import worker

    worker._seen.clear()
    with pytest.raises(ValueError):
        worker.handle(
            Envelope(type="crawl.requested", payload={"nope": 1}, producer="gateway")
        )


# --------------------------------------------------------------------- tenancy


def test_a_crawl_is_only_readable_by_the_tenant_that_owns_it(isolated, client):
    """Reads used to ignore tenancy entirely: writes were scoped by a foreign
    key, reads were not, so one id was enough to read another account's report.
    Found by reading the gateway while building a UI on top of it."""
    isolated.store.create("c-1", "https://example.com", tenant_id="tenant-a")

    assert client.get("/v1/crawls/c-1", params={"tenant_id": "tenant-a"}).status_code == 200
    # 404, not 403: a 403 would confirm the id exists.
    assert client.get("/v1/crawls/c-1", params={"tenant_id": "tenant-b"}).status_code == 404


def test_a_listing_shows_only_the_asking_tenants_crawls(isolated, client):
    isolated.store.create("c-1", "https://mine.example", tenant_id="tenant-a")
    isolated.store.create("c-2", "https://theirs.example", tenant_id="tenant-b")

    listed = client.get("/v1/crawls", params={"tenant_id": "tenant-a"}).json()
    assert [row["crawl_id"] for row in listed] == ["c-1"]


def test_a_listing_is_not_starved_by_another_tenants_rows(isolated, client):
    """The first cut read `limit` files and filtered afterwards, so a tenant
    with two crawls saw none if ten of someone else's were newer."""
    for i in range(10):
        isolated.store.create(f"theirs-{i}", "https://theirs.example", tenant_id="tenant-b")
    isolated.store.create("mine-1", "https://mine.example", tenant_id="tenant-a")

    listed = client.get("/v1/crawls", params={"limit": 5, "tenant_id": "tenant-a"}).json()
    assert [row["crawl_id"] for row in listed] == ["mine-1"]


def test_a_worker_reading_its_own_job_is_not_filtered(isolated):
    """tenant_id=None means "do not filter" — the mode a worker uses when it
    reads back a job it is already running."""
    isolated.store.create("c-1", "https://example.com", tenant_id="tenant-a")

    assert isolated.store.get("c-1") is not None


def test_tenancy_survives_a_restart(isolated, tmp_path):
    """Files were written from `to_dict`, which each service overrides for its
    public payload — and none of them included tenant_id. The record answered
    correctly from memory and lost its owner the moment it was read back from
    disk, so a tenant's list emptied itself when the process restarted."""
    from services.crawl.store import CrawlStore

    isolated.store.create("c-1", "https://example.com", tenant_id="tenant-a")

    fresh = CrawlStore(tmp_path / "crawls")            # nothing cached in memory
    assert fresh.get("c-1", tenant_id="tenant-a") is not None
    assert fresh.get("c-1", tenant_id="tenant-b") is None
