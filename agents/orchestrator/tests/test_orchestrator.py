"""The orchestrator: planning, dispatch, advancing, and the awkward cases.

Runs against a real Postgres. Two of the properties under test — that a
redelivered event advances the workflow once, and that two events landing
together cannot both dispatch the next step — are transaction behaviour, which
cannot be demonstrated with a fake store.

    TEST_DATABASE_URL=postgresql://seo@127.0.0.1/seo pytest agents
"""

from __future__ import annotations

import os
import sys
import threading
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

psycopg = pytest.importorskip("psycopg")

DSN = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not set")

from agents.orchestrator import engine, planner  # noqa: E402
from agents.orchestrator.store import WorkflowStore  # noqa: E402
from shared.contracts import validate_event  # noqa: E402

AUDIT = {"start_url": "https://example.com", "seed": "کفش"}


@pytest.fixture
def store():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("TRUNCATE workflows, workflow_steps, outbox RESTART IDENTITY CASCADE")
    s = WorkflowStore(DSN)
    yield s
    s.close()


@pytest.fixture
def conn():
    with psycopg.connect(DSN, autocommit=True) as connection:
        yield connection


def crawl_done(job_id: str, status: str = "completed", error: str | None = None) -> dict:
    return {
        "crawl_id": job_id,
        "start_url": "https://example.com",
        "status": status,
        "error": error,
        "overall_score": 73,
        "grade": "B",
        "stats": {"total_issues": 9, "pages_crawled": 12, "indexable_pages": 10, "errors": 0,
                  "redirects": 1, "avg_words": 400, "avg_load_ms": 250, "critical_count": 0,
                  "high_count": 2},
        "category_scores": [],
        "result_url": f"/v1/crawls/{job_id}",
    }


def research_done(job_id: str, status: str = "completed", error: str | None = None) -> dict:
    return {
        "research_id": job_id,
        "seed": "کفش",
        "lang": "fa",
        "country": "IR",
        "status": status,
        "error": error,
        "total": 128,
        "cluster_count": 4,
        "top_keywords": [
            {"keyword": "کفش ورزشی", "demand": 80, "opportunity": 70, "intent": "commercial"},
            {"keyword": "کفش مردانه", "demand": 60, "opportunity": 65, "intent": "commercial"},
        ],
        "result_url": f"/v1/research/{job_id}",
    }


def serp_done(job_id: str, status: str = "completed", error: str | None = None) -> dict:
    return {
        "check_id": job_id,
        "target_domain": "example.com",
        "status": status,
        "error": error,
        "keywords_checked": 3,
        "keywords_ranked": 2,
        "average_position": 3.5,
        "best": {"keyword": "کفش پیاده‌روی", "position": 2, "url": "https://example.com/w"},
        "top_competitors": [{"domain": "rival.com", "outranks_on": 2}],
        "opportunities": [{"keyword": "کفش", "position": None, "opportunity": 100}],
        "result_url": f"/v1/checks/{job_id}",
    }


def started(store, inputs=None) -> str:
    workflow_id = str(uuid.uuid4())
    engine.start(store, workflow_id, "site_audit", inputs or dict(AUDIT))
    return workflow_id


# --------------------------------------------------------------------- planner


def test_a_site_audit_crawls_then_researches_then_checks_rankings():
    steps = planner.plan("site_audit", AUDIT)
    assert [(s.position, s.kind) for s in steps] == [
        (1, "crawl"), (2, "keyword_research"), (3, "serp_check")
    ]


def test_an_unknown_goal_is_refused():
    with pytest.raises(planner.UnknownGoal):
        planner.plan("take-over-the-world", AUDIT)


def test_a_site_audit_needs_a_url():
    with pytest.raises(ValueError):
        planner.plan("site_audit", {"seed": "کفش"})


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com", "example"),
        ("https://www.example.com/path", "example"),
        ("https://digi-kala.co.uk", "digi kala"),
        ("http://shop_site.ir", "shop site"),
    ],
)
def test_a_seed_can_be_derived_from_the_domain(url, expected):
    assert planner.seed_from_domain(url) == expected


