"""The internal links service: job lifecycle, events, worker, and the fetch.

The crawl service is stubbed. Unlike the SERP provider that is not an honest
boundary problem — the crawl service is ours and is tested elsewhere — it is
just that a test should not need a second process running. What is exercised
for real here is every decision this service makes about a report once it has
one, and every way fetching that report can go wrong.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from services.internal_links import source  # noqa: E402
from shared import upstream  # noqa: E402
from shared.contracts import validate_event  # noqa: E402
from shared.events import Envelope  # noqa: E402

# Grabbed before the autouse fixture replaces it: `api.source` is this same
# module, so stubbing the fetch for the service tests stubs it everywhere.
REAL_FETCH = source.fetch_report

HOME = "https://site.test/"
ABOUT = "https://site.test/about"
POST = "https://site.test/blog/post"

REPORT = {
    "start_url": HOME,
    "pages": [
        {"url": HOME, "title": "خانه", "status": 200, "indexable": True, "depth": 0, "words": 300},
        {"url": ABOUT, "title": "درباره", "status": 200, "indexable": True, "depth": 1, "words": 200},
        {"url": POST, "title": "مقاله", "status": 200, "indexable": True, "depth": 3, "words": 1500},
    ],
    "links": {
        "edges": [{"from": HOME, "to": ABOUT, "anchor": "درباره ما", "nofollow": False}],
        "truncated": False,
    },
}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKS_DATA_DIR", str(tmp_path / "links"))
    monkeypatch.setenv("EVENTS_ENABLED", "0")

    from services.internal_links import api
    from services.internal_links.store import AnalysisStore

    api.store = AnalysisStore(tmp_path / "links")
    api._publisher = None
    monkeypatch.setattr(api.source, "fetch_report", lambda crawl_id, tenant_id=None: REPORT)
    yield api


@pytest.fixture
def client(isolated):
    return TestClient(isolated.app)


# ------------------------------------------------------------------- the api


def test_an_analysis_runs_end_to_end_and_is_readable(client):
    accepted = client.post("/v1/link-analyses", json={"crawl_id": "crawl-1"})
    assert accepted.status_code == 202
    analysis_id = accepted.json()["analysis_id"]

    body = client.get(f"/v1/link-analyses/{analysis_id}").json()
    assert body["status"] == "completed"
    assert body["crawl_id"] == "crawl-1"
    assert body["report"]["pages"] == 3
    # The post has content and nothing links to it — the finding this service
    # exists for.
    assert POST in body["report"]["orphans"]


def test_an_unknown_analysis_is_404(client):
    assert client.get(f"/v1/link-analyses/{uuid.uuid4()}").status_code == 404


def test_a_request_without_a_crawl_is_rejected(client):
    assert client.post("/v1/link-analyses", json={}).status_code == 422


def test_an_analysis_is_only_readable_by_the_tenant_that_owns_it(isolated, client):
    isolated.store.create("a-1", "crawl-1", tenant_id="tenant-a")

    assert client.get("/v1/link-analyses/a-1", params={"tenant_id": "tenant-a"}).status_code == 200
    assert client.get("/v1/link-analyses/a-1", params={"tenant_id": "tenant-b"}).status_code == 404


# ----------------------------------------------------------------- the events


def test_completion_emits_a_contract_valid_event(isolated, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))

    analysis_id = str(uuid.uuid4())
    isolated.store.create(analysis_id, "crawl-1")
    isolated.run_analysis(analysis_id, "crawl-1")

    event_type, payload = events[0]
    assert event_type == "links.analyzed"
    validate_event("links.analyzed", payload)
    assert payload["pages"] == 3
    assert payload["orphan_count"] == 1
    assert payload["result_url"] == f"/v1/link-analyses/{analysis_id}"


def test_the_event_carries_a_headline_not_the_graph(isolated, monkeypatch):
    """A graph is thousands of edges. Putting it on the bus would push every
    consumer's message size up for data none of them read."""
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))

    analysis_id = str(uuid.uuid4())
    isolated.store.create(analysis_id, "crawl-1")
    isolated.run_analysis(analysis_id, "crawl-1")

    _, payload = events[0]
    assert "edges" not in payload
    assert "hubs" not in payload


