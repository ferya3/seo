"""The reporting service: rendering, serving the file, and the two triggers.

The orchestrator is stubbed. What is exercised here is what happens around the
document — that it is served as a file rather than a JSON string, that a
workflow gets exactly one automatic report no matter how many times its
completion event arrives, and that the bus is never asked to carry the markup.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from services.reporting import source  # noqa: E402
from shared import upstream  # noqa: E402
from shared.contracts import validate_event  # noqa: E402
from shared.events import Envelope  # noqa: E402

REAL_FETCH = source.fetch_workflow

WORKFLOW = {
    "workflow_id": "11111111-2222-3333-4444-555555555555",
    "status": "completed",
    "created_at": "2026-07-27T09:00:00+00:00",
    "inputs": {"start_url": "https://site.test/"},
    "steps": [{"position": 1, "kind": "crawl", "status": "completed", "error": None}],
    "report": {
        "headline": {"overall_score": 87},
        "summary": {"source": "rules", "text_fa": "خلاصه‌ی آزمایشی.", "next_actions": []},
    },
}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("EVENTS_ENABLED", "0")

    from services.reporting import api
    from services.reporting.store import DocumentStore

    api.store = DocumentStore(tmp_path / "reports")
    api._publisher = None
    monkeypatch.setattr(api.source, "fetch_workflow", lambda wid, tenant_id=None: WORKFLOW)
    yield api


@pytest.fixture
def client(isolated):
    return TestClient(isolated.app)


# ------------------------------------------------------------------- the api


def test_a_report_renders_both_formats(client):
    accepted = client.post("/v1/reports", json={"workflow_id": "w-1"})
    assert accepted.status_code == 202
    report_id = accepted.json()["report_id"]

    body = client.get(f"/v1/reports/{report_id}").json()
    assert body["status"] == "completed"
    assert body["formats"] == ["html", "md"]
    assert body["title"] == "گزارش سئو — https://site.test/"


def test_the_document_is_served_as_a_file_not_a_json_string(client):
    report_id = client.post("/v1/reports", json={"workflow_id": "w-1"}).json()["report_id"]

    page = client.get(f"/v1/reports/{report_id}/document")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "filename=" in page.headers["content-disposition"]
    assert page.text.startswith("<!doctype html>")


def test_markdown_is_served_with_its_own_type(client):
    report_id = client.post("/v1/reports", json={"workflow_id": "w-1"}).json()["report_id"]

    document = client.get(f"/v1/reports/{report_id}/document", params={"format": "md"})
    assert document.headers["content-type"].startswith("text/markdown")
    assert document.text.startswith("# گزارش سئو")


def test_an_unknown_format_is_404_not_an_empty_file(client):
    report_id = client.post("/v1/reports", json={"workflow_id": "w-1"}).json()["report_id"]
    assert client.get(
        f"/v1/reports/{report_id}/document", params={"format": "pdf"}
    ).status_code == 404


def test_the_listing_does_not_carry_the_markup(client):
    """An HTML report is tens of kilobytes. A list of twenty of them would be
    a megabyte of markup nobody asked for."""
    client.post("/v1/reports", json={"workflow_id": "w-1"})
    body = client.get("/v1/reports").json()

    assert body[0]["formats"] == ["html", "md"]
    assert "documents" not in str(body)


def test_an_unknown_report_is_404(client):
    assert client.get(f"/v1/reports/{uuid.uuid4()}").status_code == 404


def test_a_report_is_only_readable_by_the_tenant_that_owns_it(isolated, client):
    isolated.store.create("r-1", "w-1", tenant_id="tenant-a")

    assert client.get("/v1/reports/r-1", params={"tenant_id": "tenant-a"}).status_code == 200
    assert client.get("/v1/reports/r-1", params={"tenant_id": "tenant-b"}).status_code == 404


def test_another_tenant_cannot_download_the_document(isolated, client):
    """The document endpoint is a second door to the same data; scoping the
    metadata and forgetting the file would leave the report readable."""
    report_id = client.post(
        "/v1/reports", json={"workflow_id": "w-1", "tenant_id": None}
    ).json()["report_id"]
    isolated.store.update(report_id, tenant_id="tenant-a")

    assert client.get(
        f"/v1/reports/{report_id}/document", params={"tenant_id": "tenant-b"}
    ).status_code == 404


# ----------------------------------------------------------------- the events


def test_completion_emits_a_contract_valid_event(isolated, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))

    report_id = str(uuid.uuid4())
    isolated.store.create(report_id, "w-1")
    isolated.run_report(report_id, "w-1")

    event_type, payload = events[0]
    assert event_type == "report.rendered"
    validate_event("report.rendered", payload)
    assert payload["formats"] == ["html", "md"]
    assert payload["document_url"] == f"/v1/reports/{report_id}/document"


def test_the_event_carries_a_link_not_the_document(isolated, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))

    report_id = str(uuid.uuid4())
    isolated.store.create(report_id, "w-1")
    isolated.run_report(report_id, "w-1")

    _, payload = events[0]
    assert "<!doctype html>" not in str(payload)


def test_the_event_says_which_summary_the_document_captured(isolated, monkeypatch):
    """A report rendered the moment a workflow finishes can hold the rule
    summary while the model's is still being written."""
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))

    report_id = str(uuid.uuid4())
    isolated.store.create(report_id, "w-1")
    isolated.run_report(report_id, "w-1")

    assert events[0][1]["summary_source"] == "rules"


