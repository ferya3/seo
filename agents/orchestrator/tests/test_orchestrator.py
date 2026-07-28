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


def links_done(job_id: str, status: str = "completed", error: str | None = None) -> dict:
    return {
        "analysis_id": job_id,
        "crawl_id": "crawl-1",
        "status": status,
        "error": error,
        "pages": 3,
        "internal_links": 4,
        "orphan_count": 1,
        "dead_end_count": 1,
        "broken_target_count": 0,
        "max_depth": 2,
        "average_inlinks": 1.3,
        "top_opportunities": [{"url": "https://example.com/deep", "inlinks": 0, "authority": 3.2}],
        "result_url": f"/v1/link-analyses/{job_id}",
    }


def content_done(job_id: str, status: str = "completed", error: str | None = None) -> dict:
    return {
        "analysis_id": job_id,
        "crawl_id": "crawl-1",
        "research_id": "research-1",
        "status": status,
        "error": error,
        "pages": 3,
        "keywords": 4,
        "covered": 3,
        "coverage": 75.0,
        "gap_count": 1,
        "cannibalisation_count": 1,
        "top_gaps": [{"keyword": "کفش کوهنوردی", "demand": 700}],
        "result_url": f"/v1/content-analyses/{job_id}",
    }


def plan_done(job_id: str, status: str = "completed", error: str | None = None) -> dict:
    return {
        "plan_id": job_id,
        "crawl_id": "crawl-1",
        "status": status,
        "error": error,
        "pages_examined": 3,
        "pages_with_fixes": 2,
        "fixes": 5,
        "written_by": "rules",
        "pages": [{"url": "https://example.com/a", "fixes": 3}],
        "result_url": f"/v1/optimizer-plans/{job_id}",
    }


def comparison_done(job_id: str, status: str = "completed", error: str | None = None) -> dict:
    return {
        "comparison_id": job_id,
        "crawl_id": "crawl-1",
        "competitor_crawl_ids": ["crawl-2", "crawl-3"],
        "status": status,
        "error": error,
        "compared_against": 2,
        "behind_on": ["described", "thin"],
        "ahead_on": [],
        "missing_theme_count": 6,
        "top_missing_themes": ["ماراتن", "راهنمای"],
        "result_url": f"/v1/comparisons/{job_id}",
    }


def drive(store, workflow_id, through: int = 6) -> None:
    """Feed a workflow the completion events for its first `through` steps.

    One place that knows the running order, so adding a fifth step later means
    changing this rather than every test that drives a workflow to the end.
    """
    events = ["crawl.completed", "keyword.researched", "serp.checked",
              "links.analyzed", "content.analyzed", "optimizer.planned"]
    payloads = [crawl_done, research_done, serp_done, links_done, content_done, plan_done]

    for index in range(through):
        step = store.get(workflow_id).step_at(index + 1)
        if step.status in ("skipped", "completed"):
            continue
        engine.on_completion(store, events[index], payloads[index](step.job_id))


def started(store, inputs=None) -> str:
    workflow_id = str(uuid.uuid4())
    engine.start(store, workflow_id, "site_audit", inputs or dict(AUDIT))
    return workflow_id


# --------------------------------------------------------------------- planner


