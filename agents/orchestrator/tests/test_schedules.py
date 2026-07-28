"""When "every Monday at nine" actually means.

The timing is a pure function, so all of it is pinned here without a clock —
including the three answers that are easy to get wrong: which nine o'clock, the
31st of a short month, and what a machine that was off for a week should do
when it comes back.

The firing half needs Postgres: staging the event and moving `next_run_at` in
one transaction is the property under test, and a fake cannot demonstrate it.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from agents.orchestrator import schedules  # noqa: E402

TEHRAN = ZoneInfo("Asia/Tehran")


def at(text: str) -> datetime:
    """A moment in Tehran, written the way a person would say it."""
    return datetime.fromisoformat(text).replace(tzinfo=TEHRAN)


def local(moment: datetime) -> datetime:
    return moment.astimezone(TEHRAN)


# ------------------------------------------------------------------- daily


def test_daily_fires_at_the_hour_that_was_asked_for():
    result = local(schedules.next_run("daily", hour=9, after=at("2026-07-27T06:00")))
    assert (result.hour, result.day) == (9, 27)


def test_a_time_already_past_today_moves_to_tomorrow():
    result = local(schedules.next_run("daily", hour=9, after=at("2026-07-27T09:30")))
    assert (result.hour, result.day) == (9, 28)


def test_the_hour_is_the_users_hour_not_the_servers():
    """Computing in UTC is what makes a schedule drift by an hour when clocks
    change; nine o'clock has to keep meaning nine o'clock."""
    utc = schedules.next_run("daily", hour=9, tz="Asia/Tehran", after=at("2026-07-27T06:00"))

    assert utc.tzinfo == timezone.utc
    assert local(utc).hour == 9
    assert utc.hour == 5              # Tehran is UTC+3:30 in July 2026


def test_a_schedule_in_another_timezone_keeps_its_own_hour():
    utc = schedules.next_run(
        "daily", hour=9, tz="Europe/Berlin",
        after=datetime(2026, 7, 27, 4, 0, tzinfo=timezone.utc),
    )
    assert utc.astimezone(ZoneInfo("Europe/Berlin")).hour == 9


def test_an_unknown_timezone_is_refused():
    with pytest.raises(schedules.InvalidSchedule):
        schedules.next_run("daily", tz="Mars/Olympus")


# ------------------------------------------------------------------- weekly


def test_weekly_lands_on_the_weekday_asked_for():
    # 2026-07-27 is a Monday.
    result = local(schedules.next_run("weekly", hour=9, weekday=2, after=at("2026-07-27T06:00")))
    assert result.weekday() == 2 and result.day == 29


def test_the_same_weekday_later_today_still_fires_today():
    result = local(schedules.next_run("weekly", hour=9, weekday=0, after=at("2026-07-27T06:00")))
    assert result.day == 27


def test_the_same_weekday_already_past_waits_a_week():
    result = local(schedules.next_run("weekly", hour=9, weekday=0, after=at("2026-07-27T10:00")))
    assert result.weekday() == 0 and result.day == 3      # the next Monday


def test_an_impossible_weekday_is_refused():
    with pytest.raises(schedules.InvalidSchedule):
        schedules.next_run("weekly", weekday=9)


# ------------------------------------------------------------------ monthly


def test_monthly_fires_on_the_day_asked_for():
    result = local(schedules.next_run("monthly", hour=9, day_of_month=15,
                                      after=at("2026-07-01T06:00")))
    assert (result.month, result.day) == (7, 15)


def test_the_thirty_first_of_a_thirty_day_month_is_the_thirtieth():
    """"The 31st" from someone setting up a monthly report means the end of the
    month, not "skip the months that are too short"."""
    result = local(schedules.next_run("monthly", hour=9, day_of_month=31,
                                      after=at("2026-09-01T06:00")))
    assert (result.month, result.day) == (9, 30)


def test_february_is_clamped_too():
    result = local(schedules.next_run("monthly", hour=9, day_of_month=30,
                                      after=at("2026-02-01T06:00")))
    assert (result.month, result.day) == (2, 28)


def test_a_day_already_past_moves_to_next_month():
    result = local(schedules.next_run("monthly", hour=9, day_of_month=1,
                                      after=at("2026-07-27T06:00")))
    assert (result.month, result.day) == (8, 1)


def test_december_rolls_into_january():
    result = local(schedules.next_run("monthly", hour=9, day_of_month=1,
                                      after=at("2026-12-15T06:00")))
    assert (result.year, result.month) == (2027, 1)


# ------------------------------------------------------------------ the rest


def test_the_next_run_is_always_in_the_future():
    """Called with the moment a run started, it must not return that moment
    again — that is an infinite loop of one schedule firing forever."""
    now = at("2026-07-27T09:00")
    for cadence in schedules.CADENCES:
        assert schedules.next_run(cadence, hour=9, after=now) > now


def test_an_unknown_cadence_is_refused():
    with pytest.raises(schedules.InvalidSchedule):
        schedules.next_run("hourly")


def test_a_machine_off_for_a_week_comes_back_to_one_run(monkeypatch):
    """Not seven. Seven identical reports is a full mailbox and seven crawls
    of somebody's site."""
    schedule = schedules.Schedule(
        id="s-1", tenant_id=None, project_id=None, goal="site_audit", inputs={},
        cadence="daily", hour=9, next_run_at=at("2026-07-20T09:00"),
    )
    resumed = local(schedules.catch_up(schedule, now=at("2026-07-27T12:00")))

    assert (resumed.day, resumed.hour) == (28, 9)


