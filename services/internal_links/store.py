"""Link-analysis view over the shared job store."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.db import JobTable, store_for
from shared.store import FileJobStore, JobRecord


@dataclass
class AnalysisRecord(JobRecord):
    """One link analysis. `subject` is the crawl it was run against.

    The subject is a crawl id rather than a URL because that is what the
    analysis is *of*: the same site crawled twice is two graphs, and a report
    that cannot say which crawl it describes cannot be compared with anything.
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
            "pages": report.get("pages"),
            "internal_links": report.get("internal_links"),
            "orphans": len(report.get("orphans") or []),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.summary(), "report": self.result}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AnalysisRecord:
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
    name="link_analyses",
    subject_column="crawl_id",
    derived={
        "pages": lambda report: report.get("pages"),
        "internal_links": lambda report: report.get("internal_links"),
        "orphan_count": lambda report: len(report.get("orphans") or []),
    },
)


class AnalysisStore(FileJobStore[AnalysisRecord]):
    def __init__(self, directory: Path | str):
        super().__init__(directory, AnalysisRecord)


def open_store(directory: Path | str, dsn: str | None = None):
    """Postgres when configured, files otherwise. See `shared.db.store_for`."""
    return store_for(TABLE, AnalysisRecord, directory, dsn)
