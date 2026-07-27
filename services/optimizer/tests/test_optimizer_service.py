"""The optimizer service: job lifecycle, events, worker, and the optional study.

Upstream services and the model are both stubbed. The two things worth
watching here are that a missing keyword study degrades the plan instead of
failing it, and that `written_by` tells the truth about whether any copy was
actually written.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from services.optimizer import source  # noqa: E402
from shared import llm, upstream  # noqa: E402
from shared.contracts import validate_event  # noqa: E402
from shared.events import Envelope  # noqa: E402

REAL_RESEARCH_FETCH = source.fetch_research

CRAWL = {
    "start_url": "https://site.test/",
    "pages": [
        {"url": "https://site.test/run", "title": "کفش دویدن", "status": 200,
         "indexable": True, "words": 800, "description": "", "headings": []},
    ],
}
RESEARCH = {"keywords": [{"keyword": "کفش دویدن", "demand": 400, "intent": "commercial"}]}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIMIZER_DATA_DIR", str(tmp_path / "optimizer"))
    monkeypatch.setenv("EVENTS_ENABLED", "0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from services.optimizer import api
    from services.optimizer.store import PlanStore

    api.store = PlanStore(tmp_path / "optimizer")
    api._publisher = None
    monkeypatch.setattr(api.source, "fetch_crawl", lambda crawl_id, tenant_id=None: CRAWL)
    monkeypatch.setattr(api.source, "fetch_research", lambda rid, tenant_id=None: RESEARCH)
    yield api


@pytest.fixture
def client(isolated):
    return TestClient(isolated.app)


# ------------------------------------------------------------------- the api


def test_a_plan_runs_end_to_end_and_is_readable(client):
    accepted = client.post(
        "/v1/optimizer-plans", json={"crawl_id": "crawl-1", "research_id": "research-1"}
    )
    assert accepted.status_code == 202
    plan_id = accepted.json()["plan_id"]

    body = client.get(f"/v1/optimizer-plans/{plan_id}").json()
    assert body["status"] == "completed"
    assert body["report"]["plans"][0]["url"] == "https://site.test/run"
    # No API key in this environment, so the plan is a brief and says so.
    assert body["report"]["written_by"] == "rules"


def test_the_page_count_is_capped(client):
    assert client.post(
        "/v1/optimizer-plans", json={"crawl_id": "c", "pages": 500}
    ).status_code == 422


def test_an_unknown_plan_is_404(client):
    assert client.get(f"/v1/optimizer-plans/{uuid.uuid4()}").status_code == 404


def test_a_plan_is_only_readable_by_the_tenant_that_owns_it(isolated, client):
    isolated.store.create("p-1", "crawl-1", tenant_id="tenant-a")

    assert client.get(
        "/v1/optimizer-plans/p-1", params={"tenant_id": "tenant-a"}
    ).status_code == 200
    assert client.get(
        "/v1/optimizer-plans/p-1", params={"tenant_id": "tenant-b"}
    ).status_code == 404


# ----------------------------------------------------------------- the events


def test_completion_emits_a_contract_valid_event(isolated, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))

    plan_id = str(uuid.uuid4())
    isolated.store.create(plan_id, "crawl-1")
    isolated.run_plan(plan_id, "crawl-1", "research-1")

    event_type, payload = events[0]
    assert event_type == "optimizer.planned"
    validate_event("optimizer.planned", payload)
    assert payload["written_by"] == "rules"
    assert payload["pages"][0]["url"] == "https://site.test/run"


def test_the_event_carries_counts_not_the_copy(isolated, monkeypatch):
    """The proposed text is the report. Putting it on the bus would push every
    consumer's message size up for data none of them read."""
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))

    plan_id = str(uuid.uuid4())
    isolated.store.create(plan_id, "crawl-1")
    isolated.run_plan(plan_id, "crawl-1", "research-1")

    _, payload = events[0]
    assert "plans" not in payload
    assert "candidate" not in str(payload)


def test_written_by_says_ai_only_when_a_rewrite_was_accepted(isolated, monkeypatch):
    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "ask", lambda *a, **kw: {"rewrites": [{
        "url": "https://site.test/run", "field": "description",
        "text": "کفش دویدن سبک و حرفه‌ای برای دوی استقامت، با زیره‌ی نرم و بدنه‌ی "
                "تنفس‌پذیر و ارسال سریع به سراسر کشور.",
        "why_fa": "توضیح ندارد.",
    }]})

    plan_id = str(uuid.uuid4())
    isolated.store.create(plan_id, "crawl-1")
    record = isolated.run_plan(plan_id, "crawl-1", "research-1")

    assert record.result["written_by"] == "ai"


def test_a_failed_fetch_still_reports(isolated, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))
    monkeypatch.setattr(isolated.source, "fetch_crawl", _raise(
        source.ReportUnavailable("crawl service unreachable")))

    plan_id = str(uuid.uuid4())
    isolated.store.create(plan_id, "crawl-1")
    record = isolated.run_plan(plan_id, "crawl-1", "research-1")

    assert record.status == "failed"
    _, payload = events[0]
    validate_event("optimizer.planned", payload)
    assert "unreachable" in payload["error"]


def test_an_invalid_payload_is_never_published(isolated, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))
    monkeypatch.setattr(isolated, "planned_payload", lambda *a: {"nonsense": True})

    plan_id = str(uuid.uuid4())
    isolated.store.create(plan_id, "crawl-1")
    isolated.run_plan(plan_id, "crawl-1", "research-1")
    assert events == []


# ----------------------------------------------------------------- the worker


def test_the_worker_runs_a_requested_plan(isolated):
    from services.optimizer import worker

    worker._seen.clear()
    plan_id = str(uuid.uuid4())
    worker.handle(Envelope(
        type="optimizer.plan_requested",
        payload={"plan_id": plan_id, "crawl_id": "crawl-1", "research_id": "research-1"},
        producer="orchestrator",
        tenant_id="tenant-a",
    ))

    record = isolated.store.get(plan_id)
    assert record.status == "completed"
    assert record.tenant_id == "tenant-a"


def test_the_worker_accepts_a_request_with_no_keyword_study(isolated):
    """Research comes back empty on some networks. Length and structure fixes
    do not need it, and refusing the whole plan would throw those away."""
    from services.optimizer import worker

    worker._seen.clear()
    plan_id = str(uuid.uuid4())
    worker.handle(Envelope(
        type="optimizer.plan_requested",
        payload={"plan_id": plan_id, "crawl_id": "crawl-1"},
        producer="orchestrator",
    ))

    assert isolated.store.get(plan_id).status == "completed"


def test_the_worker_dead_letters_a_request_with_no_crawl(isolated):
    from services.optimizer import worker

    worker._seen.clear()
    with pytest.raises(ValueError):
        worker.handle(Envelope(
            type="optimizer.plan_requested", payload={"research_id": "r"},
            producer="orchestrator",
        ))


# ------------------------------------------------------------------ the fetch


def test_a_missing_keyword_study_is_not_fatal(monkeypatch):
    monkeypatch.setattr(upstream.requests, "get", lambda *a, **kw: _Response(404))
    assert REAL_RESEARCH_FETCH("nope") == {}


def test_no_research_id_at_all_is_not_a_request(monkeypatch):
    monkeypatch.setattr(upstream.requests, "get", _raise(
        AssertionError("must not call the keyword service")))
    assert REAL_RESEARCH_FETCH(None) == {}


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
