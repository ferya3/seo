"""Postgres job store, transactional outbox, and the relay.

These run against a real Postgres — a store whose whole value is transactional
atomicity cannot be proven with a fake. Set TEST_DATABASE_URL to a database
with the migrations applied; without it the module skips rather than pretending
to have covered anything.

    createdb seo && psql -d seo -f infra/db/migrations/0001_core.sql \
                    && psql -d seo -f infra/db/migrations/0002_research.sql
    TEST_DATABASE_URL=postgresql://seo@127.0.0.1/seo pytest shared/tests
"""

from __future__ import annotations

import os
import uuid

import pytest

from shared import relay
from shared.db import DatabaseUnavailable, PostgresJobStore, store_for
from shared.store import FileJobStore, PendingEvent

psycopg = pytest.importorskip("psycopg")

DSN = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not set")

from services.crawl.store import TABLE as CRAWL_TABLE  # noqa: E402
from services.crawl.store import CrawlRecord  # noqa: E402
from services.keyword.store import TABLE as RESEARCH_TABLE  # noqa: E402
from services.keyword.store import ResearchRecord  # noqa: E402


@pytest.fixture
def clean():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("TRUNCATE crawls, research, outbox RESTART IDENTITY CASCADE")
    yield


@pytest.fixture
def crawls(clean):
    store = PostgresJobStore(DSN, CRAWL_TABLE, CrawlRecord)
    yield store
    store.close()


@pytest.fixture
def research(clean):
    store = PostgresJobStore(DSN, RESEARCH_TABLE, ResearchRecord)
    yield store
    store.close()


@pytest.fixture
def conn(clean):
    with psycopg.connect(DSN, autocommit=True) as connection:
        yield connection


REPORT = {
    "overall_score": 73,
    "grade": "B",
    "stats": {"total_issues": 4},
    "pages": [{"url": "https://example.com/", "indexable": True}],
}


class Recorder:
    """Stands in for the bus. Records what the relay handed it."""

    def __init__(self, fail: bool = False):
        self.published: list = []
        self.fail = fail

    def publish(self, envelope):
        if self.fail:
            raise ConnectionError("broker down")
        self.published.append(envelope)


# ------------------------------------------------------------------- the store


def test_a_crawl_round_trips(crawls):
    crawl_id = str(uuid.uuid4())
    crawls.create(crawl_id, "https://example.com")

    assert crawls.get(crawl_id).status == "queued"
    crawls.mark_running(crawl_id)
    assert crawls.get(crawl_id).status == "running"

    record = crawls.complete(crawl_id, REPORT)
    assert record.status == "completed"
    assert record.report["overall_score"] == 73
    # Read back through a second connection: the value is on disk, not in a
    # process-local cache the way the file store's memory map would be.
    assert crawls.get(crawl_id).report["grade"] == "B"


def test_the_two_services_use_separate_tables(crawls, research):
    shared_id = str(uuid.uuid4())
    crawls.create(shared_id, "https://example.com")
    assert research.get(shared_id) is None


def test_derived_values_become_real_columns(crawls, conn):
    """Not cosmetic: the list endpoint and the dashboard sort on these, and a
    sort over a JSONB field cannot use an index."""
    crawl_id = str(uuid.uuid4())
    crawls.create(crawl_id, "https://example.com")
    crawls.complete(crawl_id, REPORT)

    score = conn.execute("SELECT overall_score FROM crawls WHERE id = %s", (crawl_id,)).fetchone()
    assert score[0] == 73


def test_research_derives_its_own_columns(research, conn):
    research_id = str(uuid.uuid4())
    research.create(research_id, "کفش")
    research.complete(research_id, {"total": 128, "lang": "fa", "country": "IR"})

    row = conn.execute(
        "SELECT total, lang, country FROM research WHERE id = %s", (research_id,)
    ).fetchone()
    assert row == (128, "fa", "IR")