def test_an_explicit_seed_always_wins():
    assert planner.resolve_seed({"seed": "کفش ورزشی"}, {"start_url": "https://example.com"}) \
        == "کفش ورزشی"


# ---------------------------------------------------------------- dispatching


def test_starting_dispatches_only_the_first_step(store, conn):
    workflow_id = started(store)
    workflow = store.get(workflow_id)

    assert workflow.status == "running"
    assert [s.status for s in workflow.steps] == ["dispatched", "pending", "pending"]

    # The second step must not have been asked for yet.
    types = [r[0] for r in conn.execute("SELECT event_type FROM outbox ORDER BY id").fetchall()]
    assert types == ["crawl.requested"]


def test_the_dispatched_event_carries_the_pre_allocated_id(store, conn):
    workflow_id = started(store)
    step = store.get(workflow_id).step_at(1)

    payload = conn.execute("SELECT payload FROM outbox WHERE event_type='crawl.requested'") \
        .fetchone()[0]
    # This is what lets crawl.completed find its way back to the step.
    assert payload["crawl_id"] == step.job_id
    assert payload["start_url"] == "https://example.com"
    validate_event("crawl.requested", payload)


def test_the_workflow_id_is_the_correlation_id(store, conn):
    workflow_id = started(store)
    correlation = conn.execute("SELECT correlation_id FROM outbox").fetchone()[0]
    assert correlation == workflow_id


def test_finishing_a_step_dispatches_the_next(store, conn):
    workflow_id = started(store)
    crawl_step = store.get(workflow_id).step_at(1)

    engine.on_completion(store, "crawl.completed", crawl_done(crawl_step.job_id))

    workflow = store.get(workflow_id)
    assert [s.status for s in workflow.steps] == ["completed", "dispatched", "pending"]
    types = [r[0] for r in conn.execute("SELECT event_type FROM outbox ORDER BY id").fetchall()]
    assert types == ["crawl.requested", "keyword.research_requested"]


def test_the_research_seed_is_resolved_after_the_crawl(store, conn):
    """The reason the steps are sequential: without an explicit seed, what to
    research is only knowable once the crawl has reported."""
    workflow_id = started(store, {"start_url": "https://digi-kala.com"})
    crawl_step = store.get(workflow_id).step_at(1)

    result = crawl_done(crawl_step.job_id)
    result["start_url"] = "https://digi-kala.com"
    engine.on_completion(store, "crawl.completed", result)

    payload = conn.execute(
        "SELECT payload FROM outbox WHERE event_type='keyword.research_requested'"
    ).fetchone()[0]
    assert payload["seed"] == "digi kala"
    validate_event("keyword.research_requested", payload)


# ------------------------------------------------------------------ finishing


def test_the_last_step_completes_the_workflow(store):
    workflow_id = started(store)
    steps = store.get(workflow_id).steps

    engine.on_completion(store, "crawl.completed", crawl_done(steps[0].job_id))
    engine.on_completion(store, "keyword.researched", research_done(steps[1].job_id))
    engine.on_completion(store, "serp.checked", serp_done(steps[2].job_id))

    workflow = store.get(workflow_id)
    assert workflow.status == "completed"
    assert workflow.report["headline"] == {
        "overall_score": 73, "total_issues": 9, "keywords_found": 128,
        "keywords_ranked": 2, "average_position": 3.5,
    }


def test_completion_emits_a_contract_valid_event(store, conn):
    workflow_id = started(store)
    steps = store.get(workflow_id).steps
    engine.on_completion(store, "crawl.completed", crawl_done(steps[0].job_id))
    engine.on_completion(store, "keyword.researched", research_done(steps[1].job_id))
    engine.on_completion(store, "serp.checked", serp_done(steps[2].job_id))

    payload = conn.execute(
        "SELECT payload FROM outbox WHERE event_type='workflow.completed'"
    ).fetchone()[0]
    validate_event("workflow.completed", payload)
    assert payload["status"] == "completed"
    assert [s["status"] for s in payload["steps"]] == ["completed"] * 3


