"""Background job runner for the dashboard.

Crawling a site takes tens of seconds, so the browser starts a job, gets an id
back immediately, and polls for progress.
"""

from __future__ import annotations

import threading
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Job:
    id: str
    kind: str                       # "audit" | "keywords"
    label: str
    status: str = "queued"          # queued | running | done | error
    message: str = "در صف اجرا"
    done: int = 0
    total: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def to_dict(self) -> dict[str, Any]:
        percent = 0
        if self.status == "done":
            percent = 100
        elif self.total:
            percent = min(99, int(self.done / self.total * 100))
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "message": self.message,
            "done": self.done,
            "total": self.total,
            "percent": percent,
            "error": self.error,
            "created_at": self.created_at,
        }


class JobStore:
    """In-memory job registry. The dashboard is a single-user local tool, so a
    dict plus a lock is the right amount of machinery."""

    def __init__(self, max_jobs: int = 40):
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self.max_jobs = max_jobs

    def create(self, kind: str, label: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > self.max_jobs:
                self._jobs.pop(self._order.pop(0), None)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 12) -> list[Job]:
        with self._lock:
            ids = list(reversed(self._order))[:limit]
            return [self._jobs[i] for i in ids if i in self._jobs]

    def run(self, job: Job, work: Callable[[Job], dict[str, Any]]) -> None:
        def target() -> None:
            job.status = "running"
            job.message = "شروع شد"
            try:
                job.result = work(job)
                job.status = "done"
                job.message = "تمام شد"
            except Exception as exc:  # noqa: BLE001 - report to the UI, don't crash the server
                job.status = "error"
                job.error = f"{type(exc).__name__}: {exc}"
                job.message = "با خطا متوقف شد"
                traceback.print_exc()

        threading.Thread(target=target, daemon=True, name=f"job-{job.id}").start()

    @staticmethod
    def progress(job: Job) -> Callable[[str, int, int], None]:
        def report(message: str, done: int, total: int) -> None:
            job.message = message
            job.done = done
            job.total = total

        return report