def test_a_failed_fetch_still_reports(isolated, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))
    monkeypatch.setattr(isolated.source, "fetch_report", _raise(source.ReportUnavailable("boom")))

    analysis_id = str(uuid.uuid4())
    isolated.store.create(analysis_id, "crawl-1")
    record = isolated.run_analysis(analysis_id, "crawl-1")

    assert record.status == "failed"
    _, payload = events[0]
    validate_event("links.analyzed", payload)
    assert payload["status"] == "failed"
    assert "boom" in payload["error"]


def test_an_invalid_payload_is_never_published(isolated, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))
    # A contract violation is our bug; broadcasting it to every consumer makes
    # it their bug too.
    monkeypatch.setattr(isolated, "analyzed_payload", lambda *a: {"nonsense": True})

    analysis_id = str(uuid.uuid4())
    isolated.store.create(analysis_id, "crawl-1")
    isolated.run_analysis(analysis_id, "crawl-1")
    assert events == []


# ----------------------------------------------------------------- the worker


def test_the_worker_runs_a_requested_analysis(isolated, monkeypatch):
    from services.internal_links import worker

    worker._seen.clear()
    analysis_id = str(uuid.uuid4())
    worker.handle(Envelope(
        type="links.analysis_requested",
        payload={"analysis_id": analysis_id, "crawl_id": "crawl-1"},
        producer="orchestrator",
    ))

    assert isolated.store.get(analysis_id).status == "completed"


def test_the_worker_ignores_a_redelivered_event(isolated, monkeypatch):
    from services.internal_links import worker

    worker._seen.clear()
    calls: list[str] = []
    monkeypatch.setattr(isolated, "run_analysis", lambda *a, **kw: calls.append(a[0]))

    envelope = Envelope(
        type="links.analysis_requested",
        payload={"analysis_id": str(uuid.uuid4()), "crawl_id": "crawl-1"},
        producer="orchestrator",
    )
    worker.handle(envelope)
    worker.handle(envelope)
    assert len(calls) == 1


def test_the_worker_carries_tenancy_from_the_envelope(isolated):
    from services.internal_links import worker

    worker._seen.clear()
    analysis_id = str(uuid.uuid4())
    worker.handle(Envelope(
        type="links.analysis_requested",
        payload={"analysis_id": analysis_id, "crawl_id": "crawl-1"},
        producer="orchestrator",
        tenant_id="tenant-a",
    ))

    # From the envelope, not the payload: the payload is caller-supplied and
    # the envelope is what the outbox stamped.
    assert isolated.store.get(analysis_id).tenant_id == "tenant-a"


def test_the_worker_dead_letters_a_malformed_request(isolated):
    from services.internal_links import worker

    worker._seen.clear()
    with pytest.raises(ValueError):
        worker.handle(Envelope(
            type="links.analysis_requested", payload={"crawl": "wrong key"},
            producer="orchestrator",
        ))


# ------------------------------------------------------------------ the fetch


def test_a_missing_crawl_is_not_retryable(monkeypatch):
    """404 means the id is wrong or belongs to someone else. Retrying that
    forever is how a queue fills up with work that can never succeed."""
    monkeypatch.setattr(upstream.requests, "get", lambda *a, **kw: _Response(404))
    with pytest.raises(source.UnknownCrawl):
        REAL_FETCH("nope")


def test_a_crawl_still_running_is_retryable(monkeypatch):
    monkeypatch.setattr(
        upstream.requests, "get",
        lambda *a, **kw: _Response(200, {"status": "running", "report": None}),
    )
    with pytest.raises(source.ReportUnavailable):
        REAL_FETCH("crawl-1")


def test_an_unreachable_crawl_service_is_retryable(monkeypatch):
    monkeypatch.setattr(upstream.requests, "get", _raise(ConnectionError("refused")))
    with pytest.raises(source.ReportUnavailable):
        REAL_FETCH("crawl-1")


def test_the_tenant_travels_with_the_fetch(monkeypatch):
    """Without it this service would be a way to read any tenant's crawl by
    id — the hole just closed at the gateway, reopened one layer down."""
    seen: dict = {}

    def capture(url, params=None, timeout=None):
        seen["params"] = params
        return _Response(200, {"report": REPORT})

    monkeypatch.setattr(upstream.requests, "get", capture)
    REAL_FETCH("crawl-1", "tenant-a")
    assert seen["params"] == {"tenant_id": "tenant-a"}


class _Response:
    def __init__(self, status_code: int, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {}

    def json(self):
        return self._body


def _raise(exc: Exception):
    def raiser(*args, **kwargs):
        raise exc
    return raiser
