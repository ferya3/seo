"""The content service: job lifecycle, events, worker, and the two fetches.

Both upstream services are stubbed — they are ours and tested elsewhere, and a
test should not need two more processes running. What is exercised for real is
every decision this service makes once it has the two reports, and what happens
when either of them cannot be had.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from services.content import source  # noqa: E402
from shared import upstream  # noqa: E402
from shared.contracts import validate_event  # noqa: E402
from shared.events import Envelope  # noqa: E402

# Grabbed before the autouse fixture replaces them: `api.source` is this same
# module, so stubbing the fetches for the service tests stubs them everywhere.
REAL_CRAWL_FETCH = source.fetch_crawl
REAL_RESEARCH_FETCH = source.fetch_research

CRAWL = {
    "start_url": "https://site.test/",
    "pages": [
        {"url": "https://site.test/", "title": "فروشگاه کفش ورزشی", "status": 200,
         "indexable": True, "words": 400, "headings": [], "description": None},
        {"url": "https://site.test/about", "title": "درباره ما", "status": 200,
         "indexable": True, "words": 120, "headings": [], "description": None},
    ],
}
RESEARCH = {"keywords": [
    {"keyword": "کفش ورزشی", "demand": 900, "intent": "commercial"},
    {"keyword": "کفش کوهنوردی", "demand": 700, "intent": "commercial"},
]}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_DATA_DIR", str(tmp_path / "content"))
    monkeypatch.setenv("EVENTS_ENABLED", "0")

    from services.content import api
    from services.content.store import ContentStore

    api.store = ContentStore(tmp_path / "content")
    api._publisher = None
    monkeypatch.setattr(api.source, "fetch_crawl", lambda crawl_id, tenant_id=None: CRAWL)
    monkeypatch.setattr(api.source, "fetch_research", lambda research_id, tenant_id=None: RESEARCH)
    yield api


@pytest.fixture
def client(isolated):
    return TestClient(isolated.app)


# ------------------------------------------------------------------- the api


def test_an_analysis_runs_end_to_end_and_is_readable(client):
    accepted = client.post(
        "/v1/content-analyses", json={"crawl_id": "crawl-1", "research_id": "research-1"}
    )
    assert accepted.status_code == 202
    analysis_id = accepted.json()["analysis_id"]

    body = client.get(f"/v1/content-analyses/{analysis_id}").json()
    assert body["status"] == "completed"
    assert body["report"]["covered"] == 1
    assert body["report"]["gaps"][0]["keyword"] == "کفش کوهنوردی"


def test_both_ids_are_required(client):
    assert client.post("/v1/content-analyses", json={"crawl_id": "c"}).status_code == 422
    assert client.post("/v1/content-analyses", json={"research_id": "r"}).status_code == 422


def test_an_unknown_analysis_is_404(client):
    assert client.get(f"/v1/content-analyses/{uuid.uuid4()}").status_code == 404


def test_an_analysis_is_only_readable_by_the_tenant_that_owns_it(isolated, client):
    isolated.store.create("a-1", "crawl-1", tenant_id="tenant-a")

    assert client.get(
        "/v1/content-analyses/a-1", params={"tenant_id": "tenant-a"}
    ).status_code == 200
    assert client.get(
        "/v1/content-analyses/a-1", params={"tenant_id": "tenant-b"}
    ).status_code == 404


# ----------------------------------------------------------------- the events


def test_completion_emits_a_contract_valid_event(isolated, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))

    analysis_id = str(uuid.uuid4())
    isolated.store.create(analysis_id, "crawl-1")
    isolated.run_analysis(analysis_id, "crawl-1", "research-1")

    event_type, payload = events[0]
    assert event_type == "content.analyzed"
    validate_event("content.analyzed", payload)
    assert payload["coverage"] == 50.0
    assert payload["top_gaps"][0]["keyword"] == "کفش کوهنوردی"


def test_the_event_carries_the_gaps_not_every_page(isolated, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))

    analysis_id = str(uuid.uuid4())
    isolated.store.create(analysis_id, "crawl-1")
    isolated.run_analysis(analysis_id, "crawl-1", "research-1")

    _, payload = events[0]
    assert "untargeted_pages" not in payload
    assert "cannibalisation" not in payload      # the count travels, not the list


def test_a_missing_research_report_fails_the_job_and_says_so(isolated, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))
    monkeypatch.setattr(
        isolated.source, "fetch_research", _raise(source.ReportUnavailable("still running"))
    )

    analysis_id = str(uuid.uuid4())
    isolated.store.create(analysis_id, "crawl-1")
    record = isolated.run_analysis(analysis_id, "crawl-1", "research-1")

    assert record.status == "failed"
    _, payload = events[0]
    validate_event("content.analyzed", payload)
    assert "still running" in payload["error"]


def test_an_invalid_payload_is_never_published(isolated, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))
    monkeypatch.setattr(isolated, "analyzed_payload", lambda *a: {"nonsense": True})

    analysis_id = str(uuid.uuid4())
    isolated.store.create(analysis_id, "crawl-1")
    isolated.run_analysis(analysis_id, "crawl-1", "research-1")
    assert events == []


# ----------------------------------------------------------------- the worker


def test_the_worker_runs_a_requested_analysis(isolated):
    from services.content import worker

    worker._seen.clear()
    analysis_id = str(uuid.uuid4())
    worker.handle(Envelope(
        type="content.analysis_requested",
        payload={"analysis_id": analysis_id, "crawl_id": "crawl-1", "research_id": "research-1"},
        producer="orchestrator",
        tenant_id="tenant-a",
    ))

    record = isolated.store.get(analysis_id)
    assert record.status == "completed"
    # From the envelope, not the payload.
    assert record.tenant_id == "tenant-a"


def test_the_worker_ignores_a_redelivered_event(isolated, monkeypatch):
    from services.content import worker

    worker._seen.clear()
    calls: list[str] = []
    monkeypatch.setattr(isolated, "run_analysis", lambda *a, **kw: calls.append(a[0]))

    envelope = Envelope(
        type="content.analysis_requested",
        payload={"analysis_id": str(uuid.uuid4()), "crawl_id": "c", "research_id": "r"},
        producer="orchestrator",
    )
    worker.handle(envelope)
    worker.handle(envelope)
    assert len(calls) == 1


def test_the_worker_dead_letters_a_request_missing_the_research(isolated):
    """Half the inputs is not something a redelivery fixes."""
    from services.content import worker

    worker._seen.clear()
    with pytest.raises(ValueError):
        worker.handle(Envelope(
            type="content.analysis_requested", payload={"crawl_id": "c"},
            producer="orchestrator",
        ))


# ------------------------------------------------------------------ the fetch


def test_each_report_comes_from_its_own_service(monkeypatch):
    seen: list[str] = []

    def capture(url, params=None, timeout=None):
        seen.append(url)
        return _Response(200, {"report": {"ok": True}})

    monkeypatch.setattr(upstream.requests, "get", capture)
    REAL_CRAWL_FETCH("crawl-1")
    REAL_RESEARCH_FETCH("research-1")

    assert seen[0].endswith("/v1/crawls/crawl-1")
    assert seen[1].endswith("/v1/research/research-1")
    assert seen[0].split("/v1")[0] != seen[1].split("/v1")[0]


def test_the_tenant_travels_with_both_fetches(monkeypatch):
    seen: list[dict] = []

    def capture(url, params=None, timeout=None):
        seen.append(params)
        return _Response(200, {"report": {"ok": True}})

    monkeypatch.setattr(upstream.requests, "get", capture)
    REAL_CRAWL_FETCH("crawl-1", "tenant-a")
    REAL_RESEARCH_FETCH("research-1", "tenant-a")

    assert seen == [{"tenant_id": "tenant-a"}, {"tenant_id": "tenant-a"}]


def test_a_missing_upstream_record_is_not_retryable(monkeypatch):
    monkeypatch.setattr(upstream.requests, "get", lambda *a, **kw: _Response(404))
    with pytest.raises(source.UnknownRecord):
        REAL_CRAWL_FETCH("nope")


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