def test_a_site_audit_crawls_then_researches_then_checks_rankings():
    steps = planner.plan("site_audit", AUDIT)
    assert [(s.position, s.kind) for s in steps] == [
        (1, "crawl"), (2, "keyword_research"), (3, "serp_check"),
        (4, "link_analysis"), (5, "content_analysis"), (6, "optimizer_plan"),
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
    assert [s.status for s in workflow.steps] == [
        "dispatched", "pending", "pending", "pending", "pending", "pending"
    ]

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
    assert [s.status for s in workflow.steps] == [
        "completed", "dispatched", "pending", "pending", "pending", "pending"
    ]
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

    drive(store, workflow_id)

    workflow = store.get(workflow_id)
    assert workflow.status == "completed"
    assert workflow.report["headline"] == {
        "overall_score": 73, "total_issues": 9, "keywords_found": 128,
        "keywords_ranked": 2, "average_position": 3.5, "orphan_pages": 1,
        "keyword_coverage": 75.0, "pages_to_rewrite": 2,
    }


def test_completion_emits_a_contract_valid_event(store, conn):
    workflow_id = started(store)
    drive(store, workflow_id)

    payload = conn.execute(
        "SELECT payload FROM outbox WHERE event_type='workflow.completed'"
    ).fetchone()[0]
    validate_event("workflow.completed", payload)
    assert payload["status"] == "completed"
    assert [s["status"] for s in payload["steps"]] == ["completed"] * 6


def test_the_report_links_to_results_rather_than_copying_them(store):
    """A crawl report is megabytes. Duplicating it here would create a second
    copy with no way to stay in step with the first."""
    workflow_id = started(store)
    steps = store.get(workflow_id).steps
    drive(store, workflow_id)

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
        except BaseException as exc:
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
    drive(store, workflow_id)

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
                         (workflow_id,)).fetchone()[0] == 6


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
        "completed", "dispatched", "pending", "pending", "pending", "pending"
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
    assert [s["kind"] for s in body["steps"]] == [
        "crawl", "keyword_research", "serp_check", "link_analysis", "content_analysis",
        "optimizer_plan",
    ]

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

    # The skipped step does not end the workflow — the one after it still runs.
    assert store.get(workflow_id).step_at(3).status == "skipped"
    drive(store, workflow_id)

    workflow = store.get(workflow_id)
    assert workflow.status == "completed"
    assert [s.status for s in workflow.steps] == [
        "completed", "completed", "skipped", "completed", "completed", "completed"
    ]
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
    drive(store, workflow_id)

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
    drive(store, workflow_id)

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


def finished(store, inputs=None) -> str:
    """A workflow taken all the way to completed."""
    workflow_id = started(store, inputs)
    drive(store, workflow_id)
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
    assert [s["kind"] for s in steps] == [
        "crawl", "keyword_research", "serp_check", "link_analysis", "content_analysis",
        "optimizer_plan",
    ]


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


# ---------------------------------------------------------------- tenancy


def provisioned(conn, name: str = "acme") -> str:
    tenant = str(uuid.uuid4())
    conn.execute("INSERT INTO tenants (id, name) VALUES (%s, %s)", (tenant, name))
    return tenant


def test_a_workflow_is_only_readable_by_the_tenant_that_owns_it(store, conn):
    """The whole audit hangs off this id — score, keywords, rankings. Reads
    carried no tenant at all, so one id read another account's entire report."""
    mine, theirs = provisioned(conn, "mine"), provisioned(conn, "theirs")
    workflow_id = str(uuid.uuid4())
    engine.start(store, workflow_id, "site_audit", dict(AUDIT), tenant_id=mine)

    assert store.get(workflow_id, tenant_id=mine) is not None
    assert store.get(workflow_id, tenant_id=theirs) is None
    assert store.get(workflow_id) is not None            # the worker's own read


def test_the_workflow_list_is_one_tenants_only(store, conn):
    mine, theirs = provisioned(conn, "mine"), provisioned(conn, "theirs")
    engine.start(store, str(uuid.uuid4()), "site_audit", dict(AUDIT), tenant_id=theirs)
    ours = str(uuid.uuid4())
    engine.start(store, ours, "site_audit", dict(AUDIT), tenant_id=mine)

    assert [w.workflow_id for w in store.recent(25, tenant_id=mine)] == [ours]
    assert len(store.recent(25)) == 2


def test_another_tenants_workflow_is_404_over_http(store, conn, monkeypatch):
    from fastapi.testclient import TestClient

    from agents.orchestrator import api

    monkeypatch.setattr(api, "_store", store)
    client = TestClient(api.app)

    mine, theirs = provisioned(conn, "mine"), provisioned(conn, "theirs")
    workflow_id = str(uuid.uuid4())
    engine.start(store, workflow_id, "site_audit", dict(AUDIT), tenant_id=mine)

    assert client.get(f"/v1/workflows/{workflow_id}",
                      params={"tenant_id": mine}).status_code == 200
    # 404 rather than 403: a 403 confirms the id belongs to someone.
    assert client.get(f"/v1/workflows/{workflow_id}",
                      params={"tenant_id": theirs}).status_code == 404
    assert client.get("/v1/workflows", params={"tenant_id": theirs}).json() == []


