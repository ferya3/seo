"""Keyword service: API, event emission, worker idempotency, shared store.

Autocomplete sources are stubbed — these cover our wiring and contract
compliance, not Google's uptime.
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

from shared.contracts import validate_event  # noqa: E402
from shared.events import Envelope  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("KEYWORD_DATA_DIR", str(tmp_path / "keywords"))
    monkeypatch.setenv("EVENTS_ENABLED", "0")

    from seoagent.keywords import sources as source_module
    from seoagent.keywords.sources import Suggestion

    def fake(query, lang="fa", country="IR", timeout=12.0):
        base = query.strip()
        return [
            Suggestion(f"{base} قیمت", "google", 0),
            Suggestion(f"{base} آموزش", "google", 1),
            Suggestion(base, "google", 2),
        ]

    monkeypatch.setattr(source_module, "SOURCES", {"google": fake})
    monkeypatch.setattr(source_module, "trending_now", lambda *a, **k: ["ترند"])
    monkeypatch.setattr(source_module, "related_from_wikipedia", lambda *a, **k: [])

    from services.keyword import api

    api.store = api.ResearchStore(tmp_path / "keywords")
    api._publisher = None
    yield api


@pytest.fixture
def client(isolated):
    return TestClient(isolated.app)


@pytest.fixture
def captured(isolated, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        isolated, "_emit", lambda t, p, c, f: (validate_event(t, p), events.append((t, p)))[1]
    )
    return events


BODY = {"seed": "کفش", "sources": ["google"], "include_alphabet": False, "include_comparisons": False}


# --------------------------------------------------------------------- api


def test_healthz(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["service"] == "keyword-service"


def test_research_runs_end_to_end_and_is_readable(client):
    response = client.post("/v1/research", json=BODY)
    assert response.status_code == 202
    research_id = response.json()["research_id"]

    result = client.get(f"/v1/research/{research_id}").json()
    assert result["status"] == "completed"
    assert result["total"] > 0
    assert result["report"]["keywords"]
    assert result["report"]["clusters"]

    listed = client.get("/v1/research").json()
    assert any(item["research_id"] == research_id for item in listed)


def test_unknown_research_is_404(client):
    assert client.get(f"/v1/research/{uuid.uuid4()}").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {},                                    # no seed
        {"seed": ""},                          # blank seed
        {"seed": "x", "max_keywords": 1},      # below the floor
        {"seed": "x", "max_keywords": 99999},  # above the ceiling
    ],
)
def test_bad_input_is_rejected(client, body):
    assert client.post("/v1/research", json=body).status_code == 422


def test_whitespace_only_seed_is_rejected(client):
    assert client.post("/v1/research", json={"seed": "   "}).status_code == 422


# ------------------------------------------------------------------ events


def test_completion_emits_a_contract_valid_event(isolated, captured):
    research_id = str(uuid.uuid4())
    isolated.store.create(research_id, "کفش")
    isolated.run_research(research_id, BODY)

    payload = next(p for t, p in captured if t == "keyword.researched")
    assert payload["research_id"] == research_id
    assert payload["seed"] == "کفش"
    assert payload["status"] == "completed"
    assert payload["total"] > 0
    assert payload["result_url"] == f"/v1/research/{research_id}"
    validate_event("keyword.researched", payload)


def test_the_event_preview_is_capped(isolated, captured):
    """The full set stays behind result_url — a message must not carry it."""
    research_id = str(uuid.uuid4())
    isolated.store.create(research_id, "کفش")
    isolated.run_research(research_id, {**BODY, "max_keywords": 500})

    payload = next(p for t, p in captured if t == "keyword.researched")
    assert len(payload["top_keywords"]) <= 25
    assert payload["total"] >= len(payload["top_keywords"])


def test_a_failed_run_still_reports_completion(isolated, captured, monkeypatch):
    def explode(config, progress=None):
        raise RuntimeError("sources unreachable")

    monkeypatch.setattr("services.keyword.api.research", explode)

    research_id = str(uuid.uuid4())
    isolated.store.create(research_id, "کفش")
    record = isolated.run_research(research_id, BODY)

    assert record.status == "failed"
    assert "sources unreachable" in record.error
    payload = next(p for t, p in captured if t == "keyword.researched")
    assert payload["status"] == "failed"
    validate_event("keyword.researched", payload)


def test_a_broken_broker_does_not_fail_a_good_run(isolated, monkeypatch):
    class DeadPublisher:
        def emit(self, *a, **k):
            raise ConnectionError("broker down")

        def publish(self, *a, **k):
            raise ConnectionError("broker down")

    monkeypatch.setattr(isolated, "publisher", lambda: DeadPublisher())

    research_id = str(uuid.uuid4())
    isolated.store.create(research_id, "کفش")
    assert isolated.run_research(research_id, BODY).status == "completed"


def test_an_invalid_payload_is_never_published(isolated, monkeypatch):
    published: list[str] = []

    class Recorder:
        def emit(self, event_type, payload, **kwargs):
            published.append(event_type)

        def publish(self, envelope):
            published.append(envelope.type)

    monkeypatch.setattr(isolated, "publisher", lambda: Recorder())
    isolated._emit("keyword.researched", {"research_id": "incomplete"}, None, {})
    assert published == []


# ------------------------------------------------------------------ worker


def test_worker_runs_a_requested_research(isolated, monkeypatch):
    from services.keyword import worker

    calls: list[str] = []
    monkeypatch.setattr(worker.api, "run_research", lambda rid, payload, **kw: calls.append(rid))
    worker._seen.clear()

    research_id = str(uuid.uuid4())
    worker.handle(
        Envelope(
            type="keyword.research_requested",
            payload={"research_id": research_id, "seed": "کفش"},
            producer="gateway",
        )
    )
    assert calls == [research_id]


def test_worker_ignores_a_redelivered_event(isolated, monkeypatch):
    from services.keyword import worker

    calls: list[str] = []
    monkeypatch.setattr(worker.api, "run_research", lambda rid, payload, **kw: calls.append(rid))
    worker._seen.clear()

    envelope = Envelope(
        type="keyword.research_requested",
        payload={"research_id": str(uuid.uuid4()), "seed": "کفش"},
        producer="gateway",
    )
    worker.handle(envelope)
    worker.handle(envelope)
    assert len(calls) == 1


def test_worker_skips_research_that_already_ran(isolated, monkeypatch):
    from services.keyword import worker

    calls: list[str] = []
    monkeypatch.setattr(worker.api, "run_research", lambda rid, payload, **kw: calls.append(rid))
    worker._seen.clear()

    research_id = str(uuid.uuid4())
    isolated.store.create(research_id, "کفش")
    isolated.store.complete(research_id, {"total": 3})

    worker.handle(
        Envelope(
            type="keyword.research_requested",
            payload={"research_id": research_id, "seed": "کفش"},
            producer="gateway",
        )
    )
    assert calls == []


def test_worker_dead_letters_a_malformed_request(isolated):
    from services.keyword import worker

    worker._seen.clear()
    with pytest.raises(ValueError):
        worker.handle(
            Envelope(type="keyword.research_requested", payload={"lang": "fa"}, producer="gateway")
        )


# ------------------------------------------------------------ shared store


def test_results_survive_a_restart(isolated, tmp_path):
    from services.keyword.store import ResearchStore

    research_id = str(uuid.uuid4())
    isolated.store.create(research_id, "کفش")
    isolated.store.complete(research_id, {"total": 42, "clusters": [{"label": "قیمت"}]})

    fresh = ResearchStore(tmp_path / "keywords")
    restored = fresh.get(research_id)
    assert restored is not None
    assert restored.status == "completed"
    assert restored.result["total"] == 42
    assert restored.summary()["cluster_count"] == 1


def test_the_two_services_do_not_share_a_namespace(tmp_path):
    """Same store implementation, separate directories — a crawl id and a
    research id must never collide."""
    from services.crawl.store import CrawlStore
    from services.keyword.store import ResearchStore

    crawls = CrawlStore(tmp_path / "crawls")
    research = ResearchStore(tmp_path / "keywords")

    shared_id = str(uuid.uuid4())
    crawls.create(shared_id, "https://example.com")
    assert research.get(shared_id) is None


def test_generic_store_round_trips_an_unknown_field(tmp_path):
    """An older file missing a field, or carrying an extra one, still loads —
    a stored report is worth more than schema purity."""
    import json

    from services.keyword.store import ResearchStore

    directory = tmp_path / "keywords"
    directory.mkdir(exist_ok=True)
    (directory / "abc.json").write_text(
        json.dumps({"research_id": "abc", "seed": "کفش", "status": "completed",
                    "report": {"total": 7}, "unexpected": "ignored"}),
        encoding="utf-8",
    )
    restored = ResearchStore(directory).get("abc")
    assert restored is not None
    assert restored.result["total"] == 7