def test_the_report_links_to_results_rather_than_copying_them(store):
    """A crawl report is megabytes. Duplicating it here would create a second
    copy with no way to stay in step with the first."""
    workflow_id = started(store)
    steps = store.get(workflow_id).steps
    engine.on_completion(store, "crawl.completed", crawl_done(steps[0].job_id))
    engine.on_completion(store, "keyword.researched", research_done(steps[1].job_id))
    engine.on_completion(store, "serp.checked", serp_done(steps[2].job_id))

    report = store.get(workflow_id).report
    assert report["crawl"]["result_url"] == f"/v1/crawls/{steps[0].job_id}"
    assert "pages" not in report["crawl"]


# --------------------------------------------------------------------- failure


def test_a_failed_step_fails_the_workflow(store, conn):
    workflow_id = started(store)
    steps = store.get(workflow_id).steps

    engine.on_completion(
        store, "crawl.completed", crawl_done(steps[0].job_id, "failed", "Timeout: unreachable")
    )

    workflow = store.get(workflow_id)
    assert workflow.status == "failed"
    assert "Timeout: unreachable" in workflow.error
    # And the second step was never asked for.
    types = [r[0] for r in conn.execute("SELECT event_type FROM outbox ORDER BY id").fetchall()]
    assert "keyword.research_requested" not in types


def test_a_failed_workflow_still_reports(store, conn):
    workflow_id = started(store)
    steps = store.get(workflow_id).steps
    engine.on_completion(store, "crawl.completed", crawl_done(steps[0].job_id, "failed", "boom"))

    payload = conn.execute(
        "SELECT payload FROM outbox WHERE event_type='workflow.completed'"
    ).fetchone()[0]
    validate_event("workflow.completed", payload)
    assert payload["status"] == "failed"


def test_an_undispatchable_step_fails_the_workflow(store):
    """No seed, and a start_url with no usable domain: the second step cannot
    be built, and the workflow has to say so rather than stall in 'running'."""
    workflow_id = str(uuid.uuid4())
    engine.start(store, workflow_id, "site_audit", {"start_url": "https://example.com"})
    step = store.get(workflow_id).step_at(1)

    result = crawl_done(step.job_id)
    result["start_url"] = ""
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("UPDATE workflows SET inputs = '{\"start_url\": \"\"}'::jsonb WHERE id = %s",
                  (workflow_id,))

    engine.on_completion(store, "crawl.completed", result)
    assert store.get(workflow_id).status == "failed"


# ------------------------------------------------- at-least-once and two hosts


def test_a_redelivered_event_advances_the_workflow_once(store, conn):
    """Delivery is at-least-once, so this event will arrive twice sooner or
    later. The step transition is dispatched -> completed, so the second
    arrival matches no row."""
    workflow_id = started(store)
    steps = store.get(workflow_id).steps
    payload = crawl_done(steps[0].job_id)

    engine.on_completion(store, "crawl.completed", payload)
    engine.on_completion(store, "crawl.completed", payload)

    types = [r[0] for r in conn.execute("SELECT event_type FROM outbox ORDER BY id").fetchall()]
    assert types.count("keyword.research_requested") == 1


def test_a_completion_for_a_job_no_workflow_asked_for_is_ignored(store):
    """Crawls started straight through the API emit the same event. Treating
    that as an error would dead-letter perfectly good messages."""
    assert engine.on_completion(store, "crawl.completed", crawl_done(str(uuid.uuid4()))) is None


def test_a_completion_with_no_job_id_is_dead_lettered(store):
    with pytest.raises(ValueError):
        engine.on_completion(store, "crawl.completed", {"status": "completed"})


def test_two_orchestrators_cannot_both_dispatch_the_next_step(store):
    """The row lock in advance(). Without it both instances read the same
    pending step and both send it, and the crawl runs twice."""
    workflow_id = started(store)
    steps = store.get(workflow_id).steps
    payload = crawl_done(steps[0].job_id)

    second = WorkflowStore(DSN)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def race(s):
        try:
            barrier.wait(timeout=5)
            engine.on_completion(s, "crawl.completed", payload)
        except BaseException as exc:                     # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=race, args=(s,)) for s in (store, second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    second.close()

    assert errors == []
    with psycopg.connect(DSN, autocommit=True) as c:
        dispatched = c.execute(
            "SELECT count(*) FROM outbox WHERE event_type='keyword.research_requested'"
        ).fetchone()[0]
    assert dispatched == 1