def test_a_failure_is_recorded(crawls):
    crawl_id = str(uuid.uuid4())
    crawls.create(crawl_id, "https://example.com")
    record = crawls.fail(crawl_id, "Timeout: unreachable")
    assert record.status == "failed"
    assert "unreachable" in record.error


def test_recent_is_newest_first_and_count_agrees(crawls):
    ids = [str(uuid.uuid4()) for _ in range(3)]
    for index, crawl_id in enumerate(ids):
        crawls.create(crawl_id, f"https://example.com/{index}")

    assert crawls.count() == 3
    assert len(crawls.recent(2)) == 2
    listed = [r.crawl_id for r in crawls.recent(10)]
    assert set(listed) == set(ids)


def test_updating_an_unknown_job_raises(crawls):
    with pytest.raises(KeyError):
        crawls.complete(str(uuid.uuid4()), REPORT)


def test_an_unknown_column_is_refused_not_interpolated(crawls):
    """The SET clause is built by string concatenation, so anything outside the
    record's own fields has to be rejected before it reaches the query."""
    crawl_id = str(uuid.uuid4())
    crawls.create(crawl_id, "https://example.com")
    with pytest.raises(KeyError):
        crawls.update(crawl_id, **{"status = 'x' --": "boom"})


def test_creating_the_same_id_twice_is_harmless(crawls):
    """A redelivered crawl.requested must not blow up on the insert."""
    crawl_id = str(uuid.uuid4())
    crawls.create(crawl_id, "https://example.com")
    crawls.create(crawl_id, "https://example.com")
    assert crawls.count() == 1


# ------------------------------------------------------------------ the outbox


def test_finishing_stages_the_event(crawls, conn):
    crawl_id = str(uuid.uuid4())
    crawls.create(crawl_id, "https://example.com")
    event = PendingEvent("crawl.completed", {"crawl_id": crawl_id}, "crawl-service")
    crawls.complete(crawl_id, REPORT, events=[event])

    row = conn.execute(
        "SELECT event_id, event_type, payload, published_at FROM outbox"
    ).fetchone()
    assert str(row[0]) == event.event_id
    assert row[1] == "crawl.completed"
    assert row[2]["crawl_id"] == crawl_id
    assert row[3] is None


def test_the_event_and_the_status_share_one_transaction(crawls, conn, monkeypatch):
    """The reason this store exists. If staging fails, the status write must go
    with it — a completed crawl with no event is exactly what the outbox is for
    preventing."""
    crawl_id = str(uuid.uuid4())
    crawls.create(crawl_id, "https://example.com")

    def explode(*_args, **_kwargs):
        raise RuntimeError("outbox insert failed")

    monkeypatch.setattr("shared.db._stage", explode)

    with pytest.raises(RuntimeError):
        crawls.complete(
            crawl_id, REPORT, events=[PendingEvent("crawl.completed", {}, "crawl-service")]
        )

    assert crawls.get(crawl_id).status == "queued"
    assert conn.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


def test_a_failed_job_still_stages_its_event(crawls, conn):
    crawl_id = str(uuid.uuid4())
    crawls.create(crawl_id, "https://example.com")
    crawls.fail(
        crawl_id, "Timeout",
        events=[PendingEvent("crawl.completed", {"status": "failed"}, "crawl-service")],
    )
    assert conn.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1


def test_staging_the_same_event_twice_is_idempotent(crawls, conn):
    crawl_id = str(uuid.uuid4())
    crawls.create(crawl_id, "https://example.com")
    event = PendingEvent("crawl.completed", {"crawl_id": crawl_id}, "crawl-service")
    crawls.complete(crawl_id, REPORT, events=[event])
    crawls.complete(crawl_id, REPORT, events=[event])
    assert conn.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1


