"""Content-analysis view over the shared job store."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.db import JobTable, store_for
from shared.store import FileJobStore, JobRecord


@dataclass
class ContentRecord(JobRecord):
    """One coverage analysis. `subject` is the crawl it was run against.

    The research id lives in the result rather than the subject: a job has one
    subject, and the crawl is the one that decides which site this is about.
    """

    @property
    def analysis_id(self) -> str:
        return self.job_id

    @property
    def crawl_id(self) -> str:
        return self.subject

    @property
    def report(self) -> dict[str, Any] | None:
        return self.result

    def summary(self) -> dict[str, Any]:
        report = self.result or {}
        return {
            "analysis_id": self.job_id,
            "crawl_id": self.subject,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "keywords": report.get("keywords"),
            "covered": report.get("covered"),
            "coverage": report.get("coverage"),
            "gaps": len(report.get("gaps") or []),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.summary(), "report": self.result}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ContentRecord:
        return cls(
            job_id=raw.get("job_id") or raw["analysis_id"],
            subject=raw.get("subject") or raw["crawl_id"],
            status=raw.get("status", "completed"),
            created_at=raw.get("created_at", ""),
            updated_at=raw.get("updated_at", ""),
            error=raw.get("error"),
            result=raw.get("result") if "result" in raw else raw.get("report"),
            tenant_id=raw.get("tenant_id"),
            project_id=raw.get("project_id"),
        )


TABLE = JobTable(
    name="content_analyses",
    subject_column="crawl_id",
    derived={
        "keywords": lambda report: report.get("keywords"),
        "covered": lambda report: report.get("covered"),
        "coverage": lambda report: report.get("coverage"),
    },
)


class ContentStore(FileJobStore[ContentRecord]):
    def __init__(self, directory: Path | str):
        super().__init__(directory, ContentRecord)


def open_store(directory: Path | str, dsn: str | None = None):
    """Postgres when configured, files otherwise. See `shared.db.store_for`."""
    return store_for(TABLE, ContentRecord, directory, dsn)
