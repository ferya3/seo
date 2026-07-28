"""Competitor-comparison view over the shared job store."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.db import JobTable, store_for
from shared.store import FileJobStore, JobRecord


@dataclass
class ComparisonRecord(JobRecord):
    """One comparison. `subject` is your own crawl — the site being asked about.

    The competitor crawl ids live in the result rather than the subject: a job
    has one subject, and a comparison is about your site, not about theirs.
    """

    @property
    def comparison_id(self) -> str:
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
            "comparison_id": self.job_id,
            "crawl_id": self.subject,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "compared_against": report.get("compared_against"),
            "behind_on": len(report.get("behind_on") or []),
            "missing_themes": len(report.get("missing_themes") or []),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.summary(), "report": self.result}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ComparisonRecord:
        return cls(
            job_id=raw.get("job_id") or raw["comparison_id"],
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
    name="competitor_comparisons",
    subject_column="crawl_id",
    derived={
        "compared_against": lambda report: report.get("compared_against"),
        "behind_on": lambda report: len(report.get("behind_on") or []),
    },
)


class ComparisonStore(FileJobStore[ComparisonRecord]):
    def __init__(self, directory: Path | str):
        super().__init__(directory, ComparisonRecord)


def open_store(directory: Path | str, dsn: str | None = None):
    """Postgres when configured, files otherwise. See `shared.db.store_for`."""
    return store_for(TABLE, ComparisonRecord, directory, dsn)
