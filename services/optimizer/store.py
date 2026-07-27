"""Optimizer view over the shared job store."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.db import JobTable, store_for
from shared.store import FileJobStore, JobRecord


@dataclass
class PlanRecord(JobRecord):
    """One rewrite plan. `subject` is the crawl it was built from.

    A plan is only true of the pages as they were when they were crawled, so
    the crawl is what identifies it — the same site optimised before and after
    a rewrite is two plans, and one that could not say which crawl it read
    could not be compared with anything.
    """

    @property
    def plan_id(self) -> str:
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
            "plan_id": self.job_id,
            "crawl_id": self.subject,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "pages_with_fixes": report.get("pages_with_fixes"),
            "fixes": report.get("fixes"),
            "written_by": report.get("written_by"),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.summary(), "report": self.result}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PlanRecord:
        return cls(
            job_id=raw.get("job_id") or raw["plan_id"],
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
    name="optimizer_plans",
    subject_column="crawl_id",
    derived={
        "pages_with_fixes": lambda report: report.get("pages_with_fixes"),
        "fix_count": lambda report: report.get("fixes"),
        "written_by": lambda report: report.get("written_by"),
    },
)


class PlanStore(FileJobStore[PlanRecord]):
    def __init__(self, directory: Path | str):
        super().__init__(directory, PlanRecord)


def open_store(directory: Path | str, dsn: str | None = None):
    """Postgres when configured, files otherwise. See `shared.db.store_for`."""
    return store_for(TABLE, PlanRecord, directory, dsn)