def test_advancing_a_finished_workflow_does_nothing(store, conn):
    workflow_id = started(store)
    steps = store.get(workflow_id).steps
    engine.on_completion(store, "crawl.completed", crawl_done(steps[0].job_id))
    engine.on_completion(store, "keyword.researched", research_done(steps[1].job_id))
    engine.on_completion(store, "serp.checked", serp_done(steps[2].job_id))

    before = conn.execute("SELECT count(*) FROM outbox").fetchone()[0]
    engine.advance(store, workflow_id)
    assert conn.execute("SELECT count(*) FROM outbox").fetchone()[0] == before


# ----------------------------------------------------------------- the worker


def test_the_worker_starts_a_requested_workflow(store, monkeypatch):
    from agents.orchestrator import api, worker
    from shared.events import Envelope

    monkeypatch.setattr(api, "_store", store)
    worker._seen.clear()

    workflow_id = str(uuid.uuid4())
    worker.handle(Envelope(
        type="workflow.requested",
        payload={"workflow_id": workflow_id, "goal": "site_audit", "inputs": AUDIT},
        producer="gateway",
    ))

    assert store.get(workflow_id).status == "running"


def test_the_worker_ignores_a_workflow_it_already_started(store, monkeypatch):
    from agents.orchestrator import api, worker
    from shared.events import Envelope

    monkeypatch.setattr(api, "_store", store)
    worker._seen.clear()

    workflow_id = str(uuid.uuid4())
    envelope = Envelope(
        type="workflow.requested",
        payload={"workflow_id": workflow_id, "goal": "site_audit", "inputs": AUDIT},
        producer="gateway",
    )
    worker.handle(envelope)
    worker._seen.clear()                       # force it past the cheap guard
    worker.handle(envelope)

    with psycopg.connect(DSN, autocommit=True) as c:
        assert c.execute("SELECT count(*) FROM workflow_steps WHERE workflow_id = %s",
                         (workflow_id,)).fetchone()[0] == 3


def test_the_worker_dead_letters_a_malformed_request(store, monkeypatch):
    from agents.orchestrator import api, worker
    from shared.events import Envelope

    monkeypatch.setattr(api, "_store", store)
    worker._seen.clear()

    with pytest.raises(ValueError):
        worker.handle(Envelope(
            type="workflow.requested", payload={"inputs": AUDIT}, producer="gateway"
        ))


def test_the_worker_advances_on_a_completion_event(store, monkeypatch):
    from agents.orchestrator import api, worker
    from shared.events import Envelope

    monkeypatch.setattr(api, "_store", store)
    worker._seen.clear()

    workflow_id = started(store)
    step = store.get(workflow_id).step_at(1)
    worker.handle(Envelope(
        type="crawl.completed", payload=crawl_done(step.job_id), producer="crawl-service"
    ))

    assert [s.status for s in store.get(workflow_id).steps] == [
        "completed", "dispatched", "pending"
    ]


# -------------------------------------------------------------------- the api


def test_the_api_rejects_an_unknown_goal(store, monkeypatch):
    from fastapi.testclient import TestClient

    from agents.orchestrator import api

    monkeypatch.setattr(api, "_store", store)
    client = TestClient(api.app)

    assert client.post("/v1/workflows", json={
        "goal": "conquer-mars", "inputs": {"start_url": "https://example.com"},
    }).status_code == 422


def test_the_api_runs_a_workflow_end_to_end(store, monkeypatch):
    from fastapi.testclient import TestClient

    from agents.orchestrator import api

    monkeypatch.setattr(api, "_store", store)
    client = TestClient(api.app)

    response = client.post("/v1/workflows", json={"goal": "site_audit", "inputs": AUDIT})
    assert response.status_code == 202
    workflow_id = response.json()["workflow_id"]

    body = client.get(f"/v1/workflows/{workflow_id}").json()
    assert body["status"] == "running"
    assert [s["kind"] for s in body["steps"]] == ["crawl", "keyword_research", "serp_check"]

    assert any(w["workflow_id"] == workflow_id for w in client.get("/v1/workflows").json())


