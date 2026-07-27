"""Channels and deliveries.

Not built on `FileJobStore` like the other services: a notification channel is
not a job. It is a small piece of configuration that outlives every job, and a
delivery is an append-only record whose whole value is the unique constraint on
it — send-once is enforced by the database, not by remembering.

Postgres only. "Keep the subscriptions in this process" is not a mode a
notification service can honestly offer: the first restart would silently stop
telling anyone anything.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from shared.db import DatabaseUnavailable, UnknownTenant, _psycopg


@dataclass
class Channel:
    id: str
    tenant_id: str | None
    project_id: str | None
    kind: str                      # webhook | email
    target: str
    events: list[str] = field(default_factory=list)
    active: bool = True
    secret: str | None = None
    created_at: str = ""

    def to_dict(self, reveal_secret: bool = False) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "target": self.target,
            "events": self.events,
            "active": self.active,
            # The secret is shown once, when it is created, and never again.
            # A listing endpoint that hands out signing secrets is a listing
            # endpoint that leaks them into logs and screenshots.
            "secret": self.secret if reveal_secret else None,
            "has_secret": bool(self.secret),
            "created_at": self.created_at,
        }

    def as_row(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "target": self.target,
            "events": self.events, "active": self.active, "secret": self.secret,
        }


class NotificationStore:
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

    # -------------------------------------------------------------- channels

    def add_channel(
        self, kind: str, target: str, tenant_id: str | None = None,
        project_id: str | None = None, events: list[str] | None = None,
        secret: str | None = None,
    ) -> Channel:
        psycopg, Jsonb = _psycopg()
        channel_id = str(uuid.uuid4())
        try:
            with self.pool.connection() as conn:
                conn.execute(
                    "INSERT INTO notification_channels "
                    "(id, tenant_id, project_id, kind, target, events, secret) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (channel_id, tenant_id, project_id, kind, target,
                     Jsonb(events or []), secret),
                )
        except psycopg.errors.ForeignKeyViolation as exc:
            raise UnknownTenant(
                f"unknown tenant_id {tenant_id!r} or project_id {project_id!r}"
            ) from exc
        return self.get_channel(channel_id, tenant_id)

    def get_channel(self, channel_id: str, tenant_id: str | None = None) -> Channel | None:
        sql = f"{_SELECT} WHERE id = %s"
        params: list[Any] = [channel_id]
        if tenant_id is not None:
            sql += " AND tenant_id = %s"
            params.append(tenant_id)

        with self.pool.connection() as conn:
            row = conn.execute(sql, params).fetchone()
        return _channel(row) if row else None

    def channels_for(self, tenant_id: str | None) -> list[Channel]:
        """Every channel a tenant owns.

        `tenant_id=None` returns the channels that belong to nobody — the
        single-machine install, where there is one user and no tenants — and
        emphatically not every tenant's.
        """
        sql = f"{_SELECT} WHERE tenant_id IS NOT DISTINCT FROM %s ORDER BY created_at"
        with self.pool.connection() as conn:
            return [_channel(row) for row in conn.execute(sql, (tenant_id,)).fetchall()]

    def delete_channel(self, channel_id: str, tenant_id: str | None = None) -> bool:
        sql = "DELETE FROM notification_channels WHERE id = %s"
        params: list[Any] = [channel_id]
        if tenant_id is not None:
            sql += " AND tenant_id = %s"
            params.append(tenant_id)
        sql += " RETURNING id"

        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchone() is not None

    # ------------------------------------------------------------ deliveries

    def claim(self, channel_id: str, event_id: str, event_type: str) -> bool:
        """Reserve this (channel, event) pair, or say it is already taken.

        The unique index does the work. Two workers handling the same
        redelivered event both try to insert; one wins, the other gets a
        conflict and stops — which is the difference between one email and
        two.
        """
        with self.pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO notification_deliveries "
                "(id, channel_id, event_id, event_type, status) "
                "VALUES (%s, %s, %s, %s, 'sending') "
                "ON CONFLICT (channel_id, event_id) DO NOTHING RETURNING id",
                (str(uuid.uuid4()), channel_id, event_id, event_type),
            ).fetchone()
        return row is not None

    def finish(self, channel_id: str, event_id: str, ok: bool,
               status: int | None = None, error: str | None = None) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE notification_deliveries SET status = %s, http_status = %s, "
                "error = %s, attempts = attempts + 1, updated_at = now() "
                "WHERE channel_id = %s AND event_id = %s",
                ("sent" if ok else "failed", status, error, channel_id, event_id),
            )

    def release(self, channel_id: str, event_id: str) -> None:
        """Give the pair back so a redelivery can try again.

        Used only for a failure worth retrying. A permanent failure keeps its
        row: that is the record of why nobody was told.
        """
        with self.pool.connection() as conn:
            conn.execute(
                "DELETE FROM notification_deliveries WHERE channel_id = %s AND event_id = %s",
                (channel_id, event_id),
            )

    def deliveries(self, tenant_id: str | None, limit: int = 50) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT d.id, d.channel_id, c.kind, c.target, d.event_type, d.event_id, "
                "       d.status, d.http_status, d.error, d.attempts, d.created_at "
                "FROM notification_deliveries d "
                "JOIN notification_channels c ON c.id = d.channel_id "
                "WHERE c.tenant_id IS NOT DISTINCT FROM %s "
                "ORDER BY d.created_at DESC LIMIT %s",
                (tenant_id, limit),
            ).fetchall()

        return [
            {
                "id": str(row[0]), "channel_id": str(row[1]), "kind": row[2],
                # Deliberately not the full target: a listing is the sort of
                # thing that ends up in a screenshot, and an email address is
                # personal data.
                "target": _mask(row[3]), "event_type": row[4], "event_id": str(row[5]),
                "status": row[6], "http_status": row[7], "error": row[8],
                "attempts": row[9], "created_at": row[10].isoformat() if row[10] else "",
            }
            for row in rows
        ]


_SELECT = (
    "SELECT id, tenant_id, project_id, kind, target, events, active, secret, created_at "
    "FROM notification_channels"
)


def _channel(row: tuple) -> Channel:
    events = row[5]
    return Channel(
        id=str(row[0]),
        tenant_id=str(row[1]) if row[1] else None,
        project_id=str(row[2]) if row[2] else None,
        kind=row[3],
        target=row[4],
        events=json.loads(events) if isinstance(events, str) else (events or []),
        active=bool(row[6]),
        secret=row[7],
        created_at=row[8].isoformat() if row[8] else "",
    )


def _mask(target: str) -> str:
    if "@" in target:
        name, _, domain = target.partition("@")
        return f"{name[:2]}…@{domain}"
    return target.split("?")[0]