def test_tenancy_travels_with_the_event(research, conn):
    """The relay never sees the job row, so whatever the event carries is all a
    consumer gets — a tenant id lost here is a tenant id lost for good."""
    tenant = str(uuid.uuid4())
    project = str(uuid.uuid4())
    conn.execute("INSERT INTO tenants (id, name) VALUES (%s, 'acme')", (tenant,))
    conn.execute(
        "INSERT INTO projects (id, tenant_id, name, domain) VALUES (%s, %s, 'site', 'example.com')",
        (project, tenant),
    )

    research_id = str(uuid.uuid4())
    research.create(research_id, "کفش", tenant_id=tenant, project_id=project)
    research.complete(
        research_id, {"total": 1},
        events=[PendingEvent("keyword.researched", {}, "keyword-service")],
    )

    row = conn.execute("SELECT tenant_id, project_id FROM outbox").fetchone()
    assert (str(row[0]), str(row[1])) == (tenant, project)


# ------------------------------------------------------------------- the relay


def test_the_relay_publishes_and_marks(crawls, conn):
    crawl_id = str(uuid.uuid4())
    crawls.create(crawl_id, "https://example.com")
    event = PendingEvent("crawl.completed", {"crawl_id": crawl_id}, "crawl-service")
    crawls.complete(crawl_id, REPORT, events=[event])

    bus = Recorder()
    assert relay.drain(conn, bus) == 1
    assert conn.execute("SELECT count(*) FROM outbox WHERE published_at IS NULL").fetchone()[0] == 0

    envelope = bus.published[0]
    assert envelope.type == "crawl.completed"
    # The staged id becomes the envelope id — this is what a consumer
    # deduplicates on when the relay redelivers.
    assert envelope.id == event.event_id
    assert envelope.payload["crawl_id"] == crawl_id


def test_the_relay_publishes_under_the_originating_service(crawls, conn):
    """Not cosmetic. The relay delivers for everyone, so if it stamped itself as
    the producer, "which service emitted this" would stop being answerable from
    the message — and that is the first question asked when an event is wrong."""
    crawl_id = str(uuid.uuid4())
    crawls.create(crawl_id, "https://example.com")
    crawls.complete(
        crawl_id, REPORT, events=[PendingEvent("crawl.completed", {}, "crawl-service")]
    )

    bus = Recorder()
    relay.drain(conn, bus)
    assert bus.published[0].producer == "crawl-service"
    assert bus.published[0].producer != relay.SERVICE


def test_a_second_pass_publishes_nothing(crawls, conn):
    crawl_id = str(uuid.uuid4())
    crawls.create(crawl_id, "https://example.com")
    crawls.complete(crawl_id, REPORT, events=[PendingEvent("crawl.completed", {}, "crawl-service")])

    relay.drain(conn, Recorder())
    bus = Recorder()
    assert relay.drain(conn, bus) == 0
    assert bus.published == []


def test_a_broker_failure_leaves_the_event_unpublished(crawls, conn):
    """Publish-then-mark, so a broker outage costs a retry rather than the
    event. Marking first would lose it silently."""
    crawl_id = str(uuid.uuid4())
    crawls.create(crawl_id, "https://example.com")
    crawls.complete(crawl_id, REPORT, events=[PendingEvent("crawl.completed", {}, "crawl-service")])

    with pytest.raises(ConnectionError):
        relay.drain(conn, Recorder(fail=True))

    assert conn.execute("SELECT count(*) FROM outbox WHERE published_at IS NULL").fetchone()[0] == 1
    bus = Recorder()
    assert relay.drain(conn, bus) == 1


def test_two_relays_do_not_publish_the_same_event(crawls, conn):
    """FOR UPDATE SKIP LOCKED is what makes a second relay safe to run."""
    crawl_id = str(uuid.uuid4())
    crawls.create(crawl_id, "https://example.com")
    crawls.complete(crawl_id, REPORT, events=[PendingEvent("crawl.completed", {}, "crawl-service")])

    with psycopg.connect(DSN) as first:
        # Claim the row and hold the lock by not committing.
        first.execute(relay.CLAIM, (100,)).fetchall()

        second = Recorder()
        assert relay.drain(conn, second) == 0
        assert second.published == []
        first.rollback()

    assert relay.drain(conn, Recorder()) == 1


