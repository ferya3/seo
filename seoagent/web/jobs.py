"""Background job runner for the dashboard.

Crawling a site takes tens of seconds, so the browser starts a job, gets an id
back immediately, and polls for progress.

Finished results are also written to disk. On a server the process restarts on
every deploy and reboot, and losing every report each time is a bad trade for
the small amount of code this costs.
"""

from __future__ import annotations

import json
import threading
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
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
    """Job registry with optional disk persistence for finished results."""

    def __init__(self, max_jobs: int = 40, storage_dir: Path | str | None = None):
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self.max_jobs = max_jobs
        self.storage_dir = Path(storage_dir) if storage_dir else None
        if self.storage_dir:
            try:
                self.storage_dir.mkdir(parents=True, exist_ok=True)
                self._load_from_disk()
            except OSError:
                # An unwritable data dir must not stop the dashboard from
                # running — it just means reports live in memory only.
                self.storage_dir = None

    # ------------------------------------------------------------ persistence

    def _path(self, job_id: str) -> Path | None:
        return self.storage_dir / f"{job_id}.json" if self.storage_dir else None

    def _load_from_disk(self) -> None:
        assert self.storage_dir is not None
        files = sorted(self.storage_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for path in files[-self.max_jobs:]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                meta = payload["job"]
            except (OSError, ValueError, KeyError):
                continue
            job = Job(
                id=meta["id"],
                kind=meta["kind"],
                label=meta["label"],
                status="done",
                message="تمام شد",
                done=meta.get("done", 0),
                total=meta.get("total", 0),
                result=payload.get("result"),
                created_at=meta.get("created_at", ""),
            )
            self._jobs[job.id] = job
            self._order.append(job.id)

    def _persist(self, job: Job) -> None:
        path = self._path(job.id)
        if path is None or job.result is None:
            return

        # Serialise and write the temp file outside the lock — a large report
        # shouldn't stall progress polling. Only the rename needs to be
        # serialised against eviction.
        temp = path.with_suffix(".tmp")
        try:
            temp.write_text(
                json.dumps({"job": job.to_dict(), "result": job.result}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            return

        with self._lock:
            # A job evicted while its thread was still working must not leave a
            # file behind. Checking and renaming under the same lock that
            # eviction holds is what makes that airtight — checking first and
            # renaming after would leave a window for an orphan.
            if job.id not in self._jobs:
                temp.unlink(missing_ok=True)
                return
            try:
                # Rename rather than write in place, so a crash mid-write can't
                # leave a truncated report that fails to parse on next boot.
                temp.replace(path)
            except OSError:
                temp.unlink(missing_ok=True)

    def _forget(self, job_id: str) -> None:
        path = self._path(job_id)
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    # ------------------------------------------------------------------- api

    def create(self, kind: str, label: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > self.max_jobs:
                evicted = self._order.pop(0)
                self._jobs.pop(evicted, None)
                self._forget(evicted)
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
                # Persist before flipping to "done", so a client that sees
                # "done" and asks for the report always finds it on disk too.
                self._persist(job)
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
