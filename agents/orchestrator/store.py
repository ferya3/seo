"""Workflow state in Postgres.

Not built on shared.FileJobStore: a workflow is not one job with one result, it
is a row plus an ordered set of steps that advance independently, and the
matching of a completion event to a step is a query rather than a key lookup.
Forcing it into that shape would cost more than it saved.

Postgres only. The file-backed fallback exists so a single machine can run the
audit engine without a database; a workflow spans services and processes, so
"no database" is not a mode it has.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from shared.db import DatabaseUnavailable, _psycopg
from shared.store import PendingEvent

TERMINAL = ("completed", "failed")


@dataclass
class Step:
    position: int
    kind: str
    job_id: str
    status: str
    error: str | None = None
    result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "kind": self.kind,
            "job_id": self.job_id,
            "status": self.status,
            "error": self.error,
            "result": self.result,
        }


@dataclass
class Workflow:
    workflow_id: str
    goal: str
    inputs: dict[str, Any]
    status: str
    steps: list[Step]
    tenant_id: str | None = None
    project_id: str | None = None
    error: str | None = None
    report: dict[str, Any] | None = None
    created_at: str = ""
    updated_at: str = ""

    def step_at(self, position: int) -> Step | None:
        return next((s for s in self.steps if s.position == position), None)

    def next_pending(self) -> Step | None:
        return next((s for s in sorted(self.steps, key=lambda s: s.position)
                     if s.status == "pending"), None)

    def summary(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "goal": self.goal,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "steps": [
                {"position": s.position, "kind": s.kind, "job_id": s.job_id, "status": s.status}
                for s in sorted(self.steps, key=lambda s: s.position)
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "inputs": self.inputs,
            "report": self.report,
            "steps": [s.to_dict() for s in sorted(self.steps, key=lambda s: s.position)],
        }


class WorkflowStore:
    def __init__(self, dsn: str):
        psycopg, _ = _psycopg()
        from psycopg_pool import ConnectionPool

        self.pool = ConnectionPool(dsn, min_size=1, max_size=8, open=False, timeout=10)
        try:
            self.pool.open(wait=True, timeout=10)
        except Exception as exc:
            raise DatabaseUnavailable(str(exc)) from exc

    def close(self) -> None:
        self.pool.close()

    # ---------------------------------------------------------------- writing

    def create(
        self,
        workflow_id: str,
        goal: str,
        inputs: dict[str, Any],
        steps: list[tuple[int, str, str]],
        tenant_id: str | None = None,
        project_id: str | None = None,
    ) -> Workflow:
        """Insert the workflow and its whole plan in one transaction.

        A workflow with no steps would sit in 'queued' forever with nothing to
        say why, so the plan is part of creating it, not a follow-up write.
        """
        _, Jsonb = _psycopg()
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO workflows (id, tenant_id, project_id, goal, inputs, status) "
                "VALUES (%s, %s, %s, %s, %s, 'queued')",
                (workflow_id, tenant_id, project_id, goal, Jsonb(inputs)),
            )
            for position, kind, job_id in steps:
                conn.execute(
                    "INSERT INTO workflow_steps (workflow_id, position, kind, job_id, status) "
                    "VALUES (%s, %s, %s, %s, 'pending')",
                    (workflow_id, position, kind, job_id),
                )
        return self.get(workflow_id)

    def mark_step(
        self,
        job_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> str | None:
        """Record a step's outcome. Returns the workflow id, or None if the job
        belongs to no step — which is normal, because crawls started straight
        through the API also emit crawl.completed."""
        _, Jsonb = _psycopg()
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "UPDATE workflow_steps SET status = %s, result = %s, error = %s, updated_at = now() "
                "WHERE job_id = %s AND status = 'dispatched' RETURNING workflow_id",
                (status, Jsonb(result) if result is not None else None, error, job_id),
            ).fetchone()
        return str(row[0]) if row else None

    def skip_step(self, job_id: str, reason: str, conn=None) -> None:
        """A step with nothing to act on. Distinct from failed on purpose: the
        report should say "there was nothing to check", not "this broke"."""
        sql = ("UPDATE workflow_steps SET status = 'skipped', error = %s, updated_at = now() "
               "WHERE job_id = %s AND status = 'pending'")
        if conn is not None:
            conn.execute(sql, (reason, job_id))
            return
        with self.pool.connection() as own:
            own.execute(sql, (reason, job_id))

    def mark_dispatched(self, job_id: str, conn=None) -> None:
        sql = ("UPDATE workflow_steps SET status = 'dispatched', updated_at = now() "
               "WHERE job_id = %s AND status = 'pending'")
        if conn is not None:
            conn.execute(sql, (job_id,))
            return
        with self.pool.connection() as own:
            own.execute(sql, (job_id,))

    def set_status(
        self,
        workflow_id: str,
        status: str,
        report: dict[str, Any] | None = None,
        error: str | None = None,
        events: list[PendingEvent] | None = None,
        conn=None,
    ) -> Workflow | None:
        """Move the workflow, staging any events in the same transaction — the
        same outbox guarantee the services use.

        `conn` is not an optimisation. When called inside `claim`, using a
        second pooled connection would block on the row lock the first one
        holds, and the caller would wait for itself forever.
        """
        if conn is not None:
            self._set_status(conn, workflow_id, status, report, error, events)
            return _load(conn, workflow_id)

        with self.pool.connection() as own, own.transaction():
            self._set_status(own, workflow_id, status, report, error, events)
            return _load(own, workflow_id)

    def _set_status(self, conn, workflow_id, status, report, error, events) -> None:
        from shared.db import _stage

        _, Jsonb = _psycopg()
        conn.execute(
            "UPDATE workflows SET status = %s, report = COALESCE(%s, report), "
            "error = %s, updated_at = now() WHERE id = %s",
            (status, Jsonb(report) if report is not None else None, error, workflow_id),
        )
        if not events:
            return
        row = conn.execute(
            "SELECT tenant_id, project_id FROM workflows WHERE id = %s", (workflow_id,)
        ).fetchone()
        subject = _OutboxSubject(
            workflow_id,
            str(row[0]) if row and row[0] else None,
            str(row[1]) if row and row[1] else None,
        )
        for event in events:
            _stage(conn, event, subject)

    @contextmanager
    def claim(self, workflow_id: str):
        """Lock the workflow row and yield the connection holding the lock.

        Two completion events can land at the same moment on two orchestrator
        instances; without this both read the same state and both dispatch the
        next step, and the crawl runs twice. Everything done inside must use
        the yielded connection — see set_status.
        """
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("SELECT id FROM workflows WHERE id = %s FOR UPDATE", (workflow_id,))
            yield conn

    def load(self, conn, workflow_id: str) -> Workflow | None:
        """Read through a connection the caller already holds."""
        return _load(conn, workflow_id)

    def save_summary(self, workflow_id: str, summary: dict[str, Any]) -> bool:
        """Replace the summary section of a finished workflow's report.

        Deliberately narrow: it writes one key of the report and only for a
        workflow that already reached a terminal state, so it cannot race the
        state machine — nothing else writes a report after that point. It takes
        no lock for the same reason.

        Returns whether a row was updated, which is False for a workflow that
        does not exist or has not finished.
        """
        _, Jsonb = _psycopg()
        with self.pool.connection() as conn:
            row = conn.execute(
                "UPDATE workflows SET report = jsonb_set("
                "  COALESCE(report, '{}'::jsonb), '{summary}', %s, true"
                "), updated_at = now() "
                "WHERE id = %s AND status = ANY(%s) RETURNING id",
                (Jsonb(summary), workflow_id, list(TERMINAL)),
            ).fetchone()
        return row is not None

    # ---------------------------------------------------------------- reading

    def get(self, workflow_id: str, tenant_id: str | None = None) -> Workflow | None:
        """`tenant_id=None` means "do not filter", which is what the worker
        wants when it reads back a workflow it is already handling. Every HTTP
        read passes the caller's tenant — see the note in api.py."""
        with self.pool.connection() as conn:
            workflow = _load(conn, workflow_id)
        if workflow is None or tenant_id is None:
            return workflow
        return workflow if workflow.tenant_id == tenant_id else None

    def recent(self, limit: int = 25, tenant_id: str | None = None) -> list[Workflow]:
        sql, params = "SELECT id FROM workflows", []
        if tenant_id is not None:
            sql += " WHERE tenant_id = %s"
            params.append(tenant_id)
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)

        with self.pool.connection() as conn:
            ids = [str(r[0]) for r in conn.execute(sql, params).fetchall()]
            return [w for w in (_load(conn, i) for i in ids) if w is not None]

    def count(self) -> int:
        with self.pool.connection() as conn:
            return conn.execute("SELECT count(*) FROM workflows").fetchone()[0]


