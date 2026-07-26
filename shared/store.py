"""Job storage shared by the services — the file-backed implementation.

Every service here has the same storage need: a long-running job whose result
must survive a restart and be readable by id. This was written once inside the
crawl service; the keyword service needs the identical thing, so it lives here
instead of being copied.

`shared/db.py` holds the Postgres implementation of the same interface. This
one is not a stepping stone left behind — it is the supported way to run the
whole thing on one machine with no database, which is what the README describes
and what most single-site installs will use. `shared/db.py:store_for` picks
between them from DATABASE_URL.

The one capability that does not survive the fallback is the transactional
outbox; `stages_events` says so, and callers are required to check it.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generic, TypeVar


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PendingEvent:
    """An event a job wants published as part of finishing.

    `event_id` is generated here rather than at publish time and never changes
    on retry: it becomes the envelope id, which is what makes a consumer's
    idempotency check work when the relay redelivers.
    """

    type: str
    payload: dict[str, Any]
    # The service that produced it, not whoever puts it on the wire. Required
    # rather than defaulted: the relay publishes on everyone's behalf, so if
    # this were optional every event would arrive attributed to the relay.
    producer: str
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass
class JobRecord:
    """One unit of work. Services subclass this to add their own fields."""

    job_id: str
    subject: str                    # what the job is about: a URL, a seed term
    status: str = "queued"          # queued | running | completed | failed
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    error: str | None = None
    result: dict[str, Any] | None = None
    # The multi-tenancy boundary. Carried on every job because a result that
    # cannot say who owns it cannot safely be served to anyone.
    tenant_id: str | None = None
    project_id: str | None = None

    def summary(self) -> dict[str, Any]:
        """Cheap view for list endpoints — no result payload."""
        return {
            "job_id": self.job_id,
            "subject": self.subject,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.summary(), "result": self.result}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]):
        """Tolerant of unknown keys so an older file still loads after a field
        is renamed — a stored report is worth more than schema purity."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})


R = TypeVar("R", bound=JobRecord)


class FileJobStore(Generic[R]):
    """Durable job store. Thread-safe; safe to share between an API process
    and a worker process through the same directory."""

    # A file cannot hold a state change and an event in one transaction, so
    # this store does not pretend to. Callers check this flag and publish
    # directly when it is False — accepting that a crash between the write and
    # the publish loses the event. That is the honest cost of running without
    # a database, and the reason PostgresJobStore exists.
    stages_events = False

    def __init__(self, directory: Path | str, record_cls: type[R]):
        self.directory = Path(directory)
        self.record_cls = record_cls
        self._lock = threading.Lock()
        self._memory: dict[str, R] = {}
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.writable = True
        except OSError:
            # A read-only volume degrades the service to memory-only rather
            # than stopping it from starting.
            self.writable = False

    # ----------------------------------------------------------------- files

    def _path(self, job_id: str) -> Path:
        return self.directory / f"{job_id}.json"

    def _write(self, record: R) -> None:
        if not self.writable:
            return
        path = self._path(record.job_id)
        temp = path.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(record.to_dict(), ensure_ascii=False), encoding="utf-8")
            # Rename rather than write in place: a crash mid-write would
            # otherwise leave a truncated file that fails to parse on next boot.
            temp.replace(path)
        except OSError:
            temp.unlink(missing_ok=True)

    def _read(self, job_id: str) -> R | None:
        path = self._path(job_id)
        if not path.exists():
            return None
        try:
            return self.record_cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, KeyError):
            return None

    # ------------------------------------------------------------------- api

    def create(self, job_id: str, subject: str, **extra: Any) -> R:
        record = self.record_cls(job_id=job_id, subject=subject, **extra)
        with self._lock:
            self._memory[job_id] = record
        self._write(record)
        return record

    def get(self, job_id: str) -> R | None:
        with self._lock:
            record = self._memory.get(job_id)
        if record is not None:
            return record
        # Not in memory: another process may have written it, or this one
        # restarted. Reading through to disk is what makes the API and the
        # worker able to share a store.
        record = self._read(job_id)
        if record is not None:
            with self._lock:
                self._memory[job_id] = record
        return record

    def update(self, job_id: str, **changes: Any) -> R:
        record = self.get(job_id)
        if record is None:
            raise KeyError(job_id)
        for key, value in changes.items():
            setattr(record, key, value)
        record.updated_at = now()
        self._write(record)
        return record

    def mark_running(self, job_id: str) -> R:
        return self.update(job_id, status="running")

    def complete(
        self, job_id: str, result: dict[str, Any], events: Sequence[PendingEvent] = ()
    ) -> R:
        # `events` is accepted and dropped: see `stages_events`. Silently
        # ignoring it is safe only because the caller is required to check
        # that flag and publish for itself.
        return self.update(job_id, status="completed", result=result, error=None)

    def fail(self, job_id: str, error: str, events: Sequence[PendingEvent] = ()) -> R:
        return self.update(job_id, status="failed", error=error)

    def recent(self, limit: int = 25) -> list[R]:
        if not self.writable:
            with self._lock:
                records = list(self._memory.values())
            return sorted(records, key=lambda r: r.created_at, reverse=True)[:limit]

        paths = sorted(self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        found = [self._read(p.stem) for p in paths[:limit]]
        return [r for r in found if r is not None]

    def count(self) -> int:
        if not self.writable:
            return len(self._memory)
        return sum(1 for _ in self.directory.glob("*.json"))
