"""Running an audit on a cadence, and the arithmetic that decides when.

With notifications in place this is what turns the tool from "run it when you
remember" into "it tells you". The interesting part is not the ticker; it is
what "every Monday at nine" means, which is a question with several wrong
answers:

  * Nine in *whose* morning. Stored with a timezone and computed in it, so a
    schedule keeps meaning nine o'clock after a clock change rather than
    drifting an hour twice a year.
  * The 31st of a month with thirty days. Clamped to the last day rather than
    skipped, because "the 31st" from someone setting up a monthly report means
    "the end of the month".
  * A machine that was off for a week. The next run moves to the next future
    occurrence instead of firing seven times to catch up — nobody wants seven
    identical reports, and the point of a schedule is the next one.

The timing is a pure function of its arguments, which is why it is separate
from the store: every one of those decisions is testable without a clock.

Gregorian only. A Jalali cadence ("the first of every Persian month") is a
real thing to want here and is not implemented; saying so beats a `month` that
quietly means something else than the reader expects.
"""

from __future__ import annotations

import calendar
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from shared.db import DatabaseUnavailable, UnknownTenant, _psycopg

CADENCES = ("daily", "weekly", "monthly")
DEFAULT_TIMEZONE = "Asia/Tehran"

# A crawl is minutes of someone else's bandwidth; hourly is not a cadence this
# offers, and the shortest gap between two runs is a day.
MIN_INTERVAL = timedelta(hours=23)


class InvalidSchedule(ValueError):
    """The cadence cannot be turned into a series of times."""


@dataclass
class Schedule:
    id: str
    tenant_id: str | None
    project_id: str | None
    goal: str
    inputs: dict[str, Any]
    cadence: str
    hour: int = 9
    weekday: int = 0                # Monday, when cadence is weekly
    day_of_month: int = 1           # when cadence is monthly
    tz: str = DEFAULT_TIMEZONE
    active: bool = True
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    runs: int = 0
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "inputs": self.inputs,
            "cadence": self.cadence,
            "hour": self.hour,
            "weekday": self.weekday,
            "day_of_month": self.day_of_month,
            "timezone": self.tz,
            "active": self.active,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "runs": self.runs,
            "created_at": self.created_at,
        }


# ------------------------------------------------------------------ the timing


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidSchedule(f"unknown timezone {name!r}") from exc


def next_run(
    cadence: str,
    hour: int = 9,
    weekday: int = 0,
    day_of_month: int = 1,
    tz: str = DEFAULT_TIMEZONE,
    after: datetime | None = None,
) -> datetime:
    """The next time this schedule should fire, in UTC.

    Always strictly after `after`, so calling it with the moment a run started
    cannot produce that same moment again and loop.
    """
    if cadence not in CADENCES:
        raise InvalidSchedule(f"unknown cadence {cadence!r}; have {CADENCES}")
    if not 0 <= hour <= 23:
        raise InvalidSchedule(f"hour must be 0-23, got {hour}")

    local_zone = zone(tz)
    moment = (after or datetime.now(timezone.utc)).astimezone(local_zone)

    if cadence == "daily":
        candidate = moment.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate <= moment:
            candidate += timedelta(days=1)

    elif cadence == "weekly":
        if not 0 <= weekday <= 6:
            raise InvalidSchedule(f"weekday must be 0-6, got {weekday}")
        candidate = moment.replace(hour=hour, minute=0, second=0, microsecond=0)
        ahead = (weekday - candidate.weekday()) % 7
        candidate += timedelta(days=ahead)
        if candidate <= moment:
            candidate += timedelta(days=7)

    else:
        if not 1 <= day_of_month <= 31:
            raise InvalidSchedule(f"day_of_month must be 1-31, got {day_of_month}")
        candidate = _monthly(moment, hour, day_of_month)

    # Built in local time, then converted: doing the arithmetic in UTC is what
    # makes a schedule drift by an hour when the clocks change.
    return candidate.astimezone(timezone.utc)