# ------------------------------------------------------- the link analysis


def test_the_link_analysis_is_told_which_crawl_to_read(store, conn):
    """The fourth step's only input is the crawl this workflow just ran. The
    service fetches the report itself, so all that travels is the id."""
    workflow_id = started(store)
    steps = store.get(workflow_id).steps
    drive(store, workflow_id, through=3)

    payload = conn.execute(
        "SELECT payload FROM outbox WHERE event_type='links.analysis_requested'"
    ).fetchone()[0]
    validate_event("links.analysis_requested", payload)
    assert payload["crawl_id"] == steps[0].job_id
    assert payload["analysis_id"] == steps[3].job_id


def test_a_workflow_whose_crawl_failed_never_asks_for_a_link_analysis(store, conn):
    workflow_id = started(store)
    step = store.get(workflow_id).step_at(1)
    engine.on_completion(
        store, "crawl.completed", crawl_done(step.job_id, "failed", "Timeout: unreachable")
    )

    assert store.get(workflow_id).status == "failed"
    assert conn.execute(
        "SELECT count(*) FROM outbox WHERE event_type='links.analysis_requested'"
    ).fetchone()[0] == 0


def test_the_link_findings_reach_the_final_report(store):
    workflow_id = started(store)
    drive(store, workflow_id)

    report = store.get(workflow_id).report
    assert report["links"]["orphan_count"] == 1
    assert report["headline"]["orphan_pages"] == 1
    assert report["links"]["result_url"].startswith("/v1/link-analyses/")


def test_the_summary_says_what_the_links_look_like(store):
    workflow_id = started(store)
    drive(store, workflow_id)

    summary = store.get(workflow_id).report["summary"]
    assert "لینک داخلی" in summary["text_fa"]
    # The cheapest piece of work a link graph can name, so it belongs in the
    # action list rather than only in the numbers.
    assert any("لینک" in a["action"] for a in summary["next_actions"])


# ---------------------------------------------------- the content analysis


def test_the_content_step_is_told_about_both_earlier_steps(store, conn):
    """The only step in the plan that needs two earlier ones at once: what
    pages exist, and what people search for."""
    workflow_id = started(store)
    steps = store.get(workflow_id).steps
    drive(store, workflow_id, through=4)

    payload = conn.execute(
        "SELECT payload FROM outbox WHERE event_type='content.analysis_requested'"
    ).fetchone()[0]
    validate_event("content.analysis_requested", payload)
    assert payload["crawl_id"] == steps[0].job_id
    assert payload["research_id"] == steps[1].job_id


def test_without_a_keyword_study_the_content_step_is_skipped_not_failed(store, conn):
    """A site with no keywords is a real answer about that site. There is no
    coverage question to ask, which is not the same as something breaking."""
    workflow_id = started(store)
    steps = store.get(workflow_id).steps
    engine.on_completion(store, "crawl.completed", crawl_done(steps[0].job_id))

    empty = research_done(steps[1].job_id)
    empty["top_keywords"] = []
    empty["total"] = 0
    engine.on_completion(store, "keyword.researched", empty)
    drive(store, workflow_id)

    workflow = store.get(workflow_id)
    assert workflow.status == "completed"
    assert workflow.step_at(5).status in ("completed", "skipped")


def test_the_coverage_findings_reach_the_final_report(store):
    workflow_id = started(store)
    drive(store, workflow_id)

    report = store.get(workflow_id).report
    assert report["content"]["coverage"] == 75.0
    assert report["headline"]["keyword_coverage"] == 75.0
    assert report["content"]["top_gaps"][0]["keyword"] == "کفش کوهنوردی"


def test_the_summary_names_the_missing_page(store):
    workflow_id = started(store)
    drive(store, workflow_id)

    summary = store.get(workflow_id).report["summary"]
    assert "پوشش" in summary["text_fa"]
    assert any("کفش کوهنوردی" in action["action"] for action in summary["next_actions"])