class _OutboxSubject:
    """Adapts a workflow to what shared.db._stage expects of a job record."""

    def __init__(self, workflow_id: str, tenant_id: str | None, project_id: str | None):
        self.job_id = workflow_id
        self.tenant_id = tenant_id
        self.project_id = project_id


def _load(conn, workflow_id: str) -> Workflow | None:
    row = conn.execute(
        "SELECT id, tenant_id, project_id, goal, inputs, status, error, report, "
        "created_at, updated_at FROM workflows WHERE id = %s",
        (workflow_id,),
    ).fetchone()
    if row is None:
        return None

    steps = [
        Step(position=s[0], kind=s[1], job_id=str(s[2]), status=s[3], error=s[4], result=s[5])
        for s in conn.execute(
            "SELECT position, kind, job_id, status, error, result FROM workflow_steps "
            "WHERE workflow_id = %s ORDER BY position",
            (workflow_id,),
        ).fetchall()
    ]

    return Workflow(
        workflow_id=str(row[0]),
        tenant_id=str(row[1]) if row[1] else None,
        project_id=str(row[2]) if row[2] else None,
        goal=row[3],
        inputs=row[4] if isinstance(row[4], dict) else json.loads(row[4] or "{}"),
        status=row[5],
        error=row[6],
        report=row[7],
        created_at=_iso(row[8]),
        updated_at=_iso(row[9]),
        steps=steps,
    )


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value or "")