def _monthly(moment: datetime, hour: int, day_of_month: int) -> datetime:
    year, month = moment.year, moment.month
    for _ in range(2):
        # The 31st of a thirty-day month is the 30th, not a month skipped:
        # "the 31st" from someone setting up a monthly report means the end.
        day = min(day_of_month, calendar.monthrange(year, month)[1])
        candidate = moment.replace(
            year=year, month=month, day=day, hour=hour, minute=0, second=0, microsecond=0
        )
        if candidate > moment:
            return candidate
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    raise InvalidSchedule("could not find a next monthly occurrence")   # pragma: no cover


def catch_up(schedule: Schedule, now: datetime | None = None) -> datetime:
    """Where a schedule that fell behind should point next.

    A machine off for a week comes back to one run, not seven. The point of a
    schedule is the next report; seven identical ones are a mailbox full of
    noise and seven crawls of someone's site.
    """
    now = now or datetime.now(timezone.utc)
    return next_run(
        schedule.cadence, schedule.hour, schedule.weekday, schedule.day_of_month,
        schedule.tz, after=now,
    )


def workflow_request(schedule: Schedule) -> dict[str, Any]:
    """The event a due schedule turns into.

    A fresh workflow id every time: two runs of one schedule are two audits,
    and reusing the id would make the second one look like a redelivery of the
    first and be ignored.
    """
    return {
        "workflow_id": str(uuid.uuid4()),
        "goal": schedule.goal,
        "inputs": dict(schedule.inputs or {}),
    }


# ------------------------------------------------------------------ the store


@dataclass
class _OutboxSubject:
    """Adapts a schedule to what `shared.db._stage` expects of a job record."""

    job_id: str
    tenant_id: str | None = None
    project_id: str | None = None
    events: list = field(default_factory=list)