# ------------------------------------------------------- the rewrite plan


def test_the_optimizer_step_gets_the_crawl_and_the_study(store, conn):
    workflow_id = started(store)
    steps = store.get(workflow_id).steps
    drive(store, workflow_id, through=5)

    payload = conn.execute(
        "SELECT payload FROM outbox WHERE event_type='optimizer.plan_requested'"
    ).fetchone()[0]
    validate_event("optimizer.plan_requested", payload)
    assert payload["crawl_id"] == steps[0].job_id
    assert payload["research_id"] == steps[1].job_id
    assert payload["pages"] == planner.DEFAULT_OPTIMIZED


def test_a_workflow_with_no_keywords_still_asks_for_a_plan(store, conn):
    """Length and structure fixes do not need a keyword study, and skipping
    the only step that proposes anything would be the wrong trade."""
    workflow_id = started(store)
    steps = store.get(workflow_id).steps
    engine.on_completion(store, "crawl.completed", crawl_done(steps[0].job_id))

    empty = research_done(steps[1].job_id)
    empty["top_keywords"] = []
    empty["total"] = 0
    engine.on_completion(store, "keyword.researched", empty)
    drive(store, workflow_id)

    payload = conn.execute(
        "SELECT payload FROM outbox WHERE event_type='optimizer.plan_requested'"
    ).fetchone()[0]
    assert payload["crawl_id"] == steps[0].job_id
    assert store.get(workflow_id).step_at(6).status == "completed"


def test_the_page_budget_reaches_the_plan(store, conn, monkeypatch):
    from fastapi.testclient import TestClient

    from agents.orchestrator import api

    monkeypatch.setattr(api, "_store", store)
    client = TestClient(api.app)

    workflow_id = client.post("/v1/workflows", json={
        "goal": "site_audit",
        "inputs": {"start_url": "https://example.com", "seed": "کفش", "optimize_pages": 2},
    }).json()["workflow_id"]
    drive(store, workflow_id, through=5)

    payload = conn.execute(
        "SELECT payload FROM outbox WHERE event_type='optimizer.plan_requested'"
    ).fetchone()[0]
    assert payload["pages"] == 2


def test_the_rewrite_plan_reaches_the_final_report(store):
    workflow_id = started(store)
    drive(store, workflow_id)

    report = store.get(workflow_id).report
    assert report["optimizer"]["pages_with_fixes"] == 2
    assert report["headline"]["pages_to_rewrite"] == 2
    assert "پیشنهاد" in report["summary"]["text_fa"] or "اصلاح" in report["summary"]["text_fa"]


# ------------------------------------------------------------- competitors


RIVALS = {**AUDIT, "competitors": ["https://rival-one.test", "rival-two.test"]}


def drive_rivals(store, workflow_id) -> list:
    """Run the six ordinary steps, then each competitor crawl, then compare."""
    drive(store, workflow_id)
    # One at a time: the competitors are separate steps, so the next one is
    # only dispatched once the previous has come back.
    for _ in range(planner.MAX_COMPETITORS):
        pending = next(
            (s for s in store.get(workflow_id).steps
             if s.kind == "competitor_crawl" and s.status == "dispatched"), None,
        )
        if pending is None:
            break
        engine.on_completion(store, "crawl.completed", crawl_done(pending.job_id))
    workflow = store.get(workflow_id)
    check = next(s for s in workflow.steps if s.kind == "competitor_check")
    if check.status == "dispatched":
        engine.on_completion(store, "competitor.compared", comparison_done(check.job_id))
    return store.get(workflow_id).steps


def test_each_competitor_becomes_its_own_crawl_step():
    steps = planner.plan("site_audit", RIVALS)
    assert [(s.position, s.kind) for s in steps][6:] == [
        (7, "competitor_crawl"), (8, "competitor_crawl"), (9, "competitor_check"),
    ]
    # The url is on the step, not derivable from the workflow's inputs.
    assert steps[6].params["start_url"] == "https://rival-one.test"
    # A bare host is still a site to crawl.
    assert steps[7].params["start_url"] == "https://rival-two.test"


