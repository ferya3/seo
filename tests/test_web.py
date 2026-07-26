"""Dashboard routes, job lifecycle and export endpoints."""

from __future__ import annotations

import time

import pytest

from seoagent.web.app import create_app, get_store


@pytest.fixture
def client():
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def wait_for(job_id: str, timeout: float = 45.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = get_store().get(job_id)
        assert job is not None
        if job.status in ("done", "error"):
            return job.to_dict()
        time.sleep(0.15)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


# ------------------------------------------------------------------- routing


def test_dashboard_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "بررسی سئوی سایت" in body
    assert "تحقیق کلمات کلیدی" in body


def test_static_assets_are_served(client):
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_unknown_report_is_a_404_page(client):
    response = client.get("/report/doesnotexist")
    assert response.status_code == 404
    assert "پیدا نشد" in response.get_data(as_text=True)


def test_unknown_job_api_returns_404(client):
    assert client.get("/api/job/nope").status_code == 404
    assert client.get("/api/job/nope/result").status_code == 404


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "endpoint,payload",
    [
        ("/api/audit", {}),
        ("/api/audit", {"url": "   "}),
        ("/api/keywords", {}),
        ("/api/keywords", {"seed": ""}),
    ],
)
def test_missing_input_is_rejected(client, endpoint, payload):
    response = client.post(endpoint, json=payload)
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_bare_hostname_gets_a_scheme(client, bad_site):
    host = bad_site.removeprefix("http://")
    response = client.post("/api/audit", json={"url": host, "max_pages": 2, "check_links": False})
    assert response.status_code == 202
    job = wait_for(response.get_json()["id"])
    assert job["status"] == "done"


def test_out_of_range_limits_are_clamped(client, bad_site):
    response = client.post(
        "/api/audit",
        json={"url": bad_site, "max_pages": 99999, "max_depth": -5, "check_links": False},
    )
    assert response.status_code == 202
    job = wait_for(response.get_json()["id"])
    assert job["status"] == "done"


# ------------------------------------------------------------ audit lifecycle


def test_audit_job_runs_and_exports(client, bad_site):
    response = client.post(
        "/api/audit",
        json={"url": bad_site, "max_pages": 8, "check_links": False, "keywords": "تست"},
    )
    assert response.status_code == 202
    job_id = response.get_json()["id"]

    job = wait_for(job_id)
    assert job["status"] == "done", job.get("error")
    assert job["percent"] == 100

    report_page = client.get(f"/report/{job_id}")
    assert report_page.status_code == 200
    html = report_page.get_data(as_text=True)
    assert "گزارش سئو" in html
    assert "امتیاز هر بخش" in html

    payload = client.get(f"/api/job/{job_id}/result").get_json()
    assert payload["overall_score"] >= 0
    assert payload["issues"]

    markdown = client.get(f"/api/job/{job_id}/export.md")
    assert markdown.status_code == 200
    assert "# گزارش سئو" in markdown.get_data(as_text=True)
    assert "attachment" in markdown.headers["Content-Disposition"]

    assert any(j["id"] == job_id for j in client.get("/api/jobs").get_json())


def test_result_is_409_while_still_running(client, bad_site):
    response = client.post("/api/audit", json={"url": bad_site, "max_pages": 5, "check_links": False})
    job_id = response.get_json()["id"]
    early = client.get(f"/api/job/{job_id}/result")
    assert early.status_code in (200, 409)  # may already be done on a fast machine
    wait_for(job_id)


def test_unreachable_host_still_produces_a_report(client):
    """A dead site is a finding, not a crash."""
    response = client.post(
        "/api/audit",
        json={"url": "http://127.0.0.1:9/", "max_pages": 2, "check_links": False},
    )
    assert response.status_code == 202
    job = wait_for(response.get_json()["id"])
    assert job["status"] == "done"
    payload = client.get(f"/api/job/{job['id']}/result").get_json()
    assert any(i["id"] == "unreachable-pages" for i in payload["issues"])


# --------------------------------------------------------- keyword lifecycle


