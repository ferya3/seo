"""Postgres-backed job storage with a transactional outbox.

Same interface as `FileJobStore`, one difference that matters: finishing a job
writes the row *and* the event it wants published in a single transaction.
A crash can no longer land between "saved" and "published" — either both
happened or neither did. `shared/relay.py` drains the outbox onto the bus.

Domain tables, not one generic `jobs` table. `crawls` and `research` already
exist in the schema with their own columns, constraints and indexes, and a
`status IN (...)` check that catches a typo at write time is worth more than
the convenience of a single table. What varies between them is described by a
`JobTable` mapping rather than by a subclass per service.

psycopg is an optional import: a deployment with no DATABASE_URL keeps running
file-backed, so the database is an upgrade rather than a new hard dependency.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from datetime import datetime, timezone
from typing import Any, Generic

from .store import FileJobStore, JobRecord, PendingEvent, R

log = logging.getLogger(__name__)


class DatabaseUnavailable(RuntimeError):
    """DATABASE_URL is set but the driver or the server is not reachable."""


class UnknownTenant(ValueError):
    """The job referenced a tenant or project that does not exist.

    Not a server fault: the caller named something real-looking that this
    database has never heard of. Tenants are provisioned by the auth/project
    service, so until that has created the row, work for it cannot be stored.
    Surfaced separately from other errors so the API can answer 400 instead of
    500 — a 500 would tell the caller we are broken when they are the ones
    pointing at nothing.
    """


def _psycopg():
    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ImportError as exc:  # pragma: no cover - exercised by the fallback path
        raise DatabaseUnavailable("psycopg is not installed") from exc
    return psycopg, Jsonb


@dataclass(frozen=True)
class JobTable:
    """How one service's table maps onto a JobRecord.

    `derived` lifts values out of the result document into real columns. They
    are duplicated on purpose: the list endpoint must not have to open every
    JSONB report to show a score, and a partial index cannot be built over a
    field the planner has to deserialise first.
    """

    name: str
    subject_column: str
    result_column: str = "report"
    derived: Mapping[str, Callable[[dict[str, Any]], Any]] = field(default_factory=dict)

    @property
    def columns(self) -> tuple[str, ...]:
        return (
            "id", "tenant_id", "project_id", self.subject_column, "status",
            "error", self.result_column, "created_at", "updated_at",
        )


class PostgresJobStore(Generic[R]):
    """Drop-in replacement for FileJobStore, plus outbox staging."""

    stages_events = True

    def __init__(self, dsn: str, table: JobTable, record_cls: type[R]):
        psycopg, _ = _psycopg()
        from psycopg_pool import ConnectionPool

        self.table = table
        self.record_cls = record_cls
        # `open=False` then `open()` so a broker-style startup race surfaces
        # here as DatabaseUnavailable rather than on the first request.
        self.pool = ConnectionPool(dsn, min_size=1, max_size=8, open=False, timeout=10)
        try:
            self.pool.open(wait=True, timeout=10)
        except Exception as exc:
            raise DatabaseUnavailable(str(exc)) from exc

    def close(self) -> None:
        self.pool.close()

    # ----------------------------------------------------------------- rows

    def _row_to_record(self, row: tuple) -> R:
        id_, tenant_id, project_id, subject, status, error, result, created, updated = row
        return self.record_cls(
            job_id=str(id_),
            subject=subject,
            status=status,
            created_at=_iso(created),
            updated_at=_iso(updated),
            error=error,
            result=result,
            tenant_id=str(tenant_id) if tenant_id else None,
            project_id=str(project_id) if project_id else None,
        )

    @property
    def _select(self) -> str:
        return f"SELECT {', '.join(self.table.columns)} FROM {self.table.name}"

    # ------------------------------------------------------------------ api

    def create(
        self,
        job_id: str,
        subject: str,
        tenant_id: str | None = None,
        project_id: str | None = None,
        **extra: Any,
    ) -> R:
        psycopg, _ = _psycopg()
        try:
            with self.pool.connection() as conn:
                conn.execute(
                    f"INSERT INTO {self.table.name} "
                    f"(id, tenant_id, project_id, {self.table.subject_column}, status) "
                    f"VALUES (%s, %s, %s, %s, 'queued') ON CONFLICT (id) DO NOTHING",
                    (job_id, tenant_id, project_id, subject),
                )
        except psycopg.errors.ForeignKeyViolation as exc:
            raise UnknownTenant(
                f"unknown tenant_id {tenant_id!r} or project_id {project_id!r}"
            ) from exc
        return self.record_cls(
            job_id=job_id, subject=subject, tenant_id=tenant_id, project_id=project_id, **extra
        )

    def get(self, job_id: str, tenant_id: str | None = None) -> R | None:
        """Filtered in SQL rather than after loading: the row must not leave
        the database for a caller who is not allowed to see it."""
        sql, params = f"{self._select} WHERE id = %s", [job_id]
        if tenant_id is not None:
            sql += " AND tenant_id = %s"
            params.append(tenant_id)

        with self.pool.connection() as conn:
            row = conn.execute(sql, params).fetchone()
        return self._row_to_record(row) if row else None

    def update(self, job_id: str, **changes: Any) -> R:
        return self._write(job_id, changes, ())

    def mark_running(self, job_id: str) -> R:
        return self._write(job_id, {"status": "running"}, ())

    def complete(
        self, job_id: str, result: dict[str, Any], events: Sequence[PendingEvent] = ()
    ) -> R:
        return self._write(job_id, {"status": "completed", "result": result, "error": None}, events)

    def fail(self, job_id: str, error: str, events: Sequence[PendingEvent] = ()) -> R:
        return self._write(job_id, {"status": "failed", "error": error}, events)

    def _write(self, job_id: str, changes: dict[str, Any], events: Sequence[PendingEvent]) -> R:
        _, Jsonb = _psycopg()
        sets: list[str] = ["updated_at = now()"]
        values: list[Any] = []

        for key, value in changes.items():
            if key == "result":
                sets.append(f"{self.table.result_column} = %s")
                values.append(Jsonb(value) if value is not None else None)
                for column, extract in self.table.derived.items():
                    sets.append(f"{column} = %s")
                    values.append(extract(value or {}))
            elif key == "subject":
                sets.append(f"{self.table.subject_column} = %s")
                values.append(value)
            else:
                # Only names we generate reach this branch; a caller-supplied
                # column name would be an injection point, so anything outside
                # the record's own fields is refused rather than interpolated.
                if key not in {f.name for f in dataclass_fields(self.record_cls)}:
                    raise KeyError(f"unknown field {key!r}")
                sets.append(f"{key} = %s")
                values.append(value)

        with self.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    f"UPDATE {self.table.name} SET {', '.join(sets)} WHERE id = %s "
                    f"RETURNING {', '.join(self.table.columns)}",
                    (*values, job_id),
                ).fetchone()
                if row is None:
                    raise KeyError(job_id)
                record = self._row_to_record(row)
                # Same transaction as the UPDATE. This is the whole point.
                for event in events:
                    _stage(conn, event, record)
        return record

    def recent(self, limit: int = 25, tenant_id: str | None = None) -> list[R]:
        sql, params = self._select, []
        if tenant_id is not None:
            sql += " WHERE tenant_id = %s"
            params.append(tenant_id)
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)

        with self.pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def count(self) -> int:
        with self.pool.connection() as conn:
            return conn.execute(f"SELECT count(*) FROM {self.table.name}").fetchone()[0]


def _stage(conn, event: PendingEvent, record: JobRecord) -> None:
    _, Jsonb = _psycopg()
    conn.execute(
        "INSERT INTO outbox (event_id, event_type, producer, tenant_id, project_id, "
        "correlation_id, causation_id, payload) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING",
        (
            event.event_id,
            event.type,
            event.producer,
            record.tenant_id,
            record.project_id,
            event.correlation_id or record.job_id,
            event.causation_id,
            Jsonb(event.payload),
        ),
    )


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value or "")


# ------------------------------------------------------------------ selection


def store_for(
    table: JobTable,
    record_cls: type[R],
    directory: str | os.PathLike[str],
    dsn: str | None = None,
) -> FileJobStore[R] | PostgresJobStore[R]:
    """Postgres when DATABASE_URL is set and reachable, files otherwise.

    Falling back rather than failing is deliberate: the single-machine install
    described in the README has no database, and it must keep working. The
    fallback is logged loudly because losing the outbox silently would be much
    worse than losing it noisily.
    """
    dsn = dsn if dsn is not None else os.environ.get("DATABASE_URL", "")
    if not dsn:
        return FileJobStore(directory, record_cls)
    try:
        return PostgresJobStore(dsn, table, record_cls)
    except DatabaseUnavailable:
        log.exception("DATABASE_URL is set but unusable — falling back to file storage at %s",
                      directory)
        return FileJobStore(directory, record_cls)