def test_an_unknown_workflow_is_404(store, monkeypatch):
    from fastapi.testclient import TestClient

    from agents.orchestrator import api

    monkeypatch.setattr(api, "_store", store)
    assert TestClient(api.app).get(f"/v1/workflows/{uuid.uuid4()}").status_code == 404


# ------------------------------------------------------- the rank-check step


def test_what_to_rank_check_comes_from_what_research_found(store, conn):
    """The strongest data dependency in the plan: this step cannot be built
    until the step before it has reported, which is the whole reason the
    workflow is sequential."""
    workflow_id = started(store)
    steps = store.get(workflow_id).steps
    engine.on_completion(store, "crawl.completed", crawl_done(steps[0].job_id))
    engine.on_completion(store, "keyword.researched", research_done(steps[1].job_id))

    payload = conn.execute(
        "SELECT payload FROM outbox WHERE event_type='serp.check_requested'"
    ).fetchone()[0]
    assert payload["keywords"] == ["کفش ورزشی", "کفش مردانه"]
    assert payload["target_domain"] == "example.com"
    validate_event("serp.check_requested", payload)


def test_the_tracked_keyword_count_is_capped(store, conn):
    """Every tracked keyword is a live search request against a host that will
    start refusing, so this is a rate-limit decision as much as a report one."""
    workflow_id = started(store, {**AUDIT, "track_keywords": 3})
    steps = store.get(workflow_id).steps
    engine.on_completion(store, "crawl.completed", crawl_done(steps[0].job_id))

    result = research_done(steps[1].job_id)
    result["top_keywords"] = [
        {"keyword": f"کلمه {n}", "demand": 10, "opportunity": 10, "intent": "informational"}
        for n in range(20)
    ]
    engine.on_completion(store, "keyword.researched", result)

    payload = conn.execute(
        "SELECT payload FROM outbox WHERE event_type='serp.check_requested'"
    ).fetchone()[0]
    assert len(payload["keywords"]) == 3


def test_duplicate_keywords_are_not_checked_twice():
    research = {"top_keywords": [
        {"keyword": "کفش ورزشی"}, {"keyword": "کفش ورزشی "}, {"keyword": "کفش مردانه"},
    ]}
    assert planner.keywords_to_track(research, 10) == ["کفش ورزشی", "کفش مردانه"]


def test_research_finding_nothing_skips_the_check_rather_than_failing(store, conn):
    """A site with no keywords is a real answer about that site. Failing the
    workflow would report it as a system fault and throw away the crawl."""
    workflow_id = started(store)
    steps = store.get(workflow_id).steps
    engine.on_completion(store, "crawl.completed", crawl_done(steps[0].job_id))

    empty = research_done(steps[1].job_id)
    empty["top_keywords"] = []
    empty["total"] = 0
    engine.on_completion(store, "keyword.researched", empty)

    workflow = store.get(workflow_id)
    assert workflow.status == "completed"
    assert [s.status for s in workflow.steps] == ["completed", "completed", "skipped"]
    # The crawl's findings survive.
    assert workflow.report["headline"]["overall_score"] == 73

    types = [r[0] for r in conn.execute("SELECT event_type FROM outbox ORDER BY id").fetchall()]
    assert "serp.check_requested" not in types


def test_a_skipped_step_is_reported_as_skipped_not_failed(store, conn):
    workflow_id = started(store)
    steps = store.get(workflow_id).steps
    engine.on_completion(store, "crawl.completed", crawl_done(steps[0].job_id))
    empty = research_done(steps[1].job_id)
    empty["top_keywords"] = []
    engine.on_completion(store, "keyword.researched", empty)

    payload = conn.execute(
        "SELECT payload FROM outbox WHERE event_type='workflow.completed'"
    ).fetchone()[0]
    validate_event("workflow.completed", payload)
    assert payload["status"] == "completed"
    assert payload["steps"][2]["status"] == "skipped"


