"""The competitor service: job lifecycle, events, worker, and the fetches.

The crawl service is stubbed — it is ours and tested elsewhere, and a test
should not need another process running. What is exercised for real is every
decision this service makes once it has the crawls, and what happens when one
of them cannot be had.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from services.competitor import source  # noqa: E402
from shared.contracts import validate_event  # noqa: E402
from shared.events import Envelope  # noqa: E402

# Grabbed before the autouse fixture replaces it: `api.source` is this same
# module, so stubbing the fetch for the service tests stubs it everywhere.
REAL_FETCH = source.fetch_crawl

GOOD_TITLE = "کفش ورزشی مردانه سبک و راحت"


def crawl(host, count=6, title=GOOD_TITLE, description="توضیح کامل این صفحه برای نتایج جست‌وجو",
          words=800, heading="کفش ورزشی"):
    return {
        "start_url": f"https://{host}/",
        "pages": [
            {"url": f"https://{host}/{i}", "title": title, "description": description,
             "status": 200, "indexable": True, "words": words,
             "headings": [{"level": 1, "text": heading}]}
            for i in range(count)
        ],
    }


MINE = crawl("mine.test", title="کفش", description=None, words=150, heading="کفش")
RIVAL_A = crawl("a.test", heading="کفش ماراتن حرفه‌ای")
RIVAL_B = crawl("b.test", heading="کفش ماراتن سبک")

CRAWLS = {"mine": MINE, "rival-a": RIVAL_A, "rival-b": RIVAL_B}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITOR_DATA_DIR", str(tmp_path / "competitor"))
    monkeypatch.setenv("EVENTS_ENABLED", "0")

    from services.competitor import api
    from services.competitor.store import ComparisonStore

    api.store = ComparisonStore(tmp_path / "competitor")
    api._publisher = None

    def fetch(crawl_id, tenant_id=None):
        if crawl_id not in CRAWLS:
            raise source.UnknownRecord(f"{crawl_id} not found")
        return CRAWLS[crawl_id]

    monkeypatch.setattr(api.source, "fetch_crawl", fetch)
    yield api


@pytest.fixture
def client(isolated):
    return TestClient(isolated.app)


def _raise(exc):
    def fail(*args, **kwargs):
        raise exc
    return fail


# ------------------------------------------------------------------- the api


def test_a_comparison_runs_end_to_end_and_is_readable(client):
    accepted = client.post("/v1/comparisons", json={
        "crawl_id": "mine", "competitor_crawl_ids": ["rival-a", "rival-b"],
    })
    assert accepted.status_code == 202
    comparison_id = accepted.json()["comparison_id"]

    body = client.get(f"/v1/comparisons/{comparison_id}").json()
    assert body["status"] == "completed"
    report = body["report"]
    assert report["compared_against"] == 2
    assert "described" in report["behind_on"]
    assert "ماراتن" in [row["term"] for row in report["missing_themes"]]


def test_at_least_one_competitor_is_required(client):
    assert client.post("/v1/comparisons", json={
        "crawl_id": "mine", "competitor_crawl_ids": [],
    }).status_code == 422
    assert client.post("/v1/comparisons", json={"crawl_id": "mine"}).status_code == 422


def test_a_site_cannot_be_its_own_competitor(client):
    # The report would say the site is exactly average, which looks like a
    # result rather than the mistake it is.
    refused = client.post("/v1/comparisons", json={
        "crawl_id": "mine", "competitor_crawl_ids": ["rival-a", "mine"],
    })
    assert refused.status_code == 422
    assert "own competitor" in refused.json()["detail"]


def test_the_same_competitor_twice_counts_once(isolated):
    comparison_id = str(uuid.uuid4())
    isolated.store.create(comparison_id, "mine")
    record = isolated.run_comparison(comparison_id, "mine", ["rival-a", "rival-a"])

    # Otherwise one site would satisfy "at least two competitors use this term"
    # on its own, and its house style would read as a gap in yours.
    assert record.report["compared_against"] == 1
    assert record.report["missing_themes"] == []


def test_an_unknown_comparison_is_404(client):
    assert client.get(f"/v1/comparisons/{uuid.uuid4()}").status_code == 404


def test_a_comparison_is_only_readable_by_the_tenant_that_owns_it(isolated, client):
    isolated.store.create("c-1", "mine", tenant_id="tenant-a")

    assert client.get("/v1/comparisons/c-1", params={"tenant_id": "tenant-a"}).status_code == 200
    assert client.get("/v1/comparisons/c-1", params={"tenant_id": "tenant-b"}).status_code == 404


def test_too_many_competitors_is_refused_rather_than_truncated(client):
    # Silently dropping the last three would answer a different question than
    # the one asked.
    refused = client.post("/v1/comparisons", json={
        "crawl_id": "mine", "competitor_crawl_ids": [f"rival-{i}" for i in range(20)],
    })
    assert refused.status_code == 422


# --------------------------------------------------------- missing competitors


def test_one_missing_competitor_does_not_lose_the_whole_comparison(isolated):
    comparison_id = str(uuid.uuid4())
    isolated.store.create(comparison_id, "mine")
    record = isolated.run_comparison(comparison_id, "mine", ["rival-a", "rival-b", "gone"])

    assert record.status == "completed"
    assert record.report["compared_against"] == 2
    # Named, so the report is not quietly thinner than it looks.
    assert record.report["unavailable"][0]["crawl_id"] == "gone"


def test_losing_every_competitor_is_a_failure_not_an_empty_report(isolated):
    comparison_id = str(uuid.uuid4())
    isolated.store.create(comparison_id, "mine")
    record = isolated.run_comparison(comparison_id, "mine", ["gone"])

    assert record.status == "failed"
    assert "at least one competitor" in record.error


def test_your_own_crawl_missing_fails_the_job(isolated, monkeypatch):
    monkeypatch.setattr(isolated.source, "fetch_crawl",
                        _raise(source.ReportUnavailable("still running")))

    comparison_id = str(uuid.uuid4())
    isolated.store.create(comparison_id, "mine")
    record = isolated.run_comparison(comparison_id, "mine", ["rival-a"])

    assert record.status == "failed"
    assert "still running" in record.error


# ----------------------------------------------------------------- the events


def test_completion_emits_a_contract_valid_event(isolated, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))

    comparison_id = str(uuid.uuid4())
    isolated.store.create(comparison_id, "mine")
    isolated.run_comparison(comparison_id, "mine", ["rival-a", "rival-b"])

    event_type, payload = events[0]
    assert event_type == "competitor.compared"
    validate_event("competitor.compared", payload)
    assert payload["compared_against"] == 2
    assert "ماراتن" in payload["top_missing_themes"]


def test_the_event_carries_the_headline_not_every_profile(isolated, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))

    comparison_id = str(uuid.uuid4())
    isolated.store.create(comparison_id, "mine")
    isolated.run_comparison(comparison_id, "mine", ["rival-a", "rival-b"])

    _, payload = events[0]
    assert "competitors" not in payload      # the profiles stay behind the url
    assert "missing_themes" not in payload   # the count and the top ten travel


def test_a_failure_is_published_too(isolated, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))
    monkeypatch.setattr(isolated.source, "fetch_crawl",
                        _raise(source.ReportUnavailable("still running")))

    comparison_id = str(uuid.uuid4())
    isolated.store.create(comparison_id, "mine")
    isolated.run_comparison(comparison_id, "mine", ["rival-a"])

    _, payload = events[0]
    validate_event("competitor.compared", payload)
    assert payload["status"] == "failed"


def test_an_invalid_payload_is_never_published(isolated, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))
    monkeypatch.setattr(isolated, "compared_payload", lambda *a: {"nonsense": True})

    comparison_id = str(uuid.uuid4())
    isolated.store.create(comparison_id, "mine")
    isolated.run_comparison(comparison_id, "mine", ["rival-a"])
    assert events == []


# ----------------------------------------------------------------- the worker


def test_the_worker_runs_a_requested_comparison(isolated):
    from services.competitor import worker

    worker._seen.clear()
    comparison_id = str(uuid.uuid4())
    worker.handle(Envelope(
        type="competitor.comparison_requested",
        payload={"comparison_id": comparison_id, "crawl_id": "mine",
                 "competitor_crawl_ids": ["rival-a", "rival-b"]},
        producer="orchestrator",
        tenant_id="tenant-a",
    ))

    record = isolated.store.get(comparison_id)
    assert record.status == "completed"
    # From the envelope, not the payload.
    assert record.tenant_id == "tenant-a"


def test_the_worker_ignores_a_redelivered_event(isolated, monkeypatch):
    from services.competitor import worker

    worker._seen.clear()
    calls: list[str] = []
    monkeypatch.setattr(isolated, "run_comparison", lambda *a, **kw: calls.append(a[0]))

    envelope = Envelope(
        type="competitor.comparison_requested",
        payload={"comparison_id": str(uuid.uuid4()), "crawl_id": "mine",
                 "competitor_crawl_ids": ["rival-a"]},
        producer="orchestrator",
    )
    worker.handle(envelope)
    worker.handle(envelope)
    assert len(calls) == 1


def test_the_worker_dead_letters_a_request_with_no_competitors(isolated):
    """An empty list is not something a redelivery fixes."""
    from services.competitor import worker

    worker._seen.clear()
    with pytest.raises(ValueError):
        worker.handle(Envelope(
            type="competitor.comparison_requested",
            payload={"crawl_id": "mine", "competitor_crawl_ids": []},
            producer="orchestrator",
        ))


def test_the_worker_does_not_rerun_a_finished_comparison(isolated, monkeypatch):
    from services.competitor import worker

    worker._seen.clear()
    comparison_id = str(uuid.uuid4())
    isolated.store.create(comparison_id, "mine")
    isolated.store.complete(comparison_id, {"compared_against": 1})

    calls: list[str] = []
    monkeypatch.setattr(isolated, "run_comparison", lambda *a, **kw: calls.append(a[0]))
    worker.handle(Envelope(
        type="competitor.comparison_requested",
        payload={"comparison_id": comparison_id, "crawl_id": "mine",
                 "competitor_crawl_ids": ["rival-a"]},
        producer="orchestrator",
    ))
    assert calls == []