def test_each_run_of_a_schedule_is_a_new_workflow():
    """Reusing the id would make the second run look like a redelivery of the
    first and be ignored."""
    schedule = schedules.Schedule(
        id="s-1", tenant_id=None, project_id=None, goal="site_audit",
        inputs={"start_url": "https://site.test/"}, cadence="daily",
    )
    first, second = schedules.workflow_request(schedule), schedules.workflow_request(schedule)

    assert first["workflow_id"] != second["workflow_id"]
    assert first["inputs"] == {"start_url": "https://site.test/"}


def test_the_request_does_not_share_the_schedules_inputs_dict():
    """A workflow that mutated its inputs would rewrite the schedule."""
    schedule = schedules.Schedule(
        id="s-1", tenant_id=None, project_id=None, goal="site_audit",
        inputs={"start_url": "https://site.test/"}, cadence="daily",
    )
    request = schedules.workflow_request(schedule)
    request["inputs"]["start_url"] = "https://elsewhere.test/"

    assert schedule.inputs["start_url"] == "https://site.test/"


# ------------------------------------------------------------ against postgres


DSN = os.environ.get("TEST_DATABASE_URL", "")
psycopg = pytest.importorskip("psycopg")
database = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not set")


@pytest.fixture
def store():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("TRUNCATE schedules, outbox CASCADE")
    s = schedules.ScheduleStore(DSN)
    yield s
    s.close()


@pytest.fixture
def conn():
    with psycopg.connect(DSN, autocommit=True) as connection:
        yield connection


AUDIT = {"start_url": "https://site.test/", "seed": "کفش"}


@database
def test_a_new_schedule_knows_when_it_next_runs(store):
    schedule = store.create("site_audit", AUDIT, "daily", hour=9)

    assert schedule.next_run_at > datetime.now(timezone.utc)
    assert schedule.runs == 0


@database
def test_a_due_schedule_stages_a_contract_valid_request(store, conn):
    store.create("site_audit", AUDIT, "daily", hour=9)
    fired = store.fire_due(now=datetime.now(timezone.utc) + timedelta(days=2))

    assert len(fired) == 1
    row = conn.execute(
        "SELECT event_type, producer, payload FROM outbox"
    ).fetchone()
    assert (row[0], row[1]) == ("workflow.requested", "scheduler")
    assert row[2]["inputs"]["start_url"] == AUDIT["start_url"]

    from shared.contracts import validate_event
    validate_event("workflow.requested", row[2])