def test_a_failed_rank_check_fails_the_workflow(store):
    workflow_id = started(store)
    steps = store.get(workflow_id).steps
    engine.on_completion(store, "crawl.completed", crawl_done(steps[0].job_id))
    engine.on_completion(store, "keyword.researched", research_done(steps[1].job_id))
    engine.on_completion(
        store, "serp.checked", serp_done(steps[2].job_id, "failed", "ProviderError: blocked")
    )

    workflow = store.get(workflow_id)
    assert workflow.status == "failed"
    assert "blocked" in workflow.error


def test_the_rankings_reach_the_final_report(store):
    workflow_id = started(store)
    steps = store.get(workflow_id).steps
    engine.on_completion(store, "crawl.completed", crawl_done(steps[0].job_id))
    engine.on_completion(store, "keyword.researched", research_done(steps[1].job_id))
    engine.on_completion(store, "serp.checked", serp_done(steps[2].job_id))

    report = store.get(workflow_id).report
    assert report["rankings"]["average_position"] == 3.5
    assert report["rankings"]["top_competitors"][0]["domain"] == "rival.com"
    assert report["rankings"]["result_url"] == f"/v1/checks/{steps[2].job_id}"


def test_the_worker_listens_for_every_completion_the_engine_handles():
    """Found by running it, not by a test: serp.checked was added to the engine
    and not to the queue bindings, so the rank check ran and its completion
    event went nowhere — the workflow sat in 'running' forever."""
    from agents.orchestrator import worker

    missing = set(engine.COMPLETIONS) - set(worker.ROUTING_KEYS)
    assert missing == set()


def test_an_unknown_input_is_refused_rather_than_dropped(store, monkeypatch):
    """Found by running it: track_keywords was missing from the request model,
    so a caller could set it, watch ten keywords get checked instead of five,
    and have nothing to tell them why. Pydantic ignores unknown fields by
    default; here it must not."""
    from fastapi.testclient import TestClient

    from agents.orchestrator import api

    monkeypatch.setattr(api, "_store", store)
    client = TestClient(api.app)

    assert client.post("/v1/workflows", json={
        "goal": "site_audit",
        "inputs": {"start_url": "https://example.com", "trak_keywords": 5},
    }).status_code == 422


def test_the_tracked_count_reaches_the_plan(store, conn, monkeypatch):
    from fastapi.testclient import TestClient

    from agents.orchestrator import api

    monkeypatch.setattr(api, "_store", store)
    client = TestClient(api.app)

    workflow_id = client.post("/v1/workflows", json={
        "goal": "site_audit",
        "inputs": {"start_url": "https://example.com", "seed": "کفش", "track_keywords": 2},
    }).json()["workflow_id"]

    steps = store.get(workflow_id).steps
    engine.on_completion(store, "crawl.completed", crawl_done(steps[0].job_id))
    engine.on_completion(store, "keyword.researched", research_done(steps[1].job_id))

    payload = conn.execute(
        "SELECT payload FROM outbox WHERE event_type='serp.check_requested'"
    ).fetchone()[0]
    assert len(payload["keywords"]) == 2


# ---------------------------------------------------------------- the summary


def finished(store) -> str:
    """A workflow taken all the way to completed."""
    workflow_id = started(store)
    steps = store.get(workflow_id).steps
    engine.on_completion(store, "crawl.completed", crawl_done(steps[0].job_id))
    engine.on_completion(store, "keyword.researched", research_done(steps[1].job_id))
    engine.on_completion(store, "serp.checked", serp_done(steps[2].job_id))
    return workflow_id


def test_a_finished_report_always_carries_a_summary(store):
    """Written inside the finishing transaction, so it exists with or without
    an API key. The model-written one replaces it later, or never."""
    summary = store.get(finished(store)).report["summary"]
    assert summary["source"] == "rules"
    assert summary["text_fa"]
    assert summary["next_actions"]
    # The best ranking travels all the way from the SERP service into the
    # sentence. It used to stop at the event boundary, so this sentence was
    # written against a field that never arrived.
    assert "کفش پیاده‌روی" in summary["text_fa"]


def test_a_failed_workflow_is_summarised_too(store):
    workflow_id = started(store)
    step = store.get(workflow_id).step_at(1)
    engine.on_completion(store, "crawl.completed", crawl_done(step.job_id, "failed", "timeout"))

    summary = store.get(workflow_id).report["summary"]
    assert "timeout" in summary["text_fa"]