class ScheduleStore:
    def __init__(self, dsn: str):
        _psycopg()
        from psycopg_pool import ConnectionPool

        self.pool = ConnectionPool(dsn, min_size=1, max_size=8, open=False, timeout=10)
        try:
            self.pool.open(wait=True, timeout=10)
        except Exception as exc:
            raise DatabaseUnavailable(str(exc)) from exc

    def close(self) -> None:
        self.pool.close()

    def create(
        self, goal: str, inputs: dict[str, Any], cadence: str, hour: int = 9,
        weekday: int = 0, day_of_month: int = 1, tz: str = DEFAULT_TIMEZONE,
        tenant_id: str | None = None, project_id: str | None = None,
    ) -> Schedule:
        psycopg, Jsonb = _psycopg()
        first = next_run(cadence, hour, weekday, day_of_month, tz)
        schedule_id = str(uuid.uuid4())

        try:
            with self.pool.connection() as conn:
                conn.execute(
                    "INSERT INTO schedules (id, tenant_id, project_id, goal, inputs, cadence, "
                    "hour, weekday, day_of_month, timezone, next_run_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (schedule_id, tenant_id, project_id, goal, Jsonb(inputs), cadence,
                     hour, weekday, day_of_month, tz, first),
                )
        except psycopg.errors.ForeignKeyViolation as exc:
            raise UnknownTenant(
                f"unknown tenant_id {tenant_id!r} or project_id {project_id!r}"
            ) from exc
        return self.get(schedule_id, tenant_id)

    def get(self, schedule_id: str, tenant_id: str | None = None) -> Schedule | None:
        sql = f"{_SELECT} WHERE id = %s"
        params: list[Any] = [schedule_id]
        if tenant_id is not None:
            sql += " AND tenant_id = %s"
            params.append(tenant_id)

        with self.pool.connection() as conn:
            row = conn.execute(sql, params).fetchone()
        return _schedule(row) if row else None

    def owned_by(self, tenant_id: str | None) -> list[Schedule]:
        sql = f"{_SELECT} WHERE tenant_id IS NOT DISTINCT FROM %s ORDER BY created_at"
        with self.pool.connection() as conn:
            return [_schedule(row) for row in conn.execute(sql, (tenant_id,)).fetchall()]

    def delete(self, schedule_id: str, tenant_id: str | None = None) -> bool:
        sql = "DELETE FROM schedules WHERE id = %s"
        params: list[Any] = [schedule_id]
        if tenant_id is not None:
            sql += " AND tenant_id = %s"
            params.append(tenant_id)
        sql += " RETURNING id"

        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchone() is not None

    def set_active(self, schedule_id: str, active: bool, tenant_id: str | None = None) -> bool:
        sql = "UPDATE schedules SET active = %s, updated_at = now() WHERE id = %s"
        params: list[Any] = [active, schedule_id]
        if tenant_id is not None:
            sql += " AND tenant_id = %s"
            params.append(tenant_id)
        sql += " RETURNING id"

        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchone() is not None

    def fire_due(self, now: datetime | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Turn every due schedule into a staged `workflow.requested`.

        One transaction per batch: the event is staged in the outbox and
        `next_run_at` moved in the same commit, so a crash cannot leave a
        schedule that fired without an event, or an event without the schedule
        having moved — which would fire it again on the next tick.

        `FOR UPDATE SKIP LOCKED` for the same reason the relay uses it: two
        schedulers can run, and the one that does not get the row moves on
        instead of waiting to do work that is already being done.
        """
        from shared.contracts import ContractError, validate_event
        from shared.db import _stage
        from shared.store import PendingEvent

        now = now or datetime.now(timezone.utc)
        fired: list[dict[str, Any]] = []

        with self.pool.connection() as conn, conn.transaction():
            rows = conn.execute(
                f"{_SELECT} WHERE active AND next_run_at <= %s "
                "ORDER BY next_run_at FOR UPDATE SKIP LOCKED LIMIT %s",
                (now, limit),
            ).fetchall()

            for row in rows:
                schedule = _schedule(row)
                payload = workflow_request(schedule)
                try:
                    validate_event("workflow.requested", payload)
                except ContractError:
                    # A schedule whose inputs no longer satisfy the contract is
                    # deactivated rather than retried every minute forever.
                    conn.execute(
                        "UPDATE schedules SET active = false, last_error = %s, updated_at = now() "
                        "WHERE id = %s",
                        ("inputs no longer valid for workflow.requested", schedule.id),
                    )
                    continue

                _stage(
                    conn,
                    PendingEvent(
                        type="workflow.requested", payload=payload, producer="scheduler",
                        correlation_id=payload["workflow_id"],
                    ),
                    _OutboxSubject(schedule.id, schedule.tenant_id, schedule.project_id),
                )
                conn.execute(
                    "UPDATE schedules SET next_run_at = %s, last_run_at = %s, "
                    "runs = runs + 1, last_error = NULL, updated_at = now() WHERE id = %s",
                    (catch_up(schedule, now), now, schedule.id),
                )
                fired.append({"schedule_id": schedule.id, **payload})

        return fired


_SELECT = (
    "SELECT id, tenant_id, project_id, goal, inputs, cadence, hour, weekday, day_of_month, "
    "timezone, active, next_run_at, last_run_at, runs, created_at FROM schedules"
)


def _schedule(row: tuple) -> Schedule:
    return Schedule(
        id=str(row[0]),
        tenant_id=str(row[1]) if row[1] else None,
        project_id=str(row[2]) if row[2] else None,
        goal=row[3],
        inputs=row[4] or {},
        cadence=row[5],
        hour=row[6],
        weekday=row[7],
        day_of_month=row[8],
        tz=row[9],
        active=bool(row[10]),
        next_run_at=row[11],
        last_run_at=row[12],
        runs=row[13],
        created_at=row[14].isoformat() if row[14] else "",
    )