def test_no_competitors_means_no_extra_steps_at_all():
    # Not an empty comparison step: a comparison against nobody is a section of
    # zeros that reads as "you are level with the market".
    assert len(planner.plan("site_audit", AUDIT)) == 6
    assert len(planner.plan("site_audit", {**AUDIT, "competitors": []})) == 6


def test_the_same_competitor_twice_is_crawled_once():
    steps = planner.plan("site_audit", {
        **AUDIT, "competitors": ["https://rival.test/a", "http://www.rival.test/b"],
    })
    assert sum(1 for s in steps if s.kind == "competitor_crawl") == 1


def test_more_competitors_than_the_ceiling_are_dropped_not_crawled():
    steps = planner.plan("site_audit", {
        **AUDIT, "competitors": [f"https://rival{i}.test" for i in range(10)],
    })
    assert sum(1 for s in steps if s.kind == "competitor_crawl") == planner.MAX_COMPETITORS


def test_competitors_are_crawled_shallower_than_your_own_site(store):
    workflow_id = started(store, {**RIVALS, "max_pages": 500})
    steps = {s.position: s for s in store.get(workflow_id).steps}

    assert steps[1].params.get("max_pages") in (None, 500)
    assert steps[7].params["max_pages"] == planner.DEFAULT_COMPETITOR_PAGES


def test_a_competitor_crawl_is_dispatched_with_its_own_url(store, conn):
    workflow_id = started(store, RIVALS)
    drive(store, workflow_id)          # the six ordinary steps

    rival = next(s for s in store.get(workflow_id).steps if s.kind == "competitor_crawl")
    assert rival.status == "dispatched"

    payload = conn.execute(
        "SELECT payload FROM outbox WHERE payload->>'crawl_id' = %s", (rival.job_id,)
    ).fetchone()[0]
    # Not the workflow's start_url, which is what a step-blind dispatch would
    # have sent — and it would have crawled your own site four times.
    assert payload["start_url"] == "https://rival-one.test"


def test_the_comparison_gets_your_crawl_and_theirs(store, conn):
    workflow_id = started(store, RIVALS)
    drive(store, workflow_id)
    workflow = store.get(workflow_id)
    own = next(s for s in workflow.steps if s.kind == "crawl")
    rivals = [s for s in workflow.steps if s.kind == "competitor_crawl"]

    for step in rivals:
        engine.on_completion(store, "crawl.completed", crawl_done(step.job_id))

    check = next(s for s in store.get(workflow_id).steps if s.kind == "competitor_check")
    payload = conn.execute(
        "SELECT payload FROM outbox WHERE payload->>'comparison_id' = %s", (check.job_id,)
    ).fetchone()[0]

    assert payload["crawl_id"] == own.job_id
    assert payload["competitor_crawl_ids"] == [s.job_id for s in rivals]
    validate_event("competitor.comparison_requested", payload)


def test_a_competitor_being_down_does_not_fail_your_audit(store):
    workflow_id = started(store, RIVALS)
    drive(store, workflow_id)
    workflow = store.get(workflow_id)
    rivals = [s for s in workflow.steps if s.kind == "competitor_crawl"]

    engine.on_completion(store, "crawl.completed",
                         crawl_done(rivals[0].job_id, "failed", "connection refused"))
    engine.on_completion(store, "crawl.completed", crawl_done(rivals[1].job_id))

    check = next(s for s in store.get(workflow_id).steps if s.kind == "competitor_check")
    engine.on_completion(store, "competitor.compared", comparison_done(check.job_id))

    workflow = store.get(workflow_id)
    assert workflow.status == "completed"
    # The step still says what happened; the workflow does not inherit it.
    failed = next(s for s in workflow.steps if s.job_id == rivals[0].job_id)
    assert failed.status == "failed"
    assert workflow.error is None


