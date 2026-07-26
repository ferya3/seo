"""Crawl result storage.

File-backed on purpose for this stage: it is durable across restarts, needs no
migration to stand up, and keeps the read path (`GET /v1/crawls/{id}`) honest —
whatever replaces it later has to satisfy the same three methods.

Swapping in Postgres means reimplementing this class against
`infra/db/migrations/0001_core.sql`; nothing outside it knows how results are
stored.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CrawlRecord:
    crawl_id: str
    start_url: str
    status: str = "queued"          # queued | running | completed | failed
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    error: str | None = None
    report: dict[str, Any] | None = None

    def summary(self) -> dict[str, Any]:
        report = self.report or {}
        return {
            "crawl_id": self.crawl_id,
            "start_url": self.start_url,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "overall_score": report.get("overall_score"),
            "total_issues": report.get("stats", {}).get("total_issues"),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.summary(), "report": self.report}


class CrawlStore:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        self._lock = threading.Lock()
        self._memory: dict[str, CrawlRecord] = {}
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._writable = True
        except OSError:
            # Still serves from memory — a read-only volume degrades the service
            # rather than stopping it.
            self._writable = False

    # ----------------------------------------------------------------- files

    def _path(self, crawl_id: str) -> Path:
        return self.directory / f"{crawl_id}.json"

    def _write(self, record: CrawlRecord) -> None:
        if not self._writable:
            return
        path = self._path(record.crawl_id)
        temp = path.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(record.to_dict(), ensure_ascii=False), encoding="utf-8")
            temp.replace(path)
        except OSError:
            temp.unlink(missing_ok=True)

    def _read(self, crawl_id: str) -> CrawlRecord | None:
        path = self._path(crawl_id)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return CrawlRecord(
            crawl_id=raw["crawl_id"],
            start_url=raw["start_url"],
            status=raw.get("status", "completed"),
            created_at=raw.get("created_at", ""),
            updated_at=raw.get("updated_at", ""),
            error=raw.get("error"),
            report=raw.get("report"),
        )

    # ------------------------------------------------------------------- api

    def create(self, crawl_id: str, start_url: str) -> CrawlRecord:
        record = CrawlRecord(crawl_id=crawl_id, start_url=start_url)
        with self._lock:
            self._memory[crawl_id] = record
        self._write(record)
        return record

    def get(self, crawl_id: str) -> CrawlRecord | None:
        with self._lock:
            record = self._memory.get(crawl_id)
        if record is not None:
            return record
        record = self._read(crawl_id)
        if record is not None:
            with self._lock:
                self._memory[crawl_id] = record
        return record

    def _transition(self, crawl_id: str, **changes: Any) -> CrawlRecord:
        record = self.get(crawl_id)
        if record is None:
            raise KeyError(crawl_id)
        for key, value in changes.items():
            setattr(record, key, value)
        record.updated_at = _now()
        self._write(record)
        return record

    def mark_running(self, crawl_id: str) -> CrawlRecord:
        return self._transition(crawl_id, status="running")

    def complete(self, crawl_id: str, report: dict[str, Any]) -> CrawlRecord:
        return self._transition(crawl_id, status="completed", report=report, error=None)

    def fail(self, crawl_id: str, error: str) -> CrawlRecord:
        return self._transition(crawl_id, status="failed", error=error)

    def recent(self, limit: int = 25) -> list[CrawlRecord]:
        if not self._writable:
            with self._lock:
                records = list(self._memory.values())
            return sorted(records, key=lambda r: r.created_at, reverse=True)[:limit]

        paths = sorted(self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        found = [self._read(p.stem) for p in paths[:limit]]
        return [r for r in found if r is not None]

    def count(self) -> int:
        if not self._writable:
            return len(self._memory)
        return sum(1 for _ in self.directory.glob("*.json"))