@database
def test_firing_moves_the_schedule_forward_in_the_same_commit(store, conn):
    """A crash between the two would fire the same schedule again on the next
    tick, or stage an event the schedule does not know it sent."""
    schedule = store.create("site_audit", AUDIT, "daily", hour=9)
    before = schedule.next_run_at

    store.fire_due(now=before + timedelta(minutes=1))
    after = store.get(schedule.id)

    assert after.next_run_at > before
    assert after.runs == 1
    assert conn.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1


@database
def test_a_schedule_that_is_not_due_does_nothing(store, conn):
    store.create("site_audit", AUDIT, "daily", hour=9)

    assert store.fire_due(now=datetime.now(timezone.utc)) == []
    assert conn.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


@database
def test_an_inactive_schedule_never_fires(store):
    schedule = store.create("site_audit", AUDIT, "daily", hour=9)
    store.set_active(schedule.id, False)

    assert store.fire_due(now=schedule.next_run_at + timedelta(minutes=1)) == []


@database
def test_ticking_twice_in_a_row_fires_once(store):
    """The second tick finds the schedule already moved on, which is what
    stops a fast ticker from starting the same audit repeatedly."""
    schedule = store.create("site_audit", AUDIT, "daily", hour=9)
    moment = schedule.next_run_at + timedelta(minutes=1)

    assert len(store.fire_due(now=moment)) == 1
    assert store.fire_due(now=moment) == []


@database
def test_a_schedule_whose_inputs_became_invalid_is_switched_off(store, conn):
    """Retrying it every minute forever is how a scheduler spends a night
    logging the same rejection."""
    schedule = store.create("site_audit", AUDIT, "daily", hour=9)
    conn.execute(
        "UPDATE schedules SET inputs = '{\"nonsense\": true}'::jsonb WHERE id = %s",
        (schedule.id,),
    )

    assert store.fire_due(now=schedule.next_run_at + timedelta(minutes=1)) == []
    assert store.get(schedule.id).active is False
    assert conn.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


@database
def test_a_schedule_is_only_visible_to_the_tenant_that_owns_it(store, conn):
    tenant = str(uuid.uuid4())
    conn.execute("INSERT INTO tenants (id, name) VALUES (%s, 'acme')", (tenant,))
    schedule = store.create("site_audit", AUDIT, "daily", tenant_id=tenant)

    assert store.get(schedule.id, tenant_id=tenant) is not None
    assert store.get(schedule.id, tenant_id=str(uuid.uuid4())) is None
    assert store.delete(schedule.id, tenant_id=str(uuid.uuid4())) is False
    assert store.delete(schedule.id, tenant_id=tenant) is True


@database
def test_tenancy_travels_onto_the_staged_event(store, conn):
    """The workflow this starts has to belong to the same tenant, or the
    orchestrator will write it against nobody."""
    tenant = str(uuid.uuid4())
    conn.execute("INSERT INTO tenants (id, name) VALUES (%s, 'acme')", (tenant,))
    schedule = store.create("site_audit", AUDIT, "daily", tenant_id=tenant)

    store.fire_due(now=schedule.next_run_at + timedelta(minutes=1))
    assert str(conn.execute("SELECT tenant_id FROM outbox").fetchone()[0]) == tenant


# ------------------------------------------------------------------- the api