def test_keyword_job_runs_and_renders(client, monkeypatch):
    from seoagent.keywords import sources as source_module
    from seoagent.keywords.sources import Suggestion

    def fake(query, lang="fa", country="IR", timeout=12.0):
        return [Suggestion(f"{query} راهنما", "google", 0), Suggestion(f"{query} قیمت", "google", 1)]

    monkeypatch.setattr(source_module, "SOURCES", {"google": fake})
    monkeypatch.setattr(source_module, "trending_now", lambda *a, **k: ["ترند"])
    monkeypatch.setattr(source_module, "related_from_wikipedia", lambda *a, **k: [])

    response = client.post(
        "/api/keywords",
        json={"seed": "کفش", "sources": ["google"], "alphabet": False, "comparisons": False},
    )
    assert response.status_code == 202
    job_id = response.get_json()["id"]

    job = wait_for(job_id)
    assert job["status"] == "done", job.get("error")

    html = client.get(f"/report/{job_id}").get_data(as_text=True)
    assert "تحقیق کلمات کلیدی" in html
    assert "همه‌ی کلمات کلیدی" in html

    payload = client.get(f"/api/job/{job_id}/result").get_json()
    assert payload["total"] > 0
    assert payload["keywords"][0]["keyword"]

    markdown = client.get(f"/api/job/{job_id}/export.md").get_data(as_text=True)
    assert "# تحقیق کلمات کلیدی" in markdown


# ------------------------------------------------------------------ job store


def test_job_store_evicts_oldest_beyond_capacity():
    from seoagent.web.jobs import JobStore

    small = JobStore(max_jobs=3)
    created = [small.create("audit", f"job-{i}") for i in range(5)]
    assert small.get(created[0].id) is None
    assert small.get(created[-1].id) is not None
    assert len(small.recent(10)) == 3


def test_job_failure_is_captured_not_raised():
    from seoagent.web.jobs import JobStore

    local = JobStore()
    job = local.create("audit", "boom")

    def explode(_job):
        raise RuntimeError("kaboom")

    local.run(job, explode)
    for _ in range(60):
        if job.status == "error":
            break
        time.sleep(0.05)
    assert job.status == "error"
    assert "kaboom" in (job.error or "")


# ----------------------------------------------------------- job persistence


def test_finished_reports_survive_a_restart(tmp_path):
    """A server restarts on every deploy and reboot; losing every report each
    time would make the dashboard useless for anything but one sitting."""
    from seoagent.web.jobs import JobStore

    first = JobStore(storage_dir=tmp_path)
    job = first.create("audit", "https://example.com")
    first.run(job, lambda _j: {"overall_score": 77, "issues": []})
    for _ in range(60):
        if job.status == "done":
            break
        time.sleep(0.05)
    assert job.status == "done"

    # A brand-new store, as if the process had been restarted.
    second = JobStore(storage_dir=tmp_path)
    restored = second.get(job.id)
    assert restored is not None
    assert restored.status == "done"
    assert restored.result == {"overall_score": 77, "issues": []}
    assert restored.label == "https://example.com"


def test_failed_jobs_are_not_persisted(tmp_path):
    from seoagent.web.jobs import JobStore

    store_ = JobStore(storage_dir=tmp_path)
    job = store_.create("audit", "boom")

    def explode(_job):
        raise RuntimeError("nope")

    store_.run(job, explode)
    for _ in range(60):
        if job.status == "error":
            break
        time.sleep(0.05)

    assert list(tmp_path.glob("*.json")) == []
    assert JobStore(storage_dir=tmp_path).get(job.id) is None


def test_eviction_removes_the_file_too(tmp_path):
    from seoagent.web.jobs import JobStore

    small = JobStore(max_jobs=2, storage_dir=tmp_path)
    jobs = []
    for i in range(3):
        job = small.create("audit", f"site-{i}")
        small.run(job, lambda _j, n=i: {"n": n})
        jobs.append(job)
    for job in jobs:
        for _ in range(60):
            if job.status in ("done", "error"):
                break
            time.sleep(0.05)

    assert small.get(jobs[0].id) is None
    assert not (tmp_path / f"{jobs[0].id}.json").exists()


def test_unwritable_storage_degrades_to_memory_only(tmp_path):
    """An unwritable data dir must not stop the dashboard from starting."""
    from seoagent.web.jobs import JobStore

    blocker = tmp_path / "not-a-dir"
    blocker.write_text("i am a file", encoding="utf-8")

    store_ = JobStore(storage_dir=blocker)
    assert store_.storage_dir is None
    job = store_.create("audit", "still works")
    assert store_.get(job.id) is job


def test_corrupt_report_file_is_skipped(tmp_path):
    from seoagent.web.jobs import JobStore

    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "empty.json").write_text("{}", encoding="utf-8")
    assert JobStore(storage_dir=tmp_path).recent() == []