def test_the_comparison_only_names_the_competitors_that_finished(store, conn):
    workflow_id = started(store, RIVALS)
    drive(store, workflow_id)
    rivals = [s for s in store.get(workflow_id).steps if s.kind == "competitor_crawl"]

    engine.on_completion(store, "crawl.completed", crawl_done(rivals[0].job_id, "failed", "gone"))
    engine.on_completion(store, "crawl.completed", crawl_done(rivals[1].job_id))

    check = next(s for s in store.get(workflow_id).steps if s.kind == "competitor_check")
    payload = conn.execute(
        "SELECT payload FROM outbox WHERE payload->>'comparison_id' = %s", (check.job_id,)
    ).fetchone()[0]
    assert payload["competitor_crawl_ids"] == [rivals[1].job_id]


def test_every_competitor_failing_skips_the_comparison(store):
    workflow_id = started(store, RIVALS)
    drive(store, workflow_id)
    rivals = [s for s in store.get(workflow_id).steps if s.kind == "competitor_crawl"]
    for step in rivals:
        engine.on_completion(store, "crawl.completed", crawl_done(step.job_id, "failed", "gone"))

    workflow = store.get(workflow_id)
    check = next(s for s in workflow.steps if s.kind == "competitor_check")
    # Skipped, not failed: there was nothing to compare, which is an answer.
    assert check.status == "skipped"
    assert "nothing to compare" in check.error
    assert workflow.status == "completed"


def test_your_own_crawl_is_still_the_one_the_other_steps_use(store, conn):
    """The regression this whole change could have caused.

    Four crawl steps now exist in one workflow. Every step that says "the
    crawl" has to keep meaning yours, or the link graph, the coverage report
    and the rewrite plan would all quietly describe a competitor's site.
    """
    workflow_id = started(store, RIVALS)
    workflow = store.get(workflow_id)
    own = next(s for s in workflow.steps if s.kind == "crawl")

    engine.on_completion(store, "crawl.completed", crawl_done(own.job_id))
    engine.on_completion(store, "keyword.researched",
                         research_done(workflow.step_at(2).job_id))
    engine.on_completion(store, "serp.checked", serp_done(workflow.step_at(3).job_id))

    links = workflow.step_at(4)
    payload = conn.execute(
        "SELECT payload FROM outbox WHERE payload->>'analysis_id' = %s", (links.job_id,)
    ).fetchone()[0]
    assert payload["crawl_id"] == own.job_id


def test_the_report_carries_the_comparison_and_the_headline_says_so(store):
    workflow_id = started(store, RIVALS)
    drive_rivals(store, workflow_id)

    report = store.get(workflow_id).report
    assert report["competitors"]["compared_against"] == 2
    assert report["headline"]["competitors_compared"] == 2
    assert report["headline"]["behind_on"] == 2


def test_a_workflow_without_competitors_has_no_competitor_section(store):
    workflow_id = started(store)
    drive(store, workflow_id)

    report = store.get(workflow_id).report
    assert "competitors" not in report
    assert "competitors_compared" not in report["headline"]


def test_two_ports_on_one_host_are_two_competitors():
    # Found live: both test sites sat on 127.0.0.1 and collapsed into one.
    # Rare between real competitors, ordinary between two local sites, and
    # wrong in both cases — different origins are different sites.
    steps = planner.plan("site_audit", {
        **AUDIT, "competitors": ["http://127.0.0.1:8501/", "http://127.0.0.1:8502/"],
    })
    assert sum(1 for s in steps if s.kind == "competitor_crawl") == 2


def test_www_and_a_trailing_path_still_mean_one_competitor():
    steps = planner.plan("site_audit", {
        **AUDIT, "competitors": ["https://www.rival.test/pricing", "https://rival.test/"],
    })
    assert sum(1 for s in steps if s.kind == "competitor_crawl") == 1


# ------------------------------------------------------------------- trends


def test_the_first_audit_of_a_site_has_no_trend(store):
    workflow_id = finished(store)
    assert "trend" not in store.get(workflow_id).report


def test_the_second_audit_compares_itself_with_the_first(store):
    first = finished(store)
    second = finished(store)

    trend = store.get(second).report["trend"]
    assert trend["compared_with"] == first
    assert {c["metric"] for c in trend["changes"]} >= {"overall_score", "total_issues"}