def test_an_unreachable_orchestrator_fails_the_report_and_says_so(isolated, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))
    monkeypatch.setattr(isolated.source, "fetch_workflow", _raise(
        source.ReportUnavailable("orchestrator unreachable")))

    report_id = str(uuid.uuid4())
    isolated.store.create(report_id, "w-1")
    record = isolated.run_report(report_id, "w-1")

    assert record.status == "failed"
    validate_event("report.rendered", events[0][1])
    assert "unreachable" in events[0][1]["error"]


# ----------------------------------------------------------------- the worker


def test_a_finished_workflow_gets_a_document_without_anyone_asking(isolated):
    """The orchestrator does not know this service exists. That is the whole
    reason there is a bus."""
    from services.reporting import worker

    worker._seen.clear()
    worker.handle(Envelope(
        type="workflow.completed",
        payload={"workflow_id": "w-1", "status": "completed"},
        producer="orchestrator",
        tenant_id="tenant-a",
    ))

    reports = isolated.store.recent(10)
    assert len(reports) == 1
    assert reports[0].workflow_id == "w-1"
    assert reports[0].tenant_id == "tenant-a"


def test_a_workflow_gets_one_automatic_report_however_often_it_is_delivered(isolated):
    """Without the check every redelivery renders the same report under a new
    id and the list fills with copies."""
    from services.reporting import worker

    worker._seen.clear()
    for _ in range(3):
        worker.handle(Envelope(
            type="workflow.completed", payload={"workflow_id": "w-1"},
            producer="orchestrator",
        ))
        worker._seen.clear()                 # past the cheap in-memory guard

    assert len(isolated.store.recent(10)) == 1


def test_an_explicit_request_renders_even_when_one_exists(isolated):
    """Asking again is how someone picks up the model-written summary that
    landed after the automatic report was made."""
    from services.reporting import worker

    worker._seen.clear()
    worker.handle(Envelope(
        type="workflow.completed", payload={"workflow_id": "w-1"}, producer="orchestrator",
    ))
    worker.handle(Envelope(
        type="report.requested", payload={"workflow_id": "w-1"}, producer="gateway",
    ))

    assert len(isolated.store.recent(10)) == 2


def test_the_worker_dead_letters_a_request_with_no_workflow(isolated):
    from services.reporting import worker

    worker._seen.clear()
    with pytest.raises(ValueError):
        worker.handle(Envelope(
            type="report.requested", payload={}, producer="gateway",
        ))


def test_a_completion_with_no_workflow_id_is_ignored_not_dead_lettered(isolated):
    """Not our message to reject: some other producer's malformed event should
    not stop this queue."""
    from services.reporting import worker

    worker._seen.clear()
    worker.handle(Envelope(
        type="workflow.completed", payload={}, producer="orchestrator",
    ))
    assert isolated.store.recent(10) == []


def test_the_worker_listens_for_both_triggers():
    from services.reporting import worker

    assert set(worker.ROUTING_KEYS) == {"report.requested", "workflow.completed"}


# ------------------------------------------------------------------ the fetch


def test_the_whole_workflow_is_fetched_not_just_its_report(monkeypatch):
    """A document needs the status, the inputs and the steps as much as the
    findings, and those sit at the top level."""
    monkeypatch.setattr(
        upstream.requests, "get",
        lambda *a, **kw: _Response(200, {"workflow_id": "w-1", "steps": [], "report": {}}),
    )
    assert REAL_FETCH("w-1")["workflow_id"] == "w-1"


def test_the_tenant_travels_with_the_fetch(monkeypatch):
    seen: dict = {}

    def capture(url, params=None, timeout=None):
        seen["params"] = params
        return _Response(200, {"workflow_id": "w-1"})

    monkeypatch.setattr(upstream.requests, "get", capture)
    REAL_FETCH("w-1", "tenant-a")
    assert seen["params"] == {"tenant_id": "tenant-a"}


def test_an_unknown_workflow_is_not_retryable(monkeypatch):
    monkeypatch.setattr(upstream.requests, "get", lambda *a, **kw: _Response(404))
    with pytest.raises(source.UnknownRecord):
        REAL_FETCH("nope")


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
