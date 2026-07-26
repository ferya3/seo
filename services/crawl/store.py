"""Crawl-specific view over the shared job store."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.store import FileJobStore, JobRecord


@dataclass
class CrawlRecord(JobRecord):
    """A crawl. `subject` is the start URL.

    The API speaks `crawl_id` / `start_url` rather than the store's generic
    `job_id` / `subject`, because those are what the event contract and the
    public API use — the generic names stop at the storage boundary.
    """

    @property
    def crawl_id(self) -> str:
        return self.job_id

    @property
    def start_url(self) -> str:
        return self.subject

    @property
    def report(self) -> dict[str, Any] | None:
        return self.result

    def summary(self) -> dict[str, Any]:
        report = self.result or {}
        return {
            "crawl_id": self.job_id,
            "start_url": self.subject,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "overall_score": report.get("overall_score"),
            "total_issues": report.get("stats", {}).get("total_issues"),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.summary(), "report": self.result}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CrawlRecord:
        return cls(
            job_id=raw.get("job_id") or raw["crawl_id"],
            subject=raw.get("subject") or raw["start_url"],
            status=raw.get("status", "completed"),
            created_at=raw.get("created_at", ""),
            updated_at=raw.get("updated_at", ""),
            error=raw.get("error"),
            result=raw.get("result") if "result" in raw else raw.get("report"),
        )


class CrawlStore(FileJobStore[CrawlRecord]):
    def __init__(self, directory: Path | str):
        super().__init__(directory, CrawlRecord)