@database
def test_the_api_creates_and_lists_a_schedule(store, monkeypatch):
    from fastapi.testclient import TestClient

    from agents.orchestrator import api

    api.reset_store(None, store)
    client = TestClient(api.app)

    created = client.post("/v1/schedules", json={
        "goal": "site_audit",
        "inputs": {"start_url": "https://site.test/", "seed": "کفش"},
        "cadence": "weekly", "hour": 9, "weekday": 6,
    })
    assert created.status_code == 201
    body = created.json()
    assert body["cadence"] == "weekly"
    assert body["next_run_at"] is not None

    assert [s["id"] for s in client.get("/v1/schedules").json()] == [body["id"]]
    api.reset_store(None, None)


@database
def test_the_api_refuses_a_schedule_it_could_never_plan(store):
    """Caught at creation rather than at three in the morning."""
    from fastapi.testclient import TestClient

    from agents.orchestrator import api

    api.reset_store(None, store)
    client = TestClient(api.app)

    assert client.post("/v1/schedules", json={
        "goal": "conquer_google",
        "inputs": {"start_url": "https://site.test/"},
        "cadence": "daily",
    }).status_code == 422
    api.reset_store(None, None)


@database
def test_an_unknown_cadence_never_reaches_the_database(store):
    from fastapi.testclient import TestClient

    from agents.orchestrator import api

    api.reset_store(None, store)
    client = TestClient(api.app)

    assert client.post("/v1/schedules", json={
        "goal": "site_audit", "inputs": {"start_url": "https://site.test/"},
        "cadence": "hourly",
    }).status_code == 422
    assert store.owned_by(None) == []
    api.reset_store(None, None)


@database
def test_a_paused_schedule_keeps_its_settings(store):
    """Pausing rather than deleting: the settings someone worked out and the
    run history are worth keeping."""
    from fastapi.testclient import TestClient

    from agents.orchestrator import api

    api.reset_store(None, store)
    client = TestClient(api.app)
    schedule = store.create("site_audit", AUDIT, "daily", hour=9)

    paused = client.post(f"/v1/schedules/{schedule.id}/pause").json()
    assert paused["active"] is False
    assert paused["cadence"] == "daily"

    assert client.post(
        f"/v1/schedules/{schedule.id}/pause", params={"active": True}
    ).json()["active"] is True
    api.reset_store(None, None)


@database
def test_another_tenants_schedule_is_404_over_http(store, conn):
    from fastapi.testclient import TestClient

    from agents.orchestrator import api

    api.reset_store(None, store)
    client = TestClient(api.app)

    tenant = str(uuid.uuid4())
    conn.execute("INSERT INTO tenants (id, name) VALUES (%s, 'acme')", (tenant,))
    schedule = store.create("site_audit", AUDIT, "daily", tenant_id=tenant)

    assert client.get(f"/v1/schedules/{schedule.id}",
                      params={"tenant_id": tenant}).status_code == 200
    assert client.get(f"/v1/schedules/{schedule.id}",
                      params={"tenant_id": str(uuid.uuid4())}).status_code == 404
    assert client.delete(f"/v1/schedules/{schedule.id}",
                         params={"tenant_id": str(uuid.uuid4())}).status_code == 404
    api.reset_store(None, None)


# --------------------------------------------------------------- the ticker


@database
def test_a_tick_fires_what_is_due_and_nothing_else(store, conn):
    from agents.orchestrator import scheduler

    due = store.create("site_audit", AUDIT, "daily", hour=9)
    conn.execute("UPDATE schedules SET next_run_at = now() - interval '1 minute' WHERE id = %s",
                 (due.id,))
    store.create("site_audit", AUDIT, "monthly", day_of_month=28)

    assert scheduler.tick(store) == 1
    assert conn.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1


@database
def test_a_second_tick_finds_nothing(store, conn):
    """What stops a fast ticker from starting the same audit repeatedly."""
    from agents.orchestrator import scheduler

    schedule = store.create("site_audit", AUDIT, "daily", hour=9)
    conn.execute("UPDATE schedules SET next_run_at = now() - interval '1 minute' WHERE id = %s",
                 (schedule.id,))

    assert scheduler.tick(store) == 1
    assert scheduler.tick(store) == 0