def test_a_trend_only_looks_at_the_same_site(store):
    finished(store, {"start_url": "https://one.test", "seed": "کفش"})
    other = finished(store, {"start_url": "https://two.test", "seed": "کفش"})

    # Two different sites audited in the same minute is the ordinary case for
    # anyone tracking more than one, and lining their numbers up would be
    # nonsense presented as history.
    assert "trend" not in store.get(other).report


def test_a_trend_never_reaches_across_tenants(store):
    alice = str(uuid.uuid4())
    engine.start(store, alice, "site_audit", dict(AUDIT), tenant_id=None)
    drive(store, alice)

    bob_id = str(uuid.uuid4())
    with psycopg.connect(DSN, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO tenants (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (bob_id, "bob"),
        )
    bob = str(uuid.uuid4())
    engine.start(store, bob, "site_audit", dict(AUDIT), tenant_id=bob_id)
    drive(store, bob)

    # Same url, different account. Bob's first audit is Bob's first audit.
    assert "trend" not in store.get(bob).report


def test_a_failed_run_is_not_something_the_next_one_compares_against(store):
    broken = started(store)
    step = store.get(broken).step_at(1)
    engine.on_completion(store, "crawl.completed",
                         crawl_done(step.job_id, "failed", "connection refused"))
    assert store.get(broken).status == "failed"

    after = finished(store)
    # Half a crawl has half the issues, and "issues halved" would be the story
    # of a run that stopped early.
    assert "trend" not in store.get(after).report


def test_the_summary_leads_with_the_direction_the_site_is_moving(store):
    finished(store)
    second = finished(store)

    text = store.get(second).report["summary"]["text_fa"]
    assert "نسبت به اجرای قبلی" in text or "امتیاز کلی از" in text


def test_history_lists_the_same_sites_finished_runs_newest_first(store):
    first = finished(store)
    second = finished(store)

    runs = store.history(store.get(second))
    assert [r["workflow_id"] for r in runs] == [second, first]
    assert runs[0]["headline"]["overall_score"] == 73


def test_history_carries_headlines_not_whole_reports(store):
    workflow_id = finished(store)
    row = store.history(store.get(workflow_id))[0]

    # A history endpoint that returned reports would send megabytes to draw
    # one line.
    assert set(row) == {"workflow_id", "started_at", "finished_at", "headline"}
    assert "crawl" not in row["headline"]


def test_the_history_endpoint_is_scoped_to_the_caller(store, monkeypatch):
    from fastapi.testclient import TestClient

    from agents.orchestrator import api

    api.reset_store(store)
    client = TestClient(api.app)
    workflow_id = finished(store)

    assert client.get(f"/v1/workflows/{workflow_id}/history").status_code == 200
    assert client.get(
        f"/v1/workflows/{workflow_id}/history", params={"tenant_id": str(uuid.uuid4())}
    ).status_code == 404
    api.reset_store(None)


def test_a_list_row_names_the_site_it_audited(store):
    """A list of audits that says only "site_audit" and eight hex characters
    is unreadable the moment somebody tracks two sites."""
    workflow_id = finished(store, {"start_url": "https://shop.test", "seed": "کفش"})
    row = store.get(workflow_id).summary()

    assert row["start_url"] == "https://shop.test"
    assert row["overall_score"] == 73


def test_a_list_row_carries_the_direction_the_report_decided(store):
    finished(store)
    second = finished(store)

    first_row = store.recent(2)[1].summary()
    second_row = store.get(second).summary()

    # Nothing to compare against on the first run, so no direction at all —
    # the same rule the report follows, not a second copy of it.
    assert first_row["trend"] is None
    assert second_row["trend"] == {"better": 0, "worse": 0}


def test_a_running_workflow_has_a_row_without_pretending_to_have_a_score(store):
    workflow_id = started(store)
    row = store.get(workflow_id).summary()

    assert row["start_url"] == "https://example.com"
    assert row["overall_score"] is None
    assert row["trend"] is None