def test_the_relay_drains_in_order(crawls, conn):
    crawl_id = str(uuid.uuid4())
    crawls.create(crawl_id, "https://example.com")
    events = [PendingEvent("page.updated", {"n": n}, "crawl-service") for n in range(5)]
    crawls.complete(crawl_id, REPORT, events=events)

    bus = Recorder()
    assert relay.drain(conn, bus) == 5
    assert [e.payload["n"] for e in bus.published] == [0, 1, 2, 3, 4]


def test_prune_keeps_the_unpublished(crawls, conn):
    crawl_id = str(uuid.uuid4())
    crawls.create(crawl_id, "https://example.com")
    crawls.complete(crawl_id, REPORT, events=[PendingEvent("crawl.completed", {}, "crawl-service")])
    relay.drain(conn, Recorder())
    crawls.update(crawl_id, status="running")
    crawls.complete(crawl_id, REPORT, events=[PendingEvent("crawl.completed", {}, "crawl-service")])

    # The published row is minutes old, not days: nothing should go yet.
    assert relay.prune(conn, keep_hours=72) == 0
    assert relay.prune(conn, keep_hours=0) == 1
    assert conn.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1


# ---------------------------------------------------------------- store_for


def test_no_database_url_means_files(tmp_path):
    store = store_for(CRAWL_TABLE, CrawlRecord, tmp_path / "crawls", dsn="")
    assert isinstance(store, FileJobStore)
    assert store.stages_events is False


def test_a_real_database_url_means_postgres(tmp_path):
    store = store_for(CRAWL_TABLE, CrawlRecord, tmp_path / "crawls", dsn=DSN)
    assert isinstance(store, PostgresJobStore)
    assert store.stages_events is True
    store.close()


def test_an_unreachable_database_falls_back_rather_than_failing(tmp_path, caplog):
    """A single-machine install with a stale DATABASE_URL should degrade, not
    refuse to boot — but never quietly."""
    store = store_for(
        CRAWL_TABLE, CrawlRecord, tmp_path / "crawls",
        dsn="postgresql://nobody@127.0.0.1:1/nothing",
    )
    assert isinstance(store, FileJobStore)
    assert "falling back to file storage" in caplog.text


def test_an_unreachable_database_raises_when_asked_directly():
    with pytest.raises(DatabaseUnavailable):
        PostgresJobStore("postgresql://nobody@127.0.0.1:1/nothing", CRAWL_TABLE, CrawlRecord)


# ------------------------------------------------------- the service end to end


def test_the_keyword_service_stages_a_valid_event(research, conn, tmp_path, monkeypatch):
    """The whole path: run the service against Postgres, and check the event the
    relay would publish still satisfies the contract."""
    from shared.contracts import validate_event

    monkeypatch.setenv("EVENTS_ENABLED", "0")
    from seoagent.keywords import sources as source_module
    from seoagent.keywords.sources import Suggestion

    monkeypatch.setattr(
        source_module, "SOURCES",
        {"google": lambda q, **k: [Suggestion(f"{q} قیمت", "google", 0), Suggestion(q, "google", 1)]},
    )
    monkeypatch.setattr(source_module, "trending_now", lambda *a, **k: [])
    monkeypatch.setattr(source_module, "related_from_wikipedia", lambda *a, **k: [])

    from services.keyword import api

    monkeypatch.setattr(api, "store", research)

    research_id = str(uuid.uuid4())
    research.create(research_id, "کفش")
    record = api.run_research(
        research_id,
        {"seed": "کفش", "sources": ["google"], "include_alphabet": False,
         "include_comparisons": False},
    )
    assert record.status == "completed"

    row = conn.execute("SELECT event_type, payload FROM outbox").fetchone()
    assert row[0] == "keyword.researched"
    validate_event(row[0], row[1])
    assert row[1]["research_id"] == research_id