def test_the_report_lists_its_steps(store):
    steps = store.get(finished(store)).report["steps"]
    assert [s["kind"] for s in steps] == ["crawl", "keyword_research", "serp_check"]


def test_the_summary_is_written_off_the_lock_not_inside_it(store, monkeypatch):
    """The model call takes seconds. Doing it in `advance` would hold the
    workflow's row lock for that whole time, and every redelivered completion
    event for that workflow would queue up behind it."""
    from agents.orchestrator import summary as summary_module

    monkeypatch.setattr(summary_module.llm, "is_available", lambda: True)
    monkeypatch.setattr(summary_module.llm, "ask", _explode)

    workflow_id = finished(store)                      # would raise if it asked
    assert store.get(workflow_id).report["summary"]["source"] == "rules"


def _explode(*args, **kwargs):
    raise AssertionError("the state machine must not call the model")


def test_the_worker_upgrades_the_summary_when_the_model_answers(store, monkeypatch):
    from agents.orchestrator import api, worker
    from agents.orchestrator import summary as summary_module
    from shared.events import Envelope

    monkeypatch.setattr(api, "_store", store)
    monkeypatch.setattr(summary_module.llm, "is_available", lambda: True)
    monkeypatch.setattr(summary_module.llm, "ask", lambda *a, **kw: {
        "summary": "سایت پایه‌ی فنی خوبی دارد ولی روی عبارت‌های تجاری دیده نمی‌شود.",
        "next_actions": [{"action": "صفحه بساز", "why": "پوشش ندارد",
                          "effort": "متوسط", "impact": "زیاد"}],
        "watch_outs": ["حجم جستجوی واقعی در دسترس نیست."],
    })
    worker._seen.clear()

    workflow_id = finished(store)
    worker.handle(Envelope(
        type="workflow.completed",
        payload={"workflow_id": workflow_id},
        producer="orchestrator",
    ))

    summary = store.get(workflow_id).report["summary"]
    assert summary["source"] == "ai"
    assert "عبارت‌های تجاری" in summary["text_fa"]
    # The rest of the report is untouched: only the summary key is replaced.
    assert store.get(workflow_id).report["rankings"]["average_position"] == 3.5


def test_a_redelivered_completion_does_not_pay_for_a_second_model_call(store, monkeypatch):
    from agents.orchestrator import api, worker
    from agents.orchestrator import summary as summary_module
    from shared.events import Envelope

    monkeypatch.setattr(api, "_store", store)
    workflow_id = finished(store)
    store.save_summary(workflow_id, {"source": "ai", "text_fa": "قبلاً نوشته شده",
                                     "next_actions": [], "watch_outs": [], "note": None})

    monkeypatch.setattr(summary_module.llm, "is_available", lambda: True)
    monkeypatch.setattr(summary_module.llm, "ask", _explode)
    worker._seen.clear()

    worker.handle(Envelope(
        type="workflow.completed", payload={"workflow_id": workflow_id}, producer="orchestrator",
    ))
    assert store.get(workflow_id).report["summary"]["text_fa"] == "قبلاً نوشته شده"


def test_the_worker_ignores_a_completion_for_a_workflow_it_does_not_have(store, monkeypatch):
    from agents.orchestrator import api, worker
    from shared.events import Envelope

    monkeypatch.setattr(api, "_store", store)
    worker._seen.clear()

    worker.handle(Envelope(
        type="workflow.completed",
        payload={"workflow_id": str(uuid.uuid4())},
        producer="orchestrator",
    ))                                                  # no raise: nothing to dead-letter


def test_a_summary_is_not_written_over_a_workflow_still_running(store):
    """The state machine owns the report until the workflow is terminal.
    Writing into it mid-flight would be overwritten by the next transition."""
    workflow_id = started(store)
    assert store.save_summary(workflow_id, {"source": "ai", "text_fa": "زود"}) is False


def test_the_worker_listens_for_the_event_it_publishes_itself():
    """workflow.completed is consumed by the same worker that emits it. Without
    the binding the summary step simply never runs, and nothing says so."""
    from agents.orchestrator import worker

    assert "workflow.completed" in worker.ROUTING_KEYS
